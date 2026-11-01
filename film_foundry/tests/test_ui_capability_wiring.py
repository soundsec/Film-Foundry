"""Real UI-to-request paths for capabilities added after the original GUI."""

import copy
import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import numpy as np
import pytest
from PIL import Image

from film_foundry.app.api import WorkflowRequest, run_request
from film_foundry.app.config_assets import load_recipe_file
from film_foundry.app.material_batch import run_material_batch
from film_foundry.app.replay_batch import load_resume_plan, run_resume_batch
from film_foundry.app.workflow import output_path_for, latent_path_for
from film_foundry.core.engine import develop_negative, scan_negative
from film_foundry.core.latent_io import load_exposed_latent, mark_exposed_latent_consumed
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.model.config import DarkroomConfig
from film_foundry.model.session import session_payload, save_session, load_session


def config():
    cfg = DarkroomConfig(enable_grain=False, enable_halation=False, enable_mtf=False)
    cfg.seed_strategy = "fixed"
    cfg.random_seed = 71
    cfg.output.save_scanner_raw = False
    cfg.output.export_transparent_plate = False
    cfg.save_sidecar = False
    cfg.output.anti_banding_strength = 0
    cfg.chemistry.program_key = "color_negative"
    return cfg


def images(root):
    root.mkdir()
    image = np.linspace(15, 230, 12*18*3).astype(np.uint8).reshape(12, 18, 3)
    for name in ("a", "b"):
        Image.fromarray(image).save(root / (name + ".png"))
    return root


def click_apply(editor):
    from PySide6 import QtWidgets
    from film_foundry.ui.i18n import tr
    button = next(button for button in editor.root.qt.findChildren(QtWidgets.QPushButton)
                  if button.text() == tr("button.apply_current"))
    button.click()


def test_material_and_process_editors_apply_to_request_without_resetting_other_axes(tmp_path):
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel
    from film_foundry.tests.test_desktop_capabilities import spectral_config
    from film_foundry.tools.run_develop_process_editor import light_leak_secondary_template_label
    panel = DarkroomPanel()
    cfg = config()
    cfg.film.spectral_exposure_profile = spectral_config().film.spectral_exposure_profile
    cfg.scanner.print_color_bias = (.93, 1.02, 1.04)
    panel._apply_loaded_config(cfg)
    panel.run_as_preview.set(True)
    panel.fast_mode.set(True)
    panel.quality_mode.set("draft")
    panel.scan_remove_base.set(False)
    try:
        panel._open_editor("film_foundry.tools.run_film_material_editor")
        material = panel._editor_windows["film"]
        material.grain_scales_field.value.set("0.001; 0.003; 0.009")
        material.grain_weights_field.value.set("0.6; 0.3; 0.1")
        material.scalar_vars["emulsion_highlight_spread_strength"].set(.12)
        material.matrix_vars["dir_material_response_matrix"][0][1].set(.14)
        material.vector_vars["hd_gamma"][0].set(.82)
        click_apply(material)
        actual = panel._config()
        assert actual.film.grain_scales == (.001, .003, .009)
        assert actual.film.dir_material_response_matrix[0][1] == .14
        assert actual.film.spectral_exposure_profile == cfg.film.spectral_exposure_profile
        assert actual.scanner.print_color_bias == cfg.scanner.print_color_bias
        assert panel.run_as_preview.get() and actual.fast_mode
        assert actual.processing.quality_mode == "draft"
        assert not actual.scanner.remove_base_mask and actual.scanner.invert_transmission
        panel._open_editor("film_foundry.tools.run_develop_process_editor")
        process = panel._editor_windows["develop"]
        process.program_key.set("color_reversal")
        process.developer_name.set("Custom Reversal Bath")
        process.dir_effectiveness_field.value.set("0.35")
        process.vars["second_development_completion"].set(.72)
        process.vars["light_leak_strength"].set(.15)
        process.light_leak_secondary_template_display.set(light_leak_secondary_template_label("cone"))
        process.light_leak_identity_seed.set("123")
        click_apply(process)
        actual = panel._config()
        assert actual.chemistry.program_key == "color_reversal"
        assert actual.chemistry.developer_name == "Custom Reversal Bath"
        assert actual.chemistry.dir_process_effectiveness == .35
        assert actual.chemistry.second_development_completion == .72
        assert actual.chemistry.light_leak_secondary_template == "cone"
        assert actual.chemistry.light_leak_identity_seed == 123
        assert panel._expected_scan_polarity() == "positive"
        assert actual.film.dir_material_response_matrix[0][1] == .14
        source = tmp_path / "frame.png"
        Image.fromarray(np.full((12, 18, 3), 120, np.uint8)).save(source)
        result = run_request(WorkflowRequest("develop", source, tmp_path/"negative", actual))
        assert result.polarity == "positive"
        medium = load_developed_medium_for_scan(result.output_path)
        assert medium.image_polarity == "positive"
        material.root.qt.close()
        assert not material.root.winfo_exists() and panel.root.winfo_exists()
        panel._open_editor("film_foundry.tools.run_film_material_editor")
        assert panel._editor_windows["film"] is not material
        assert panel._editor_windows["film"]._config_from_controls().film.grain_scales == (.001, .003, .009)
    finally:
        panel.root.destroy()


