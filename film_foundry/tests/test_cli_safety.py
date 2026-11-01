from pathlib import Path
from types import SimpleNamespace
import ast
import json

import numpy as np
import pytest

import film_foundry.app.cli as cli
from film_foundry.app.cli import _apply_common_config, _developed_path_for, _ensure_batch_output_target, _iter_negative_files, _output_path_for, _scan_from_file, build_parser
from film_foundry.app import workflow
from film_foundry.core.electronic_negative import save_linear_rgb_tiff
from film_foundry.core.execution import resolve_execution_axes
from film_foundry.model.config import DarkroomConfig


def test_cli_shares_medium_path_helpers_with_workflow():
    assert cli._developed_path_for is workflow.developed_path_for
    assert cli._iter_negative_files is workflow.iter_developed_medium_files
    assert cli._raw_path_for_medium is workflow.transmission_raw_path_for_medium
    assert cli._rng_and_resolved_seed_for_develop is workflow.rng_and_resolved_seed_for_develop
    assert cli._output_path_for is not workflow.output_path_for


def test_workflow_module_does_not_import_ui():
    source = Path(workflow.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        name == "film_foundry.ui" or name.startswith("film_foundry.ui.")
        for name in imported
    )


def test_cli_full_uses_workflow_process_image_file():
    import inspect
    import film_foundry.app.api as api

    assert cli._process_image_file is workflow.process_image_file
    assert cli._run_request is api.run_request
    assert "_run_request" in inspect.getsource(cli._run_develop)
    assert "_run_request" in inspect.getsource(cli._run_expose)
    assert "_run_request" in inspect.getsource(cli._run_develop_latent)
    assert "_run_request" in inspect.getsource(cli._scan_from_file)
    assert "develop_image_file" not in inspect.getsource(cli._run_develop)
    assert "develop_latent_file" not in inspect.getsource(cli._run_develop_latent)
    assert "expose_image_file" not in inspect.getsource(cli._run_expose)


def test_app_api_module_does_not_import_ui():
    import film_foundry.app.api as api

    source = Path(api.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        name == "film_foundry.ui" or name.startswith("film_foundry.ui.")
        for name in imported
    )


def test_workflow_preset_records_are_untranslated():
    record = workflow.film_preset_record("vivid_balanced_negative")
    assert record is not None
    assert "label" not in record
    assert record["name"]
    assert record["polarity"] == "negative"


@pytest.mark.parametrize("polarity", ["negative", "positive"])
@pytest.mark.parametrize("optical_master", [False, True])
def test_npz_inspector_explicitly_retains_scan_stages(tmp_path, polarity, optical_master):
    from film_foundry.core.states import ScanOutput, ScannedPositive

    path = tmp_path / "medium.npz"
    density = np.linspace(0.1, 1.0, 5 * 7 * 3, dtype=np.float32).reshape(5, 7, 3)
    np.savez_compressed(path, density_cmy=density, density_grain=density,
        developed_medium_metadata=json.dumps({
            "medium_family": "silver_halide_film",
            "medium_process": "slide" if polarity == "positive" else "negative",
            "image_polarity": polarity, "view_mode": "transmission",
            "base_type": "clear" if polarity == "positive" else "orange_mask",
            "color_system": "dye",
        }), **({"optical_density_rgb": density} if optical_master else {}))
    config = DarkroomConfig()
    output, _, _ = workflow.scan_from_file(path, config)
    stages, kind, preview = workflow.scan_from_file(path, config, retain_stages=True)
    assert isinstance(output, ScanOutput)
    assert isinstance(stages, ScannedPositive)
    assert kind == "density_npz"
    assert preview.shape == density.shape
    for name in ("scanner_raw", "positive_raw", "negative_base_balanced", "negative_channel_reconstructed"):
        assert getattr(stages, name).shape == density.shape
    np.testing.assert_array_equal(output.output_srgb, stages.output_srgb)


