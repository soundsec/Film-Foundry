from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from film_foundry.app.workflow import config_from_share_code, encode_recipe_share
from film_foundry.contracts import data_kernel
from film_foundry.contracts.config_share import decode_config_share
from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.core.engine import develop_negative, develop_negative_from_latent, expose_image_to_latent, scan_medium_output
from film_foundry.core.latent_io import latent_identity_from_film, load_exposed_latent, mark_exposed_latent_consumed, save_exposed_latent
from film_foundry.core.sensitometry import rgb_exposure_to_layer_exposure
from film_foundry.core.spectral_exposure import compile_spectral_exposure
from film_foundry.core.states import developed_medium_metadata
from film_foundry.model.config import DarkroomConfig


def _sample():
    path = Path(__file__).resolve().parents[2] / 'android/kernel/src/test/resources/spectral_exposure_sample.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _profile():
    return SpectralExposureProfile.from_value(_sample()['profile'])


def _image():
    image = np.linspace(0.03, 0.98, 24 * 36 * 3, dtype=np.float32).reshape(24, 36, 3)
    image[8:12, 16:20] = 1.0
    return image


def test_contraction_matches_analytic_shared_fixture_and_explicit_spectral_integration():
    profile = _profile()
    matrix = np.asarray(compile_spectral_exposure(profile), dtype=np.float32)
    np.testing.assert_allclose(matrix, _sample()['matrix'], rtol=1e-7, atol=1e-8)
    film = DarkroomConfig().film
    film.spectral_exposure_profile = profile
    image = _image()
    before = image.copy()
    spectrum = np.einsum('...c,cn->...n', image.astype(np.float64), profile.rgb_basis)
    wave = np.asarray(profile.wavelengths_nm)
    expected = []
    for sensitivity, gain in zip(profile.layer_sensitivity, profile.layer_gain):
        integrand = spectrum * sensitivity
        expected.append(np.sum((integrand[..., :-1] + integrand[..., 1:]) * np.diff(wave) * 0.5, axis=-1) * gain)
    np.testing.assert_allclose(rgb_exposure_to_layer_exposure(image, film), np.stack(expected, axis=-1), rtol=2e-7)
    np.testing.assert_array_equal(image, before)


def test_exposure_setup_replaces_matrix_consistently_for_spatial_sources_and_keeps_recipe():
    spectral = DarkroomConfig()
    spectral.film.spectral_exposure_profile = _profile()
    spectral.film.emulsion_highlight_spread_strength = 0.2
    spectral.film.halation_return_model = 'layer_selective'
    spectral.chemistry.development_adjacency_strength = 0.2
    matrix_only = copy.deepcopy(spectral)
    matrix_only.film.spectral_exposure_profile = None
    matrix_only.film.layer_sensitivity_matrix = compile_spectral_exposure(_profile())
    before = asdict(spectral)
    a = develop_negative(_image(), spectral, rng=np.random.default_rng(31))
    b = develop_negative(_image(), matrix_only, rng=np.random.default_rng(31))
    for key in ('after_halation', 'density_cmy', 'density_grain', 'optical_density_rgb'):
        np.testing.assert_array_equal(getattr(a, key), getattr(b, key))
    assert asdict(spectral) == before
    assert a.metadata['runtime_config'].film.spectral_exposure_profile == _profile()
    assert a.metadata['runtime_config'].film.layer_sensitivity_matrix == spectral.film.layer_sensitivity_matrix
    model = developed_medium_metadata(a)['film_process_model']
    assert model['spectral_exposure_profile'] == _profile().as_dict()
    assert model['execution_implementation']['component_models']['exposure_mapping'] == _profile().model