def test_scanner_curves_change_capture_and_output_and_leave_medium_unchanged(tmp_path):
    from film_foundry.tools.run_darkroom_gui import DarkroomPanel
    from film_foundry.app.config_assets import load_curve_file
    panel = DarkroomPanel()
    panel._apply_loaded_config(config())
    image = np.linspace(.05, .85, 12*18*3, dtype=np.float32).reshape(12, 18, 3)
    try:
        panel._open_scanner_editor()
        scanner = panel._editor_windows["scanner"]
        baseline = panel._config()
        medium = develop_negative(image, baseline, rng=np.random.default_rng(71))
        original = medium.optical_density_rgb.copy()
        plain = scan_negative(medium, baseline)
        for key in ("color_curve_rgb", "device_response_rgb"):
            field = scanner.curve_fields[key]
            field.enabled.set(True)
            for value in field.values:
                value.set("0:0.08; 0.5:0.7; 1:1")
        scanner.vector_vars["highlight_color_bias"][0].set(1.08)
        click_apply(scanner)
        actual = panel._config()
        changed = scan_negative(medium, actual)
        assert not np.array_equal(plain.scanner_raw, changed.scanner_raw)
        assert not np.array_equal(plain.output_srgb, changed.output_srgb)
        np.testing.assert_array_equal(original, medium.optical_density_rgb)
        assert actual.scanner.color_curve_rgb[0][1] == (.5, .7)
        assert actual.scanner.highlight_color_bias[0] == 1.08
        path = tmp_path/"curve.json"
        path.write_text(json.dumps({"device_response_rgb": actual.scanner.device_response_rgb}), encoding="utf-8")
        assert load_curve_file(path, "device_response_rgb") == actual.scanner.device_response_rgb
        with pytest.raises(ValueError, match="has no scanner.color_curve_rgb"):
            load_curve_file(path, "color_curve_rgb")
        snapshot = copy.deepcopy(actual.scanner)
        scanner.curve_fields["color_curve_rgb"].values[0].set("0:0; 0:1")
        click_apply(scanner)
        assert panel._config().scanner == snapshot
        assert "x values" in scanner.status.get()
    finally:
        panel.root.destroy()