def test_cli_and_gui_scan_share_one_observation_with_distinct_third_values(tmp_path):
    raw_path = tmp_path / "frame.darkroom_positive.light_table_raw.tiff"
    save_linear_rgb_tiff(np.full((5, 6, 3), 0.65, dtype=np.float32), raw_path)
    config = DarkroomConfig()
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.input_polarity = "positive"
    config.scanner.target_medium_process = "positive"
    config.scanner.scan_method = "positive_transparency"

    scanned, source_kind, source_path, preview = workflow.observe_medium_file(raw_path, config)
    cli_scanned, cli_kind, cli_path = _scan_from_file(raw_path, config)
    gui_scanned, gui_kind, gui_preview = workflow.scan_from_file(raw_path, config)

    assert source_kind == cli_kind == gui_kind == "light_table_raw_tiff"
    assert source_path == cli_path == raw_path
    assert gui_preview.shape == preview.shape
    assert scanned.interpreter_key == cli_scanned.interpreter_key == gui_scanned.interpreter_key


def test_batch_file_output_is_rejected():
    with pytest.raises(ValueError, match="must be a folder"):
        _ensure_batch_output_target([Path("a.jpg"), Path("b.jpg")], Path("out.jpg"), "Final image")


def test_single_file_output_is_allowed():
    _ensure_batch_output_target([Path("a.jpg")], Path("out.jpg"), "Final image")


def test_batch_folder_output_is_allowed():
    _ensure_batch_output_target([Path("a.jpg"), Path("b.jpg")], Path("outputs"), "Final image")


def test_batch_sources_with_same_stem_are_rejected_before_overwrite():
    with pytest.raises(ValueError, match="same output stem"):
        _ensure_batch_output_target(
            [Path("frame.jpg"), Path("frame.png")],
            Path("outputs"),
            "Final image",
        )


def test_existing_dotted_output_directory_is_not_treated_as_single_file(tmp_path):
    output = tmp_path / "outputs.v03"
    output.mkdir()

    _ensure_batch_output_target([Path("a.jpg"), Path("b.jpg")], output, "Final image")

    assert _output_path_for(Path("a.jpg"), output, "png") == output / "a_foundry.png"


def test_cli_develop_path_and_scan_discovery_support_positive_media(tmp_path):
    positive = SimpleNamespace(image_polarity="positive")
    positive_path = _developed_path_for(Path("frame.png"), tmp_path, positive)
    positive_path.touch()
    positive_path.with_suffix(".light_table_raw.tiff").touch()

    assert positive_path.name == "frame.darkroom_positive.npz"
    assert _iter_negative_files(tmp_path) == [positive_path]


def test_cli_scan_outputs_preserve_medium_polarity_and_do_not_collide(tmp_path):
    negative = Path("frame.darkroom_negative.npz")
    positive = Path("frame.darkroom_positive.npz")

    assert _output_path_for(negative, tmp_path, "png").name == "frame_negative_foundry.png"
    assert _output_path_for(positive, tmp_path, "png").name == "frame_positive_foundry.png"
    assert _output_path_for(negative, tmp_path, "png") != _output_path_for(positive, tmp_path, "png")


def test_cli_exposes_scanner_color_curve_json(tmp_path):
    curve = tmp_path / "curve.json"
    curve.write_text(
        "[[[0, 0.1], [1, 1]], [[0, 0.1], [1, 1]], [[0, 0.1], [1, 1]]]",
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "scan",
        "medium.npz",
        "output",
        "--scanner-color-curve",
        str(curve),
    ])

    config = _apply_common_config(args)

    assert config.scanner.color_curve_rgb[0][0] == (0.0, 0.1)
    assert config.scanner.color_curve_rgb[2][-1] == (1.0, 1.0)


def test_cli_exposes_scanner_device_response_json(tmp_path):
    curve = tmp_path / "device.json"
    curve.write_text(
        "[[[0, 0.0], [1, 0.8]], [[0, 0.0], [1, 0.8]], [[0, 0.0], [1, 0.8]]]",
        encoding="utf-8",
    )
    args = build_parser().parse_args([
        "scan",
        "medium.npz",
        "output",
        "--scanner-device-response",
        str(curve),
    ])

    config = _apply_common_config(args)

    assert config.scanner.device_response_rgb[0][-1] == (1.0, 0.8)
    assert config.scanner.device_response_rgb[2][0] == (0.0, 0.0)


def test_cli_exposes_explicit_scan_interpretation_override():
    args = build_parser().parse_args([
        "scan",
        "medium.npz",
        "output",
        "--interpretation",
        "positive",
    ])

    config = _apply_common_config(args)

    assert config.scanner.interpretation_mode == "positive"
    assert config.scanner.interpreter_key == "positive_transparency_scan"


def test_cli_exposes_input_icc_policy_override():
    args = build_parser().parse_args([
        "full",
        "input.png",
        "output.png",
        "--input-icc",
        "ignore",
    ])

    config = _apply_common_config(args)

    assert config.input_icc_policy == "ignore"


