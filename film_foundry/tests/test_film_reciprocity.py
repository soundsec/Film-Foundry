"""Explicit exposure clocks, shared latent formation, and immutable capture."""
from copy import deepcopy
from dataclasses import asdict, replace
import numpy as np
import pytest
from film_foundry.contracts.film_exposure import MODEL, validate_response, validate_duration
from film_foundry.core.film_process.reciprocity import layer_efficiency, duration_for_loss
from film_foundry.core.engine import expose_linear_to_latent, develop_negative, develop_negative_from_latent
from film_foundry.model.config import DarkroomConfig


def config():
    c = DarkroomConfig(enable_grain=False, enable_halation=False, enable_mtf=False,
                       seed_strategy='fixed', random_seed=9)
    c.film.reciprocity_response = validate_response({'model': MODEL})
    c.output.save_scanner_raw = False; c.output.export_transparent_plate = False
    return c


def scene():
    return np.broadcast_to(np.linspace(.002, .8, 29, dtype=np.float32)[None, :, None], (19, 29, 3)).copy()


def test_layer_dose_and_linear_loss_target_precede_latent_curve():
    c = config(); c.exposure.duration_seconds = 64.
    image = scene(); before = image.copy()
    efficiency = layer_efficiency(c.film.reciprocity_response, 64., 3)
    np.testing.assert_allclose(efficiency, np.exp2(-6 * np.array([.15, .2, .25])), rtol=1e-7)
    timed = expose_linear_to_latent(image, c)
    plain = deepcopy(c); plain.exposure.duration_seconds = None
    # Reference forms activation from material-layer dose, not RGB tinting.
    from film_foundry.core.film_process.integration import _exposure_material
    from film_foundry.core.sensitometry import rgb_exposure_to_layer_exposure
    material = _exposure_material(c.film)
    reference = material.expose(rgb_exposure_to_layer_exposure(image, c.film) * efficiency)
    np.testing.assert_allclose(timed.developability, reference.developability, atol=1e-7)
    assert np.all(timed.developability <= expose_linear_to_latent(image, plain).developability)
    for key in ('halide', 'coupler', 'metallic_silver', 'dye'):
        np.testing.assert_array_equal(getattr(timed, key), getattr(reference, key))
    np.testing.assert_array_equal(image, before)
    for branch in ('long', 'short'):
        for loss in (0., .2, .5):
            duration = duration_for_loss(c.film.reciprocity_response, loss, branch=branch)
            eff = layer_efficiency(c.film.reciprocity_response, duration, 3)
            actual = 0. if eff is None else -np.log2(eff.astype(np.float64)).mean()
            assert actual == pytest.approx(loss, abs=1e-7)
    # For equal irradiance, longer exposure still increases effective dose.
    assert np.all(128 * layer_efficiency(c.film.reciprocity_response, 128, 3) > 64 * efficiency)
    assert layer_efficiency(c.film.reciprocity_response, .1, 3) is None
    assert layer_efficiency(c.film.reciprocity_response, None, 3) is None
    assert layer_efficiency(c.film.reciprocity_response, 64, 1)[0] == pytest.approx(2**(-1.2))
    for value in (0., -1., float('nan'), True, 86401.):
        with pytest.raises(ValueError): validate_duration(value)
    with pytest.raises(ValueError): validate_response({'model': MODEL, 'short_limit_seconds': 2.})
    with pytest.raises(ValueError): duration_for_loss({'model': MODEL, 'long_loss_per_stop': [0., 0., 0.]}, 1.)