def test_frozen_exposure_survives_copy_npz_consumption_and_recipe_changes(tmp_path):
    config = DarkroomConfig()
    config.film.spectral_exposure_profile = _profile()
    config.film.spatial_reference_short_edge_mm = 24.0
    latent = expose_image_to_latent(_image(), config)
    assert latent.copy().spectral_exposure_profile is latent.spectral_exposure_profile
    path = tmp_path / 'frame.darkroom_latent.npz'
    save_exposed_latent(path, latent, identity=latent_identity_from_film(config.film))
    loaded, meta = load_exposed_latent(path)
    assert loaded.spectral_exposure_profile == _profile()
    assert meta['spectral_exposure_profile'] == _profile().as_dict()
    mark_exposed_latent_consumed(path)
    consumed, _ = load_exposed_latent(path, allow_consumed=True)
    assert consumed.spectral_exposure_profile == _profile()
    changed = copy.deepcopy(config)
    raw = _profile().as_dict()
    raw['layer_gain'] = [2, 3, 4]
    raw['profile_id'] = 'later_exposure'
    changed.film.spectral_exposure_profile = SpectralExposureProfile.from_value(raw)
    changed.chemistry.frame_long_edge_mm = 99.0
    a = develop_negative_from_latent(loaded.copy(), config, rng=np.random.default_rng(37))
    b = develop_negative_from_latent(loaded.copy(), changed, rng=np.random.default_rng(37))
    for key in ('density_cmy', 'density_grain', 'optical_density_rgb'):
        np.testing.assert_array_equal(getattr(a, key), getattr(b, key))
    assert b.metadata['film_process_model']['spectral_exposure_profile'] == _profile().as_dict()
    assert changed.film.spectral_exposure_profile.profile_id == 'later_exposure'
    before = b.optical_density_rgb.copy()
    scan_medium_output(b, changed)
    np.testing.assert_array_equal(b.optical_density_rgb, before)


def test_old_latent_does_not_acquire_new_exposure_provenance():
    config = DarkroomConfig()
    latent = expose_image_to_latent(_image(), config)
    config.film.spectral_exposure_profile = _profile()
    config.film.spatial_reference_short_edge_mm = 24.0
    config.chemistry.frame_long_edge_mm = 44.0
    formed = develop_negative_from_latent(latent, config)
    assert 'spectral_exposure_profile' not in formed.metadata['film_process_model']
    assert 'material_geometry' not in formed.metadata['film_process_model']
    assert formed.metadata['runtime_config'].film.spatial_reference_short_edge_mm is None
    assert formed.metadata['runtime_config'].chemistry.frame_long_edge_mm is None


def test_profile_round_trips_json_and_ffc2_with_same_shared_fields():
    config = DarkroomConfig.from_dict({'film': {'spectral_exposure_profile': _profile().as_dict()}})
    restored = DarkroomConfig.from_dict(json.loads(json.dumps(asdict(config))))
    shared = config_from_share_code(encode_recipe_share(config))
    assert restored.film.spectral_exposure_profile == shared.film.spectral_exposure_profile == _profile()
    assert set(_profile().as_dict()) == set(data_kernel.SPECTRAL_EXPOSURE_PROFILE_FIELDS)
    fixture = Path(__file__).resolve().parents[2] / 'android/kernel/src/test/resources/spectral_exposure_sample.ffc2'
    assert decode_config_share(fixture.read_text(encoding='utf-8'))['film']['spectral_exposure_profile'] == _profile().as_dict()


def test_single_layer_spectrum_is_used_for_monochrome_material():
    config = DarkroomConfig()
    config.mode = 'bw_negative'
    config.film.color_process = 'bw'
    raw = _profile().as_dict()
    raw['layer_sensitivity'] = [raw['layer_sensitivity'][0]]
    raw['layer_gain'] = [1.0]
    config.film.spectral_exposure_profile = SpectralExposureProfile.from_value(raw)
    latent = expose_image_to_latent(_image(), config)
    assert latent.developability.shape[-1] == 1
    assert latent.spectral_exposure_profile.layer_count == 1
    for program in ("color_negative", "color_reversal"):
        choice = copy.deepcopy(config)
        choice.mode = choice.chemistry.program_key = program
        formed = develop_negative_from_latent(latent.copy(), choice)
        assert np.isfinite(formed.optical_density_rgb).all()
        assert latent.halide.shape[-1] == 1
    wrong = DarkroomConfig()
    wrong.film.spectral_exposure_profile = config.film.spectral_exposure_profile
    with pytest.raises(ValueError, match='layer count'):
        expose_image_to_latent(_image(), wrong)