def test_cli_resize_long_edge_alias_preserves_legacy_session_field():
    modern = build_parser().parse_args([
        "full",
        "input.png",
        "output.png",
        "--resize-long-edge",
        "1440",
    ])
    legacy = build_parser().parse_args([
        "full",
        "input.png",
        "output.png",
        "--preview-long-edge",
        "1440",
    ])

    assert modern.preview_long_edge == legacy.preview_long_edge == 1440


def test_cli_scaling_fast_and_quality_tier_can_be_combined():
    args = build_parser().parse_args(
        [
            "full",
            "input.png",
            "output.png",
            "--processing-mode",
            "scaled_fast",
            "--fast",
            "--quality-mode",
            "draft",
        ]
    )

    config = _apply_common_config(args)
    axes = resolve_execution_axes(config)

    assert axes.scaling_enabled is True
    assert axes.lower_order_enabled is True
    assert axes.quality_mode == "draft"
    assert axes.label == "scaled_reduced_fast"


def test_cli_light_table_raw_uses_positive_direct_interpretation(tmp_path):
    raw_path = tmp_path / "frame.darkroom_positive.light_table_raw.tiff"
    save_linear_rgb_tiff(np.full((5, 6, 3), 0.65, dtype=np.float32), raw_path)
    config = DarkroomConfig()
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.input_polarity = "positive"
    config.scanner.target_medium_process = "positive"
    config.scanner.scan_method = "positive_transparency"

    scanned, source_kind, source_path = _scan_from_file(raw_path, config)

    assert source_kind == "light_table_raw_tiff"
    assert source_path == raw_path
    assert scanned.interpreter_key == "positive_transparency_scan"
    assert scanned.metadata["requested_interpretation"] == "auto"
    assert scanned.metadata["resolved_interpretation"] == "positive"


def test_cli_positive_interpretation_preserves_physical_negative_raw_kind_and_crop(tmp_path):
    raw_path = tmp_path / "frame.darkroom_negative.scanner_raw.tiff"
    save_linear_rgb_tiff(np.full((8, 10, 3), 0.55, dtype=np.float32), raw_path)
    raw_path.with_suffix(raw_path.suffix + ".json").write_text(
        json.dumps({
            "kind": "ScannerRawNegative",
            "border": {"present": True, "percent": 0.1, "min_px": 1, "width_px": 1},
        }),
        encoding="utf-8",
    )
    config = DarkroomConfig()
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.interpretation_mode = "positive"

    scanned, source_kind, source_path = _scan_from_file(raw_path, config)

    assert source_kind == "scanner_raw_tiff"
    assert source_path == raw_path
    assert scanned.metadata["scan_source"] == "scanner_raw_tiff"
    assert scanned.scanner_raw.shape[:2] == (6, 8)


def test_cli_npz_auto_interpretation_follows_positive_medium_metadata(tmp_path):
    medium_path = tmp_path / "frame.darkroom_positive.npz"
    density = np.full((5, 7, 3), 0.3, dtype=np.float32)
    np.savez_compressed(
        medium_path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=json.dumps({
            "medium_family": "silver_halide_film",
            "medium_process": "slide",
            "image_polarity": "positive",
            "view_mode": "transmission",
            "base_type": "clear",
            "color_system": "dye",
            "compatible_interpreters": ["positive_transparency_scan"],
        }),
    )

    scanned, source_kind, source_path = _scan_from_file(medium_path, DarkroomConfig())

    assert source_kind == "density_npz"
    assert source_path == medium_path
    assert scanned.input_polarity == "positive"
    assert scanned.interpreter_key == "positive_transparency_scan"
    assert scanned.metadata["requested_interpretation"] == "auto"
    assert scanned.metadata["resolved_interpretation"] == "positive"