def test_runtime_export_session_and_frame_identity_reach_real_archive(tmp_path, monkeypatch):
    from film_foundry.tools import run_darkroom_gui as gui
    panel = gui.DarkroomPanel()
    panel._apply_loaded_config(config())
    try:
        panel._open_runtime_settings()
        runtime = panel._editor_windows["runtime"]
        runtime.values["input_icc_policy"].set("ignore")
        runtime.values["native_thread_limit"].set("2")
        runtime.values["memory_budget_mb"].set("512")
        runtime.values["medium_npz_compression"].set("store")
        runtime.values["bit_depth"].set("16")
        runtime.values["render_long_edge"].set("12")
        runtime.values["look_strength"].set("0.75")
        runtime.identity_values["roll_id"].set("roll-ui")
        runtime.identity_values["frame_index"].set("7")
        # Main controls changed while settings were open must survive Apply.
        panel.run_as_preview.set(False)
        panel.fast_mode.set(True)
        panel.quality_mode.set("high")
        panel.output_format.set("tiff")
        click_apply(runtime)
        actual = panel._config()
        assert actual.output.format == "tiff" and actual.output.bit_depth == 16
        assert actual.processing.quality_mode == "high" and actual.fast_mode
        assert not panel.run_as_preview.get()
        assert actual.processing.native_thread_limit == 2
        assert actual.input_icc_policy == "ignore" and actual.look.look_strength == .75
        source = images(tmp_path/"images")
        panel.input_path.set(str(source))
        panel.pipeline_mode.set("expose")
        snapshot = panel._session_payload()
        for suffix in (".json", ".ffc2"):
            path = tmp_path/("recipe" + suffix)
            monkeypatch.setattr(gui.file_dialog, "asksaveasfilename", lambda **kwargs: str(path))
            panel._export_recipe()
            exported = load_recipe_file(path)
            assert exported.look.look_strength == .75
            if suffix == ".json":
                assert exported.processing.native_thread_limit == 2
                assert exported.output.medium_npz_compression == "store"
        session = tmp_path/"session.json"
        save_session(session, snapshot)
        panel._frame_identity = None
        monkeypatch.setattr(gui.file_dialog, "askopenfilename", lambda **kwargs: str(session))
        panel._load_session()
        assert panel._frame_identity == {"roll_id": "roll-ui", "frame_index": 7}
        result = run_material_batch("expose", source, tmp_path/"output", actual, settings=snapshot)
        assert result.completed == 2 and not result.failures
        for index, path in enumerate(sorted(result.medium_root.glob("*.npz")), 7):
            state, metadata = load_exposed_latent(path)
            assert metadata["frame_identity"] == {"roll_id": "roll-ui", "frame_index": index}
            assert state.halide.shape[:2] == (8, 12)
        assert load_session(result.run_root/"film_foundry_run.json")["run"]["inputs"] == [str(source/"a.png"), str(source/"b.png")]
    finally:
        panel.root.destroy()


def make_run(tmp_path, command):
    cfg = config()
    source = images(tmp_path/"images")
    if command == "develop_latent":
        source = run_material_batch("expose", source, tmp_path/"exposed", cfg).medium_root
    elif command == "scan":
        target = tmp_path/"developed"
        for path in sorted(source.glob("*.png")):
            run_request(WorkflowRequest("develop", path, target, cfg))
        source = target
    run_root = tmp_path/"run"
    run_root.mkdir()
    output_root = run_root/"negative" if command in {"develop", "develop_latent"} else run_root/"latent" if command == "expose" else run_root
    inputs = sorted(source.glob("*.npz" if command in {"scan", "develop_latent"} else "*.png"))
    payload = session_payload(config=cfg, pipeline_mode=command, input_path=str(source), negative_path=str(source), output_path=str(run_root), presets={})
    payload["run"] = {"id": "test_run", "directory": str(run_root), "actual_output": str(output_root), "inputs": [str(path) for path in inputs], "status": "running"}
    manifest = run_root/"film_foundry_run.json"
    save_session(manifest, payload)
    return load_resume_plan(manifest)


@pytest.mark.parametrize("command", ("full", "develop", "expose", "develop_latent", "scan"))
def test_resume_five_stages_skip_complete_outputs_and_freeze_original_inputs(tmp_path, command):
    from dataclasses import replace
    plan = make_run(tmp_path, command)
    first = run_resume_batch(replace(plan, items=plan.items[:1]))
    assert first.completed == 1 and not first.failures
    # Restore the originally frozen two-item plan after a simulated interruption.
    save_session(plan.manifest, plan.settings)
    first_output = load_resume_plan(plan.manifest).items[0].output_path
    first_time = first_output.stat().st_mtime_ns
    plan.items[1].output_path.write_bytes(b"")
    result = run_resume_batch(load_resume_plan(plan.manifest))
    assert result.completed == 1 and result.reused == 1 and not result.failures
    assert not result.record_error
    assert first_output.stat().st_mtime_ns == first_time
    payload = load_session(plan.manifest)
    assert payload["run"]["status"] == "completed"
    assert payload["batch"]["completed"] == 2 and payload["batch"]["reused"] == 1
    for item in payload["batch"]["items"]:
        assert Path(item["output"]).is_file()
    if command == "develop_latent":
        for item in plan.items:
            assert not load_exposed_latent(item.input_path)[1]["consumed"]


