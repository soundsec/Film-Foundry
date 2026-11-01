import copy
import json
from pathlib import Path

import numpy as np
import pytest

from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile
from film_foundry.core.spectral_optics import SpectralMedium, capture_spectral_medium, final_pool_amounts
from film_foundry.core.engine import develop_negative, develop_negative_from_latent, expose_image_to_latent, scan_medium_direct, scan_medium_output
from film_foundry.core.latent_io import save_exposed_latent, load_exposed_latent, latent_identity_from_film
from film_foundry.core.states import developed_medium_metadata
from film_foundry.core.negative_io import load_developed_medium_for_scan, load_developed_negative_npz
from film_foundry.model.config import DarkroomConfig


def fixture():
    path = Path(__file__).resolve().parents[2] / 'android/kernel/src/test/resources/spectral_optics_sample.json'
    return json.loads(path.read_text(encoding='utf-8'))


def config():
    cfg = DarkroomConfig(enable_grain=False, enable_mtf=False, enable_halation=False)
    sample = fixture()
    cfg.film.spectral_material_profile = SpectralMaterialProfile.from_value(sample['material'])
    cfg.scanner.spectral_observation_profile = SpectralObservationProfile.from_value(sample['observer'])
    return cfg


def image():
    return np.linspace(.05, .95, 12*18*3, dtype=np.float32).reshape(12,18,3)


def test_transmission_matches_explicit_spectral_integral_and_ignores_rgb_master():
    cfg = config()
    medium = develop_negative(image(), cfg)
    state = medium.spectral_medium
    observer = cfg.scanner.spectral_observation_profile
    wave = np.asarray(observer.wavelengths_nm)
    density = np.einsum('...k,kn->...n', state.component_amounts.astype(np.float64), state.profile.component_absorbance)
    source = np.asarray(observer.response_rgb) * observer.illuminant
    def integral(v):
        return np.sum((v[..., :-1]+v[..., 1:]) * np.diff(wave) * .5, axis=-1)
    expected = np.stack([integral(10**(-density)*row)/integral(row) for row in source],axis=-1)
    actual = capture_spectral_medium(state, observer)
    np.testing.assert_allclose(actual,expected,rtol=1e-7)
    np.testing.assert_array_equal(actual[3:9], capture_spectral_medium(state,observer,row_slice=slice(3,9)))
    before = state.component_amounts.copy()
    optical = medium.optical_density_rgb.copy()
    a = scan_medium_output(medium,cfg,'direct')
    b = scan_medium_direct(medium,cfg,'direct')
    np.testing.assert_array_equal(a.output_srgb,b.output_srgb)
    assert a.metadata['optical_capture']['model']=='spectral_beer_lambert_capture_v1'
    np.testing.assert_array_equal(state.component_amounts,before)
    np.testing.assert_array_equal(medium.optical_density_rgb,optical)
    assert not state.component_amounts.flags.writeable
    medium.optical_density_rgb=np.zeros_like(optical)
    np.testing.assert_array_equal(a.output_srgb,scan_medium_output(medium,cfg,'direct').output_srgb)


@pytest.mark.parametrize('process,subtractive', [('color_negative',True),('color_reversal',True),('bw_negative',False),('bw_reversal',False)])
def test_four_programs_retain_actual_final_pools_and_tile_latent_agree(process,subtractive):
    cfg=config()
    cfg.enable_subtractive=subtractive
    cfg.chemistry.program_key=process
    if not subtractive: cfg.film.color_process='bw'
    latent=expose_image_to_latent(image(),cfg)
    a=develop_negative(image(),cfg)
    b=develop_negative_from_latent(latent,cfg)
    np.testing.assert_allclose(a.spectral_medium.component_amounts,b.spectral_medium.component_amounts,rtol=1e-6,atol=1e-7)
    assert np.isfinite(scan_medium_output(a,cfg).output_srgb).all()
    if not subtractive: assert not np.any(a.spectral_medium.component_amounts[...,:3])


def test_material_definition_is_frozen_in_latent_and_not_added_to_old_latent(tmp_path):
    cfg=config()
    state=expose_image_to_latent(image(),cfg)
    assert state.copy().spectral_material_profile is state.spectral_material_profile
    path=tmp_path/'frame.darkroom_latent.npz'
    save_exposed_latent(path,state,identity=latent_identity_from_film(cfg.film))
    loaded,_=load_exposed_latent(path)
    changed=copy.deepcopy(cfg)
    changed.film.spectral_material_profile=None
    medium=develop_negative_from_latent(loaded,changed)
    assert medium.spectral_medium.profile == cfg.film.spectral_material_profile
    old=expose_image_to_latent(image(),changed)
    legacy=develop_negative_from_latent(old,cfg)
    assert legacy.spectral_medium is None
    with pytest.raises(ValueError,match='component amounts'):
        scan_medium_output(legacy,cfg)


@pytest.mark.parametrize('accident', ['chemical_stain', 'silver_plating', 'reticulation_strength'])
def test_postprocess_is_retained_without_changing_source_latent(accident):
    cfg=config()
    state=expose_image_to_latent(image(),cfg)
    before=state.developability.copy()
    cfg.enable_grain=True
    setattr(cfg.chemistry, accident, .2)
    medium=develop_negative_from_latent(state.copy(),cfg)
    assert medium.spectral_medium.post_process_density_rgb is not None
    assert not medium.spectral_medium.post_process_density_rgb.flags.writeable
    assert np.isfinite(scan_medium_output(medium,cfg).output_srgb).all()
    np.testing.assert_array_equal(state.developability,before)


