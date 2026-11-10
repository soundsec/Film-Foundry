from copy import deepcopy

import numpy as np
import pytest

from film_foundry.contracts.carbon_backing import MODEL, MODEL_V2, validate_carbon_backing
from film_foundry.core.carbon_backing import exposure_return, initial_mass_for_halation_loss
from film_foundry.core.engine import expose_image_to_latent
from film_foundry.core.halation import apply_halation, halation_return_field
from film_foundry.tests.test_carbon_backing import PRE, WASH, config, assert_balance


def scene():
    image = np.full((31, 47, 3), .06, np.float32)
    image[12:18, 20:26] = (1., .9, .8)
    return image


def coupled():
    cfg = config()
    cfg.enable_halation = True
    cfg.film.carbon_backing = validate_carbon_backing({'model': MODEL_V2, 'initial_mg_m2': 10.})
    cfg.film.halation_threshold = .3
    cfg.film.halation_strength = .7
    cfg.film.halation_area_suppression = 0.
    # The standard peak neighbourhood is subpixel at this test size; widen the
    # fixture's declared radius so the bright edge actually produces return.
    cfg.film.halation_peak_radius = .04
    return cfg


def test_target_resolves_real_inventory_and_rejects_unreachable_loss():
    old = validate_carbon_backing({})
    assert old['model'] == MODEL and exposure_return(old) is None
    assert exposure_return(None) is None
    material = coupled().film.carbon_backing
    for loss in (0., 1., 4., 12.):
        mass = initial_mass_for_halation_loss(material, loss)
        resolved = {**material, 'initial_mg_m2': mass}
        report = exposure_return(resolved)
        assert report['return_fraction'] == pytest.approx(2. ** -loss)
        assert report['loss_stops'] == pytest.approx(loss)
        cfg = config(PRE, WASH); cfg.film.carbon_backing = resolved
        from film_foundry.tests.test_carbon_backing import form
        fields, _ = form(cfg)
        if mass: assert_balance(fields, mass)
    with pytest.raises(ValueError): initial_mass_for_halation_loss(old, 2.)
    with pytest.raises(ValueError): initial_mass_for_halation_loss({**material, 'density_per_mg_m2': 0.}, 2.)
    with pytest.raises(ValueError): initial_mass_for_halation_loss({**material, 'density_per_mg_m2': 1e-8}, 2.)
    for value in (True, float('nan'), float('inf'), -1.):
        with pytest.raises(ValueError): initial_mass_for_halation_loss(material, value)


def test_backing_attenuates_return_only_and_zero_paths_are_exact():
    cfg = coupled(); image = scene(); original = image.copy()
    legacy = deepcopy(cfg.film); legacy.carbon_backing['model'] = MODEL
    source = halation_return_field(image, legacy, work_long_edge=24)
    assert np.max(source) > 0
    ratio = exposure_return(cfg.film.carbon_backing)['return_fraction']
    actual = halation_return_field(image, cfg.film, work_long_edge=24)
    np.testing.assert_allclose(actual, source * ratio, rtol=1e-7, atol=1e-12)
    before = apply_halation(image, legacy, work_long_edge=24) - image
    after = apply_halation(image, cfg.film, work_long_edge=24) - image
    np.testing.assert_allclose(after, before * ratio, atol=1e-7)
    np.testing.assert_array_equal(image, original)
    for key in ('initial_mg_m2', 'density_per_mg_m2'):
        zero = deepcopy(cfg.film); zero.carbon_backing[key] = 0.
        np.testing.assert_array_equal(halation_return_field(image, zero, work_long_edge=24), source)
    opaque = deepcopy(cfg.film)
    opaque.carbon_backing.update(initial_mg_m2=1000., density_per_mg_m2=10.)
    assert halation_return_field(image, opaque, allow_empty_none=True) is None
    np.testing.assert_array_equal(apply_halation(image, opaque), image)