@pytest.mark.parametrize('key,value', [
    ('model', 'unknown'), ('working_space', 'prophoto'), ('profile_id', ''),
    ('wavelengths_nm', [400, 450, 450, 700]), ('wavelengths_nm', [400, True, 600, 700]),
    ('rgb_basis', [[0, -1, 0, 0]] * 3), ('layer_gain', [1, 0, 1]),
    ('layer_gain', [1, float('nan'), 1]), ('layer_sensitivity', [[0, 1]] * 3),
])
def test_invalid_profiles_fail_before_exposure(key, value):
    raw = _profile().as_dict()
    raw[key] = value
    with pytest.raises(ValueError):
        DarkroomConfig.from_dict({'film': {'spectral_exposure_profile': raw}})


def test_overflow_and_no_basis_overlap_are_rejected():
    raw = _profile().as_dict()
    raw['layer_gain'] = [1e300] * 3
    with pytest.raises(ValueError, match='float32'):
        compile_spectral_exposure(SpectralExposureProfile.from_value(raw))
    raw['layer_gain'] = [1] * 3
    raw['rgb_basis'] = [[1, 0, 0, 0]] * 3
    raw['layer_sensitivity'] = [[0, 1, 0, 0]] * 3
    with pytest.raises(ValueError, match='overlap'):
        compile_spectral_exposure(SpectralExposureProfile.from_value(raw))


def test_latent_layer_mismatch_is_rejected_before_saving(tmp_path):
    latent = expose_image_to_latent(_image(), DarkroomConfig())
    raw = _profile().as_dict()
    raw['layer_sensitivity'] = [raw['layer_sensitivity'][0]]
    raw['layer_gain'] = [1]
    latent.spectral_exposure_profile = SpectralExposureProfile.from_value(raw)
    with pytest.raises(ValueError, match='pool layers'):
        save_exposed_latent(tmp_path / 'invalid.darkroom_latent.npz', latent, identity={})


def test_headless_expose_develop_and_scan_save_frozen_profile_and_original_recipe(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest, run_request
    from film_foundry.core.negative_io import load_developed_medium_for_scan

    config = DarkroomConfig()
    config.film.spectral_exposure_profile = _profile()
    config.enable_grain = False
    config.output.save_scanner_raw = config.output.export_transparent_plate = False
    source = tmp_path / 'input.png'
    Image.fromarray((_image() * 255).astype(np.uint8)).save(source)
    latent = tmp_path / 'frame.darkroom_latent.npz'
    formed = tmp_path / 'frame.darkroom_negative.npz'
    run_request(WorkflowRequest('expose', source, latent, config))
    run_request(WorkflowRequest('develop_latent', latent, formed, config))
    loaded = load_developed_medium_for_scan(formed)
    assert loaded.metadata['film_process_model']['spectral_exposure_profile'] == _profile().as_dict()
    assert loaded.metadata['runtime_config'].film.spectral_exposure_profile == _profile()
    assert loaded.metadata['runtime_config'].film.layer_sensitivity_matrix == config.film.layer_sensitivity_matrix
    optical = loaded.optical_density_rgb.copy()
    scanned = run_request(WorkflowRequest('scan', formed, tmp_path / 'scan.png', config))
    assert np.isfinite(scanned.extra['scanned'].output_srgb).all()
    reloaded = load_developed_medium_for_scan(formed)
    np.testing.assert_array_equal(reloaded.optical_density_rgb, optical)