def test_resume_cancel_does_not_start_remaining_input_and_rejects_plain_session(tmp_path, monkeypatch):
    from film_foundry.app import replay_batch
    from film_foundry.app.control import CancelFlag, WorkflowControl
    plan = make_run(tmp_path, "expose")
    flag = CancelFlag()
    real = replay_batch.run_request
    started = []
    def request(value):
        started.append(value.input_path)
        result = real(value)
        flag.cancel()
        return result
    monkeypatch.setattr(replay_batch, "run_request", request)
    result = run_resume_batch(plan, control=WorkflowControl(cancel_requested=flag.requested))
    assert result.cancelled and result.completed == 1 and len(started) == 1
    assert load_session(plan.manifest)["run"]["status"] == "cancelled"
    plain = tmp_path/"plain.json"
    payload = copy.deepcopy(plan.settings)
    payload.pop("run")
    save_session(plain, payload)
    with pytest.raises(ValueError, match="batch run record"):
        load_resume_plan(plain)


def test_resume_does_not_reactivate_consumed_latent_after_result_is_lost(tmp_path):
    from dataclasses import replace
    plan = make_run(tmp_path, "develop_latent")
    first = run_resume_batch(replace(plan, items=plan.items[:1]))
    assert first.completed == 1
    # Simulate an archive written by the old, consuming desktop workflow.
    mark_exposed_latent_consumed(plan.items[0].input_path)
    reused = run_resume_batch(replace(plan, items=plan.items[:1]))
    assert reused.reused == 1 and reused.completed == 0 and not reused.failures
    plan.items[0].output_path.unlink()
    save_session(plan.manifest, plan.settings)
    result = run_resume_batch(load_resume_plan(plan.manifest))
    assert result.completed == 1 and result.reused == 0 and len(result.failures) == 1
    assert load_exposed_latent(plan.items[0].input_path, allow_consumed=True)[1]["consumed"]
    assert load_session(plan.manifest)["batch"]["status"] == "completed_with_errors"


def test_resume_regenerates_missing_result_from_preserved_latent(tmp_path):
    from dataclasses import replace
    plan = make_run(tmp_path, "develop_latent")
    original = {item.input_path: item.input_path.read_bytes() for item in plan.items}
    first = run_resume_batch(replace(plan, items=plan.items[:1]))
    assert first.completed == 1 and not first.failures
    plan.items[0].output_path.unlink()
    save_session(plan.manifest, plan.settings)
    result = run_resume_batch(load_resume_plan(plan.manifest))
    assert result.completed == 2 and result.reused == 0 and not result.failures
    assert load_session(plan.manifest)["batch"]["status"] == "completed"
    for path, data in original.items():
        assert path.read_bytes() == data
        assert not load_exposed_latent(path)[1]["consumed"]


def test_gui_resume_uses_recorded_recipe_and_finishes_thread_state(tmp_path, monkeypatch):
    from film_foundry.tools import run_darkroom_gui as gui
    from PySide6 import QtWidgets
    plan = make_run(tmp_path, "full")
    panel = gui.DarkroomPanel()
    requests = []
    class DeferredThread:
        def __init__(self, **kwargs):
            requests.append(kwargs)
        def start(self):
            pass
    try:
        panel.exposure_ev.set(2)
        monkeypatch.setattr(gui, "Thread", DeferredThread)
        monkeypatch.setattr(gui.file_dialog, "askopenfilename", lambda **kwargs: str(plan.manifest))
        panel._start_resume()
        assert panel._processing
        frozen, flag = requests[0]["args"]
        assert frozen.config.look.exposure_ev == plan.config.look.exposure_ev
        assert panel.exposure_ev.get() == 2
        requests[0]["target"](frozen, flag)
        QtWidgets.QApplication.processEvents()
        assert not panel._processing
        assert load_session(plan.manifest)["batch"]["completed"] == 2
    finally:
        panel.root.destroy()