@pytest.mark.parametrize('return_model', ['compatibility_rgb', 'layer_selective'])
def test_all_film_exposure_branches_share_backing_and_leave_primary_exposure_alone(return_model):
    for mode in ('color_negative', 'color_reversal', 'bw_negative', 'bw_reversal'):
        cfg = coupled(); cfg.mode = mode; cfg.chemistry.program_key = mode
        if mode.startswith('bw_'):
            cfg.film.color_process = 'bw'; cfg.enable_subtractive = False
        if mode.endswith('reversal'): cfg.film.image_polarity = 'positive'
        cfg.film.halation_return_model = return_model
        cfg.processing.material_tile_rows = 7
        cfg.processing.material_tile_threshold_megapixels = .0001
        legacy = deepcopy(cfg); legacy.film.carbon_backing['model'] = MODEL
        equivalent = deepcopy(legacy)
        equivalent.film.halation_strength *= exposure_return(cfg.film.carbon_backing)['return_fraction']
        def expose(c): return expose_image_to_latent(scene(), c, rng=np.random.default_rng(5)).developability
        actual = expose(cfg)
        np.testing.assert_allclose(actual, expose(equivalent), atol=2e-7, rtol=2e-7)
        assert np.max(np.abs(actual-expose(legacy))) > 1e-6
        cfg.enable_halation = legacy.enable_halation = False
        np.testing.assert_array_equal(expose(cfg), expose(legacy))


@pytest.mark.parametrize('spectral', [False, True])
def test_frozen_capture_share_archive_and_scan_do_not_recompute_return(tmp_path, monkeypatch, spectral):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest, run_request
    from film_foundry.app.workflow import encode_recipe_share, config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz, load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    cfg = coupled(); cfg.film.halation_return_model = 'layer_selective'
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        profile = configuration()
        cfg.film.spectral_material_profile = profile.film.spectral_material_profile
        cfg.scanner.spectral_observation_profile = profile.scanner.spectral_observation_profile
    source = tmp_path/'source.png'; Image.fromarray(np.uint8(scene()*255)).save(source)
    latent = tmp_path/'source.darkroom_latent.npz'
    run_request(WorkflowRequest('expose', source, latent, cfg))
    shared = config_from_share_code(encode_recipe_share(cfg))
    assert shared.film.carbon_backing == cfg.film.carbon_backing
    expected = exposure_return(cfg.film.carbon_backing)
    cfg.film.carbon_backing = None
    cfg.chemistry.process_events = config(PRE, WASH).chemistry.process_events
    def must_not_reexpose(*args, **kwargs): raise AssertionError('saved latent was re-exposed')
    monkeypatch.setattr('film_foundry.core.engine.apply_halation', must_not_reexpose)
    monkeypatch.setattr('film_foundry.core.engine.halation_return_field', must_not_reexpose)
    result = run_request(WorkflowRequest('develop_latent', latent, tmp_path/'clean', cfg))
    medium = load_developed_negative_npz(result.medium_path)
    report = medium.metadata['film_process_model']['halation_formation']['backing_at_exposure']
    assert report == {**expected, 'evaluation': 'already_frozen_in_latent'}
    assert_balance(medium.process_surface, 10.)
    assert medium.process_surface['backing_removed_mg_m2'].mean() > 9.9
    before = medium.optical_density_rgb.copy()
    scan = scan_medium_output(medium, cfg).output_srgb
    light = load_developed_medium_for_scan(result.medium_path)
    np.testing.assert_array_equal(scan, scan_medium_output(light, cfg).output_srgb)
    np.testing.assert_array_equal(before, medium.optical_density_rgb)
    assert light.metadata['film_process_model']['halation_formation']['backing_at_exposure'] == report


def test_actual_material_editor_preserves_legacy_and_resolves_same_parameter(monkeypatch, tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessResponseDialog
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.core.process_couplings import describe_process_couplings
    for key in ('FILM_FOUNDRY_UI_SETTINGS', 'FILM_FOUNDRY_CAMERA_SETTINGS', 'FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key, str(tmp_path/(key+'.json')))
    app = _application(); cfg = config()
    dialog = ProcessResponseDialog(None, carbon_backing=cfg.film.carbon_backing)
    try:
        assert not dialog.backing_halation.isChecked()
        assert dialog.read_backing() == cfg.film.carbon_backing
        dialog.backing_loss.setValue(3.); dialog.resolve_backing_load()
        assert dialog.backing_halation.isChecked()
        dialog.apply(); cfg.film.carbon_backing = dialog.carbon_backing
        assert exposure_return(cfg.film.carbon_backing)['loss_stops'] == pytest.approx(3., abs=1e-6)
        actual = FilmMaterialEditor(config=cfg)
        try: assert actual._config_from_controls().film.carbon_backing == cfg.film.carbon_backing
        finally: actual.root.destroy()
        codes = {v['code'] for v in describe_process_couplings(cfg)['findings']}
        assert {'carbon_backing_halation', 'carbon_backing_routing'} <= codes
        from film_foundry.core.effect_intent import compile_effect_intent
        compiled, _ = compile_effect_intent(cfg)
        assert compiled.film.carbon_backing == cfg.film.carbon_backing
    finally:
        dialog.close(); app.processEvents()