def test_cli_scan_batch_attempts_later_files_after_one_failure(tmp_path, monkeypatch):
    source = tmp_path / "medium"
    source.mkdir()
    bad = source / "a.darkroom_negative.npz"
    good = source / "b.darkroom_negative.npz"
    bad.touch()
    good.touch()
    attempted: list[Path] = []
    saved: list[Path] = []

    def fake_scan(path, _config, *, control=None):
        attempted.append(path)
        if path == bad:
            raise ValueError("corrupt medium")
        scanned = SimpleNamespace(
            output_srgb=np.zeros((2, 3, 3), dtype=np.float32),
            metadata={},
        )
        return scanned, "density_npz", path

    monkeypatch.setattr(cli, "_scan_from_file", fake_scan)
    monkeypatch.setattr(
        cli,
        "save_image_bundle",
        lambda _image, path, _output, _sidecar=None, **_kwargs: saved.append(path),
    )
    config = DarkroomConfig()
    config.save_sidecar = False
    args = SimpleNamespace(
        scanner_raw_border=None,
        scanner_raw_border_min_px=None,
        negative=source,
        output=tmp_path / "output",
    )

    with pytest.raises(RuntimeError, match="1 failure"):
        cli._run_scan(args, config)

    assert attempted == [bad, good]
    assert len(saved) == 1


def test_cli_help_uses_display_name_not_legacy_product_pair():
    from film_foundry.identity import APP_DISPLAY_NAME

    parser = build_parser()
    assert parser.description == f"{APP_DISPLAY_NAME} command-line workflow."
    assert "Electronic Negative Factory" not in parser.description


def test_cli_parser_includes_expose_and_develop_latent():
    parser = build_parser()
    expose = parser.parse_args(["expose", "in.png", "out.darkroom_latent.npz"])
    assert expose.command == "expose"
    latent = parser.parse_args(
        ["develop_latent", "in.darkroom_latent.npz", "out.darkroom_negative.npz"]
    )
    assert latent.command == "develop_latent"
    share = parser.parse_args(
        [
            "share-config",
            "decode",
            "FFC2:abc",
        ]
    )
    assert share.command == "share-config"
    assert share.share_action == "decode"
    encode = parser.parse_args(
        [
            "share-config",
            "encode",
            "--exposure-ev",
            "-0.25",
        ]
    )
    assert encode.share_action == "encode"
    assert encode.exposure_ev == pytest.approx(-0.25)


def test_cli_parser_accepts_frame_identity_flags():
    parser = build_parser()
    expose = parser.parse_args(
        [
            "expose",
            "in.png",
            "out.darkroom_latent.npz",
            "--roll-id",
            "R1",
            "--frame-id",
            "A12",
            "--frame-index",
            "12",
        ]
    )
    assert expose.roll_id == "R1"
    assert expose.frame_id == "A12"
    assert expose.frame_index == 12
    develop = parser.parse_args(
        ["develop", "in.png", "out.darkroom_negative.npz", "--roll-id", "R1"]
    )
    assert develop.roll_id == "R1"
    assert develop.frame_id is None
    assert develop.frame_index is None
    scan = parser.parse_args(["scan", "in.npz", "out.png"])
    assert not hasattr(scan, "roll_id")
    full = parser.parse_args(["full", "in.png", "out.png"])
    assert not hasattr(full, "roll_id")


def test_cli_frame_identity_batch_helper():
    batch_args = SimpleNamespace(roll_id="R", frame_id=None, frame_index=2)
    first = cli._frame_identity_for_cli_item(batch_args, item_index=0, item_count=3)
    second = cli._frame_identity_for_cli_item(batch_args, item_index=1, item_count=3)
    assert first == {"roll_id": "R", "frame_index": 2, "frame_id": "R:2"}
    assert second == {"roll_id": "R", "frame_index": 3, "frame_id": "R:3"}
    single = cli._frame_identity_for_cli_item(
        SimpleNamespace(roll_id="R", frame_id="A12", frame_index=None),
        item_index=0,
        item_count=1,
    )
    assert single == {"roll_id": "R", "frame_id": "A12"}
    omitted = cli._frame_identity_for_cli_item(
        SimpleNamespace(roll_id=None, frame_id=None, frame_index=None),
        item_index=0,
        item_count=1,
    )
    assert omitted is None
    with pytest.raises(ValueError, match="--roll-id"):
        cli._validate_cli_frame_identity(
            SimpleNamespace(roll_id=None, frame_id="A1", frame_index=None),
            1,
        )
    with pytest.raises(ValueError, match="--frame-id"):
        cli._validate_cli_frame_identity(
            SimpleNamespace(roll_id="R", frame_id="A1", frame_index=None),
            2,
        )