def test_capture_survives_archive_ignores_develop_draft_and_clears_unused_slots(tmp_path):
    from film_foundry.core.latent_io import save_exposed_latent, load_exposed_latent, unexposed_latent_like
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    from film_foundry.core.engine import scan_medium_output
    from film_foundry.app.api import WorkflowRequest, run_request
    c = config(); c.exposure.duration_seconds = 32.
    latent = expose_linear_to_latent(scene(), c)
    archive = tmp_path / 'frame.darkroom_latent.npz'
    save_exposed_latent(archive, latent, identity={})
    loaded, metadata = load_exposed_latent(archive)
    assert metadata['exposure_conditions']['duration_seconds'] == 32.
    assert loaded.copy().exposure_duration_seconds == 32.
    blank = unexposed_latent_like(loaded)
    assert blank.exposure_duration_seconds is None and not np.any(blank.developability)
    other = expose_linear_to_latent(scene(), replace(c, exposure=replace(c.exposure, duration_seconds=.01)))
    assert other.material_snapshot == loaded.material_snapshot  # different frames can share a roll
    changed = deepcopy(c); changed.exposure.duration_seconds = 1e-8; changed.film.reciprocity_response = None
    first = develop_negative_from_latent(loaded.copy(), c)
    second = develop_negative_from_latent(loaded.copy(), changed)
    np.testing.assert_array_equal(first.optical_density_rgb, second.optical_density_rgb)
    record = second.metadata['film_process_model']['exposure_conditions']
    assert record['duration_seconds'] == 32. and record['applied']
    result = run_request(WorkflowRequest('develop_latent', archive, tmp_path/'final.npz', changed))
    medium = load_developed_medium_for_scan(result.medium_path)
    assert medium.metadata['film_process_model']['exposure_conditions'] == record
    original = medium.optical_density_rgb.copy()
    np.testing.assert_array_equal(scan_medium_output(medium, c).output_srgb, scan_medium_output(medium, changed).output_srgb)
    np.testing.assert_array_equal(medium.optical_density_rgb, original)
    old = deepcopy(c); old.exposure.duration_seconds = None
    old_latent = expose_linear_to_latent(scene(), old)
    assert 'exposure_conditions' not in develop_negative_from_latent(old_latent, changed).metadata['film_process_model']


@pytest.mark.parametrize('spectral', [False, True])
def test_full_grid_tiles_and_local_chemistry_use_the_same_exposure(spectral):
    from film_foundry.tests.test_spectral_grain import configuration, scene as texture
    from film_foundry.core.engine import expose_image_to_latent
    c = configuration(independent=False); c.enable_grain = False
    c.film.reciprocity_response = validate_response({'model': MODEL}); c.exposure.duration_seconds = 128.
    c.chemistry.development_adjacency_strength = .3
    c.film.dir_material_response_matrix = ((.1, .12, .08),) * 3
    if not spectral: c.film.spectral_material_profile = None; c.scanner.spectral_observation_profile = None
    c.processing.material_tile_rows = 7
    tiled = develop_negative(texture(), c, rng=np.random.default_rng(4))
    whole = deepcopy(c); whole.processing.material_tile_rows = 0
    full = develop_negative(texture(), whole, rng=np.random.default_rng(4))
    latent = expose_image_to_latent(texture(), whole, rng=np.random.default_rng(4))
    restored = develop_negative_from_latent(latent, whole, rng=np.random.default_rng(4))
    for item in (tiled, restored):
        np.testing.assert_allclose(item.optical_density_rgb, full.optical_density_rgb, atol=2e-6, rtol=2e-6)
        if spectral: np.testing.assert_allclose(item.spectral_medium.component_amounts, full.spectral_medium.component_amounts, atol=2e-6, rtol=2e-6)
    assert full.metadata['film_process_model']['exposure_conditions']['duration_seconds'] == 128.


def test_disabled_and_normal_interval_preserve_pixels_and_random_stream():
    c = config(); c.enable_grain = True
    original_rng = np.random.default_rng(12)
    original = develop_negative(scene(), c, rng=original_rng)
    expected_rng = original_rng.integers(0, 2**32, 8)
    for duration, response in ((.2, c.film.reciprocity_response), (128., None),
                               (128., {'model': MODEL, 'long_loss_per_stop': [0., 0., 0.]})):
        changed = deepcopy(c); changed.exposure.duration_seconds = duration; changed.film.reciprocity_response = response
        rng = np.random.default_rng(12); actual = develop_negative(scene(), changed, rng=rng)
        np.testing.assert_array_equal(actual.optical_density_rgb, original.optical_density_rgb)
        np.testing.assert_array_equal(rng.integers(0, 2**32, 8), expected_rng)