def test_headless_optional_archive_roundtrip_and_paired_capability(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest, run_request
    cfg=config()
    source=tmp_path/'source.png'
    Image.fromarray(np.rint(image()*255).astype(np.uint8)).save(source)
    latent=tmp_path/'frame.darkroom_latent.npz'
    formed=tmp_path/'frame.darkroom_negative.npz'
    run_request(WorkflowRequest('expose',source,latent,cfg))
    run_request(WorkflowRequest('develop_latent',latent,formed,cfg))
    medium=load_developed_medium_for_scan(formed)
    full=load_developed_negative_npz(formed)
    np.testing.assert_array_equal(medium.spectral_medium.component_amounts,full.spectral_medium.component_amounts)
    observed=run_request(WorkflowRequest('scan',formed,tmp_path/'scan.png',cfg))
    assert observed.extra['scanned'].metadata['optical_capture']['model']=='spectral_beer_lambert_capture_v1'
    broken=tmp_path/'broken.npz'
    np.savez(broken,optical_density_rgb=medium.optical_density_rgb,
             developed_medium_metadata=np.asarray(json.dumps(developed_medium_metadata(medium))))
    with pytest.raises(ValueError,match='paired'): load_developed_medium_for_scan(broken)


@pytest.mark.parametrize('field,value', [('model','unknown'),('coefficient_unit','rgb_density'),('component_names',['wrong']),('wavelengths_nm',[400,400]),('component_absorbance',[[-1]*4]*9)])
def test_invalid_material_definitions_fail(field,value):
    profile=fixture()['material']
    profile[field]=value
    with pytest.raises(ValueError): SpectralMaterialProfile.from_value(profile)


def test_clear_base_grid_mismatch_and_effective_band_approximation_bound():
    cfg=config()
    medium=develop_negative(image(),cfg).spectral_medium
    observer=cfg.scanner.spectral_observation_profile
    wave=np.asarray(observer.wavelengths_nm)
    q=np.zeros(4);q[:-1]+=np.diff(wave)/2;q[1:]+=np.diff(wave)/2
    weights=np.asarray(observer.response_rgb)*observer.illuminant*q
    weights/=weights.sum(axis=1,keepdims=True)
    effective=weights@np.asarray(medium.profile.component_absorbance).T
    reduced=10**(-np.einsum('...k,ck->...c',medium.component_amounts,effective))
    full=capture_spectral_medium(medium,observer)
    assert np.all(reduced<=full+1e-7)
    assert np.max(full-reduced)>1e-3
    base=SpectralMedium(medium.profile,np.asarray(medium.base_component_amounts,dtype=np.float32).reshape(1,1,9),medium.base_component_amounts)
    np.testing.assert_allclose(capture_spectral_medium(medium,observer,clear_base=True),capture_spectral_medium(base,observer),rtol=1e-7)
    bad=observer.as_dict();bad['wavelengths_nm']=[401,450,600,700]
    with pytest.raises(ValueError,match='grids'): capture_spectral_medium(medium,bad)


@pytest.mark.parametrize('mode',['negative','direct'])
def test_spectral_scan_border_and_tile_keep_one_global_interpretation(mode):
    cfg=config()
    cfg.scanner.include_clear_base_border=True
    cfg.output.scanner_raw_border_min_px=2
    cfg.processing.scan_tile_rows=3
    medium=develop_negative(image(),cfg)
    tiled=scan_medium_output(medium,cfg,mode)
    full=scan_medium_direct(medium,cfg,mode)
    np.testing.assert_array_equal(tiled.output_srgb,full.output_srgb)


def test_actual_pool_extraction_and_tiled_formation_match_without_rgb_inversion():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced
    from film_foundry.core.engine import _develop_reduced_material_tiled
    from film_foundry.core.color import srgb_to_linear
    cfg=config()
    linear=srgb_to_linear(image())
    reduced=develop_color_negative_reduced(linear,cfg.film,cfg.chemistry)
    base,expected=final_pool_amounts(reduced.process_result.final_medium)
    amounts=np.empty_like(expected);tiled_base=[]
    _develop_reduced_material_tiled(linear,cfg,'color_negative',3,
        spectral_component_output=amounts,spectral_base_output=tiled_base)
    np.testing.assert_array_equal(amounts,expected)
    np.testing.assert_array_equal(amounts[...,:3],reduced.process_result.final_medium.dye)
    np.testing.assert_array_equal(amounts[...,3],np.mean(reduced.process_result.final_medium.metallic_silver,axis=-1))
    assert tuple(tiled_base)==base


def test_rgb_raw_cannot_silently_claim_spectral_observation():
    from film_foundry.core.engine import scan_scanner_raw,scan_scanner_raw_direct
    cfg=config()
    for scan in (lambda:scan_scanner_raw(image(),cfg),lambda:scan_scanner_raw_direct(image(),cfg,'direct')):
        with pytest.raises(ValueError,match='material components'): scan()


def test_spectral_scratch_reuse_is_exact_and_setup_cache_uses_values_not_profile_id():
    from film_foundry.core.execution_topology import execution_topology
    from film_foundry.core.spectral_optics import _spectral_setup
    cfg=config();state=develop_negative(image(),cfg).spectral_medium
    observer=cfg.scanner.spectral_observation_profile
    with execution_topology('reference'):reference=capture_spectral_medium(state,observer)
    with execution_topology('optimized'):optimized=capture_spectral_medium(state,observer)
    np.testing.assert_array_equal(reference,optimized)
    first=_spectral_setup(state.profile,observer)
    assert _spectral_setup(state.profile,observer) is first
    assert not first[0].flags.writeable and not first[1].flags.writeable
    changed=observer.as_dict();changed['illuminant']=[1,10,1,1]
    new_observer=SpectralObservationProfile.from_value(changed)
    assert _spectral_setup(state.profile,new_observer) is not first
    assert np.max(np.abs(capture_spectral_medium(state,new_observer)-reference))>1e-3