def test_cli_parser_accepts_progress_flag():
    parser = build_parser()
    expose = parser.parse_args(
        ["expose", "in.png", "out.darkroom_latent.npz", "--progress"]
    )
    assert expose.progress is True
    full = parser.parse_args(["full", "in.png", "out.png", "--progress"])
    assert full.progress is True
    scan = parser.parse_args(["scan", "in.npz", "out.png", "--progress"])
    assert scan.progress is True
    quiet = parser.parse_args(["expose", "in.png", "out.darkroom_latent.npz"])
    assert quiet.progress is False


def _tiny_rgb_png(path: Path) -> Path:
    from PIL import Image

    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(path)
    return path


def test_cli_expose_batch_stops_after_pre_cancelled_flag(tmp_path, monkeypatch, capsys):
    from film_foundry.app.control import CancelFlag

    source = tmp_path / "in"
    source.mkdir()
    first = _tiny_rgb_png(source / "a.png")
    second = _tiny_rgb_png(source / "b.png")
    output = tmp_path / "latents"
    output.mkdir()
    flag = CancelFlag()
    flag.cancel()
    requested: list[Path] = []
    real_run = cli._run_request

    def wrapped(request):
        requested.append(request.input_path)
        return real_run(request)

    monkeypatch.setattr(cli, "_run_request", wrapped)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    args = SimpleNamespace(
        input=source,
        latent_output=output,
        preview=False,
        progress=False,
        cancel_flag=flag,
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        cli._run_expose(args, config)

    assert requested == [first]
    assert second.exists()
    assert list(output.glob("*.darkroom_latent.npz")) == []
    err = capsys.readouterr().err
    assert "1 remaining item(s) not started" in err


def test_cli_expose_progress_prints_stages(tmp_path, capsys):
    source = _tiny_rgb_png(tmp_path / "frame.png")
    output = tmp_path / "frame.darkroom_latent.npz"
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    args = SimpleNamespace(
        input=source,
        latent_output=output,
        preview=False,
        progress=True,
    )

    assert cli._run_expose(args, config) == 1
    assert output.exists()
    err = capsys.readouterr().err
    assert "[expose:starting]" in err
    assert "[expose:completed]" in err


def test_cli_sigint_helper_cancels_on_first_press():
    import signal

    from film_foundry.app.control import CancelFlag

    flag = CancelFlag()
    previous = signal.getsignal(signal.SIGINT)
    restore = cli._install_sigint_cancel(flag)
    try:
        handler = signal.getsignal(signal.SIGINT)
        handler(signal.SIGINT, None)
        assert flag.requested()
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    finally:
        restore()
        assert signal.getsignal(signal.SIGINT) == previous


def test_cli_parser_accepts_resume_flag():
    parser = build_parser()
    expose = parser.parse_args(
        ["expose", "in.png", "out.darkroom_latent.npz", "--resume"]
    )
    assert expose.resume is True
    full = parser.parse_args(["full", "in.png", "out.png", "--resume"])
    assert full.resume is True
    scan = parser.parse_args(["scan", "in.npz", "out.png", "--resume"])
    assert scan.resume is True
    quiet = parser.parse_args(["expose", "in.png", "out.darkroom_latent.npz"])
    assert quiet.resume is False


def test_cli_expose_resume_skips_complete_and_retries_garbage(tmp_path, monkeypatch, capsys):
    from film_foundry.core.latent_io import load_exposed_latent

    source = tmp_path / "in"
    source.mkdir()
    first = _tiny_rgb_png(source / "a.png")
    second = _tiny_rgb_png(source / "b.png")
    output = tmp_path / "latents"
    output.mkdir()
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    assert cli._run_expose(
        SimpleNamespace(
            input=first,
            latent_output=output,
            preview=False,
            progress=False,
            resume=False,
        ),
        config,
    ) == 1
    complete = output / "a.darkroom_latent.npz"
    assert complete.exists()
    garbage = output / "b.darkroom_latent.npz"
    garbage.write_bytes(b"truncated")

    requested: list[Path] = []
    real_run = cli._run_request

    def wrapped(request):
        requested.append(request.input_path)
        return real_run(request)

    monkeypatch.setattr(cli, "_run_request", wrapped)
    count = cli._run_expose(
        SimpleNamespace(
            input=source,
            latent_output=output,
            preview=False,
            progress=False,
            resume=True,
        ),
        config,
    )
    assert count == 2
    assert requested == [second]
    captured = capsys.readouterr()
    assert "skip complete" in captured.out
    load_exposed_latent(complete)
    load_exposed_latent(output / "b.darkroom_latent.npz")