def test_optical_relay_passes_its_clock_without_multiplying_dose_twice():
    from film_foundry.app.optical_cascade import expose_on_film, project_medium
    from film_foundry.contracts.optical_cascade import OpticalRelay
    c = config(); c.exposure.duration_seconds = .001; before = asdict(c)
    source = develop_negative(scene(), c); original = source.optical_density_rgb.copy()
    relay = OpticalRelay(exposure_seconds=16., exposure_ev=-4.)
    latent, _ = expose_on_film(source, c, relay)
    expected = deepcopy(c); expected.exposure.duration_seconds = 16.
    direct = expose_linear_to_latent(project_medium(source, relay), expected)
    np.testing.assert_array_equal(latent.developability, direct.developability)
    assert latent.exposure_duration_seconds == 16. and asdict(c) == before
    np.testing.assert_array_equal(source.optical_density_rgb, original)
    dark, _ = expose_on_film(source, c, replace(relay, exposure_seconds=0.))
    assert dark.exposure_duration_seconds is None


def test_actual_editors_sharing_modes_and_cli_preserve_capture(monkeypatch, tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.film_time_editor import FilmTimeDialog
    from film_foundry.ui.film_exposure_editor import FilmExposureDialog
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.app.workflow import encode_recipe_share, config_from_share_code
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS', 'FILM_FOUNDRY_CAMERA_SETTINGS', 'FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key, str(tmp_path/(key+'.json')))
    app = _application(); c = config(); c.exposure.duration_seconds = 32.
    material = FilmTimeDialog(c, material=True); exposure = FilmExposureDialog(c)
    editor = FilmMaterialEditor(config=c); desktop = DesktopPanel(); desktop.auto_preview.set(False)
    try:
        material.apply(); exposure.loss.setValue(.5); exposure.resolve(); exposure.apply()
        assert material.result() == exposure.result() == 1
        assert material.reciprocity_value == c.film.reciprocity_response
        assert exposure.duration == pytest.approx(duration_for_loss(c.film.reciprocity_response, .5), rel=1e-8)
        assert editor._config_from_controls().film.reciprocity_response == c.film.reciprocity_response
        desktop._apply_loaded_config(c)
        desktop._edit_exposure_conditions()
        page=desktop.find_configuration_dialog('film-exposure')
        assert page is not None and page.isWindow()
        desktop.exposure_ev.set(.7)
        page.seconds.setValue(64.); page.apply()
        assert desktop._config().exposure.duration_seconds == 64.
        assert desktop._config().look.exposure_ev == .7
        restored = config_from_share_code(encode_recipe_share(c))
        assert restored.exposure.duration_seconds == 32. and restored.film.reciprocity_response == c.film.reciprocity_response
        assert condition_driven_config(restored).exposure.duration_seconds == 32.
        restored.chemistry.effect_intent = {'model': 'process_effect_intent_v1', 'amounts': {'chemical_stain': .1}}
        compiled, _ = compile_effect_intent(restored)
        assert compiled.exposure.duration_seconds == 32.
        assert DarkroomConfig.from_dict({}).exposure.duration_seconds is None
        from film_foundry.app.cli import build_parser, _apply_common_config
        args=build_parser().parse_args(['expose', str(tmp_path/'input.png'), str(tmp_path/'out.npz'),
                                       '--config-share', encode_recipe_share(c), '--exposure-seconds', '48'])
        assert _apply_common_config(args).exposure.duration_seconds == 48.
    finally:
        material.close(); exposure.close(); editor.root.destroy(); desktop.root.destroy(); app.processEvents()


def test_share_clock_extension_is_optional_and_typed():
    from film_foundry.app.workflow import recipe_share_payload, config_from_share_code, encode_recipe_share
    from film_foundry.contracts.config_share import encode_config_share
    c = config()
    assert 'exposure' not in recipe_share_payload(c)
    c.exposure.duration_seconds = .2
    assert config_from_share_code(encode_recipe_share(c)).exposure.duration_seconds == .2
    with pytest.raises(ValueError): encode_config_share({'exposure': '60s'})
