import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from film_foundry.app.api import (
    WORKFLOW_ERROR_CODES,
    WorkflowError,
    WorkflowRequest,
    run_request,
)
from film_foundry.model.config import DarkroomConfig


def test_run_request_rejects_unknown_command(tmp_path):
    with pytest.raises(WorkflowError, match="Unsupported workflow command") as caught:
        run_request(
            WorkflowRequest(
                command="print",
                input_path=tmp_path / "in.png",
                output_path=tmp_path / "out.png",
                config=DarkroomConfig(),
            )
        )
    assert caught.value.code == "unsupported_command"


def test_run_request_full_writes_output(tmp_path):
    image = np.linspace(0.1, 0.9, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    config.scanner.scan_normalize = False
    output = tmp_path / "out.png"
    result = run_request(
        WorkflowRequest(
            command="full",
            input_path=source,
            output_path=output,
            config=config,
        )
    )
    assert result.output_path == output
    assert output.exists()


def test_run_request_scan_exposes_color_response(tmp_path):
    density = np.linspace(0.2, 1.4, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    path = tmp_path / "medium.npz"
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=json.dumps({
            "medium_family": "silver_halide_film",
            "medium_process": "negative",
            "image_polarity": "negative",
            "view_mode": "transmission",
            "base_type": "orange_mask",
            "color_system": "dye",
        }),
    )
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    config.scanner.color_curve_rgb = (
        ((0.0, 0.1), (1.0, 1.0)),
        ((0.0, 0.1), (1.0, 1.0)),
        ((0.0, 0.1), (1.0, 1.0)),
    )
    result = run_request(
        WorkflowRequest(
            command="scan",
            input_path=path,
            output_path=tmp_path / "unused.png",
            config=config,
        )
    )
    scanned = result.extra["scanned"]
    assert result.source_kind == "density_npz"
    assert scanned.metadata["requested_interpretation"] == "auto"
    assert scanned.metadata["scanner_color_response"]["curve"] == "piecewise_rgb_v1"
    assert scanned.metadata["scanner_color_response"]["device"] == "none"


def test_run_request_scan_exposes_device_response(tmp_path):
    density = np.linspace(0.2, 1.4, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    path = tmp_path / "medium.npz"
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=json.dumps({
            "medium_family": "silver_halide_film",
            "medium_process": "negative",
            "image_polarity": "negative",
            "view_mode": "transmission",
            "base_type": "orange_mask",
            "color_system": "dye",
        }),
    )
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    config.scanner.device_response_rgb = (
        ((0.0, 0.0), (1.0, 0.8)),
        ((0.0, 0.0), (1.0, 0.8)),
        ((0.0, 0.0), (1.0, 0.8)),
    )
    result = run_request(
        WorkflowRequest(
            command="scan",
            input_path=path,
            output_path=tmp_path / "unused.png",
            config=config,
        )
    )
    scanned = result.extra["scanned"]
    assert scanned.metadata["scanner_color_response"]["device"] == "piecewise_rgb_v1"
    assert scanned.metadata["scanner_color_response"]["curve"] == "none"


def test_run_request_classifies_missing_input(tmp_path):
    missing = tmp_path / "missing.png"
    with pytest.raises(WorkflowError) as caught:
        run_request(
            WorkflowRequest(
                command="full",
                input_path=missing,
                output_path=tmp_path / "out.png",
                config=DarkroomConfig(),
            )
        )
    assert caught.value.code == "input_not_found"
    assert caught.value.path == missing
    assert "input_not_found" in WORKFLOW_ERROR_CODES


def test_run_request_develop_writes_medium(tmp_path):
    image = np.linspace(0.1, 0.9, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    output = tmp_path / "frame.darkroom_negative.npz"
    result = run_request(
        WorkflowRequest(
            command="develop",
            input_path=source,
            output_path=output,
            config=config,
        )
    )
    assert result.output_path == output
    assert output.exists()
    assert result.polarity == "negative"


def test_run_request_expose_then_develop_latent(tmp_path):
    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.seed_strategy = "fixed"
    config.random_seed = 31
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    latent = tmp_path / "frame.darkroom_latent.npz"
    developed = tmp_path / "frame.darkroom_negative.npz"

    exposed = run_request(
        WorkflowRequest(
            command="expose",
            input_path=source,
            output_path=latent,
            config=config,
        )
    )
    assert exposed.output_path == latent
    assert latent.exists()
    original_latent = latent.read_bytes()
    original_time = latent.stat().st_mtime_ns
    assert exposed.extra["frame_identity"] is None

    result = run_request(
        WorkflowRequest(
            command="develop_latent",
            input_path=latent,
            output_path=developed,
            config=config,
        )
    )
    assert result.output_path == developed
    assert developed.exists()
    assert result.polarity == "negative"
    medium = result.extra["medium"]
    assert medium.optical_density_rgb is not None
    assert medium.density_cmy.size > 0
    assert result.extra["frame_identity"] is None
    assert "frame_identity" not in medium.metadata

    again = run_request(WorkflowRequest(
        command="develop_latent", input_path=latent,
        output_path=tmp_path / "again.darkroom_negative.npz", config=config,
    ))
    assert again.output_path.exists()
    np.testing.assert_array_equal(medium.optical_density_rgb, again.extra["medium"].optical_density_rgb)
    assert latent.read_bytes() == original_latent
    assert latent.stat().st_mtime_ns == original_time


def test_run_request_rejects_invalid_frame_identity(tmp_path):
    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    with pytest.raises(WorkflowError) as caught:
        run_request(
            WorkflowRequest(
                command="expose",
                input_path=source,
                output_path=tmp_path / "frame.darkroom_latent.npz",
                config=config,
                frame_identity={"frame_id": "A12"},
            )
        )
    assert caught.value.code == "invalid_request"


def test_run_request_frame_identity_round_trips_and_scan_does_not_write_back(tmp_path):
    import shutil

    from film_foundry.core.latent_io import load_exposed_latent
    from film_foundry.core.negative_io import load_developed_medium_for_scan

    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    config.scanner.scan_normalize = False
    identity = {"roll_id": "roll-a", "frame_id": "A12", "frame_index": 12}
    latent = tmp_path / "frame.darkroom_latent.npz"
    developed = tmp_path / "frame.darkroom_negative.npz"

    exposed = run_request(
        WorkflowRequest(
            command="expose",
            input_path=source,
            output_path=latent,
            config=config,
            frame_identity=identity,
        )
    )
    assert exposed.extra["frame_identity"] == identity
    _state, latent_meta = load_exposed_latent(latent)
    assert latent_meta["frame_identity"] == identity

    latent_copy = tmp_path / "copy.darkroom_latent.npz"
    shutil.copy2(latent, latent_copy)

    result = run_request(
        WorkflowRequest(
            command="develop_latent",
            input_path=latent,
            output_path=developed,
            config=config,
        )
    )
    assert result.extra["frame_identity"] == identity
    medium = result.extra["medium"]
    identity_before = dict(medium.metadata["frame_identity"])
    optical_before = np.array(medium.optical_density_rgb, copy=True)
    loaded = load_developed_medium_for_scan(developed)
    assert loaded.metadata["frame_identity"] == identity
    with np.load(developed, allow_pickle=False) as data:
        payload = json.loads(str(data["developed_medium_metadata"].item()))
    assert payload["frame_identity"] == identity

    scanned_result = run_request(
        WorkflowRequest(
            command="scan",
            input_path=developed,
            output_path=tmp_path / "unused.png",
            config=config,
        )
    )
    assert scanned_result.extra["frame_identity"] == identity
    scanned = scanned_result.extra["scanned"]
    assert scanned.metadata["frame_identity"] == identity
    scanned.metadata["frame_identity"]["roll_id"] = "mutated"
    assert medium.metadata["frame_identity"] == identity_before
    np.testing.assert_array_equal(medium.optical_density_rgb, optical_before)
    reloaded = load_developed_medium_for_scan(developed)
    assert reloaded.metadata["frame_identity"] == identity

    override = {"roll_id": "roll-b", "frame_index": 1}
    overridden = run_request(
        WorkflowRequest(
            command="develop_latent",
            input_path=latent_copy,
            output_path=tmp_path / "override.darkroom_negative.npz",
            config=config,
            frame_identity=override,
        )
    )
    assert overridden.extra["frame_identity"] == override


def test_run_request_cancel_before_start_writes_nothing(tmp_path):
    from film_foundry.app.control import CancelFlag, WorkflowControl

    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    latent = tmp_path / "frame.darkroom_latent.npz"
    flag = CancelFlag()
    flag.cancel()
    stages: list[str] = []
    with pytest.raises(WorkflowError) as caught:
        run_request(
            WorkflowRequest(
                command="expose",
                input_path=source,
                output_path=latent,
                config=DarkroomConfig(),
                control=WorkflowControl(
                    cancel_requested=flag.requested,
                    on_progress=lambda progress: stages.append(progress.stage),
                ),
            )
        )
    assert caught.value.code == "cancelled"
    assert "cancelled" in WORKFLOW_ERROR_CODES
    assert not latent.exists()
    assert stages == []


def test_run_request_reports_expose_stages(tmp_path):
    from film_foundry.app.control import WorkflowControl

    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    stages: list[str] = []
    result = run_request(
        WorkflowRequest(
            command="expose",
            input_path=source,
            output_path=tmp_path / "frame.darkroom_latent.npz",
            config=config,
            control=WorkflowControl(
                on_progress=lambda progress: stages.append(progress.stage),
            ),
        )
    )
    assert result.output_path.exists()
    assert stages == ["starting", "loading", "exposing", "saving", "completed"]


def test_run_request_cancel_before_save_does_not_consume_latent(tmp_path):
    from film_foundry.app.control import CancelFlag, WorkflowControl
    from film_foundry.core.latent_io import load_exposed_latent

    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    latent = tmp_path / "frame.darkroom_latent.npz"
    developed = tmp_path / "frame.darkroom_negative.npz"
    run_request(
        WorkflowRequest(
            command="expose",
            input_path=source,
            output_path=latent,
            config=config,
        )
    )
    flag = CancelFlag()
    stages: list[str] = []

    def on_progress(progress) -> None:
        stages.append(progress.stage)
        if progress.stage == "saving":
            flag.cancel()

    with pytest.raises(WorkflowError) as caught:
        run_request(
            WorkflowRequest(
                command="develop_latent",
                input_path=latent,
                output_path=developed,
                config=config,
                control=WorkflowControl(
                    cancel_requested=flag.requested,
                    on_progress=on_progress,
                ),
            )
        )
    assert caught.value.code == "cancelled"
    assert not developed.exists()
    _state, metadata = load_exposed_latent(latent)
    assert metadata["consumed"] is False
    assert "saving" in stages
    assert "completed" not in stages
