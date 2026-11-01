import copy
import json

import numpy as np
import pytest

from film_foundry.contracts.spectral_optics import SPECTRAL_POST_PROCESS_ARRAY_KEY, post_process_rgb_weights
from film_foundry.core.spectral_optics import SpectralMedium, capture_spectral_medium, load_spectral_medium
from film_foundry.core.engine import develop_negative, scan_medium_output, process_array
from film_foundry.core.execution_topology import execution_topology
from film_foundry.tests.test_spectral_grain import configuration, scene


@pytest.mark.parametrize("process", ["color_negative", "color_reversal", "bw_negative", "bw_reversal"])
def test_postprocess_preserves_pools_rgb_and_rng_and_changes_spectral_observation(process):
    cfg=configuration(process, independent=True)
    baseline=copy.deepcopy(cfg)
    cfg.chemistry.chemical_stain=.6
    cfg.chemistry.silver_plating=.4
    cfg.chemistry.reticulation_strength=.7
    a_rng=np.random.default_rng(92);b_rng=np.random.default_rng(92)
    a=develop_negative(scene(),cfg,rng=a_rng)
    legacy=copy.deepcopy(cfg)
    legacy.film.spectral_material_profile=None
    legacy.scanner.spectral_observation_profile=None
    b=develop_negative(scene(),legacy,rng=b_rng)
    for name in ("density_cmy","density_grain","optical_density_rgb"):
        np.testing.assert_array_equal(getattr(a,name),getattr(b,name))
    np.testing.assert_array_equal(a_rng.integers(2**32,size=8),b_rng.integers(2**32,size=8))
    # Deposits/structure are separate from imaging material, including grain.
    plain=develop_negative(scene(),baseline,rng=np.random.default_rng(92))
    # Accident generation historically advances the frame stream. Compare
    # chemical pools with grain disabled to avoid confusing a different noise draw.
    no_grain=copy.deepcopy(cfg);no_grain.enable_grain=False
    base_no_grain=copy.deepcopy(baseline);base_no_grain.enable_grain=False
    pa=develop_negative(scene(),no_grain,rng=np.random.default_rng(92))
    pb=develop_negative(scene(),base_no_grain,rng=np.random.default_rng(92))
    np.testing.assert_array_equal(pa.spectral_medium.component_amounts,pb.spectral_medium.component_amounts)
    assert pa.spectral_medium.base_component_amounts==pb.spectral_medium.base_component_amounts
    observer=cfg.scanner.spectral_observation_profile
    assert np.max(abs(capture_spectral_medium(a.spectral_medium,observer)-
                      capture_spectral_medium(plain.spectral_medium,observer)))>1e-4
    before=a.spectral_medium.post_process_density_rgb.copy()
    expected=scan_medium_output(a,cfg).output_srgb
    np.testing.assert_array_equal(process_array(scene(),cfg,rng=np.random.default_rng(92)),expected)
    np.testing.assert_array_equal(a.spectral_medium.post_process_density_rgb,before)


def test_proxy_is_explicit_spectral_density_sum_with_signed_structure_and_global_base():
    cfg=configuration();cfg.enable_grain=False
    base=develop_negative(scene(),cfg).spectral_medium
    overlay=np.full(base.component_amounts.shape[:2]+(3,),(.2,.1,.4),dtype=np.float32)
    overlay[:10]=-20 # Signed structure cannot imply gain above open gate.
    medium=SpectralMedium(base.profile,base.component_amounts,base.base_component_amounts,overlay)
    observer=cfg.scanner.spectral_observation_profile
    wave=np.asarray(observer.wavelengths_nm)
    density=np.einsum('...k,kn->...n',base.component_amounts.astype(np.float64),base.profile.component_absorbance)
    density+=np.einsum('...c,nc->...n',overlay,[post_process_rgb_weights(w) for w in wave])
    density=np.clip(density,0,80)
    quadrature=np.zeros(len(wave));quadrature[:-1]+=np.diff(wave)/2;quadrature[1:]+=np.diff(wave)/2
    weights=np.asarray(observer.response_rgb)*observer.illuminant*quadrature
    weights/=weights.sum(1,keepdims=True)
    expected=np.einsum('...n,cn->...c',10**(-density),weights)
    with execution_topology('reference'): reference=capture_spectral_medium(medium,observer)
    with execution_topology('optimized'): actual=capture_spectral_medium(medium,observer)
    np.testing.assert_array_equal(reference,actual)
    np.testing.assert_allclose(actual,expected,rtol=1e-7)
    assert actual.min()>=0 and actual.max()<=1
    np.testing.assert_array_equal(actual[4:19],capture_spectral_medium(medium,observer,row_slice=slice(4,19)))
    np.testing.assert_array_equal(capture_spectral_medium(medium,observer,clear_base=True),
                                  capture_spectral_medium(base,observer,clear_base=True))


def test_actual_headless_archive_and_preview_preserve_overlay(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.preview_inputs import preview_medium
    from film_foundry.core.negative_io import load_developed_medium_for_scan,load_developed_negative_npz
    cfg=configuration();cfg.chemistry.chemical_stain=.5;cfg.chemistry.reticulation_strength=.4
    source=tmp_path/'source.png';target=tmp_path/'frame.darkroom_negative.npz'
    Image.fromarray(np.uint8(np.rint(scene()*255))).save(source)
    run_request(WorkflowRequest('develop',source,target,cfg))
    medium=load_developed_medium_for_scan(target)
    full=load_developed_negative_npz(target)
    np.testing.assert_array_equal(medium.spectral_medium.post_process_density_rgb,full.spectral_medium.post_process_density_rgb)
    preview=preview_medium(medium,15)
    assert preview.spectral_medium.post_process_density_rgb.shape[:2]==preview.frame_shape[:2]
    assert not preview.spectral_medium.post_process_density_rgb.flags.writeable
    run_request(WorkflowRequest('scan',target,tmp_path/'scan.png',cfg))
    np.testing.assert_array_equal(medium.spectral_medium.post_process_density_rgb,
        load_developed_medium_for_scan(target).spectral_medium.post_process_density_rgb)


@pytest.mark.parametrize('damage',['missing_array','orphan_array','unknown_model','nan','wrong_shape'])
def test_bad_postprocess_capability_is_not_silently_ignored(tmp_path,damage):
    cfg=configuration();cfg.enable_grain=False
    base=develop_negative(scene(),cfg).spectral_medium
    payload=base.metadata();payload['post_process_model']='piecewise_rgb_density_overlay_v1'
    overlay=np.zeros(scene().shape,dtype=np.float32)
    if damage=='unknown_model':payload['post_process_model']='unknown'
    if damage=='orphan_array':payload.pop('post_process_model')
    if damage=='nan':overlay[0,0,0]=np.nan
    if damage=='wrong_shape':overlay=overlay[:2]
    arrays={'spectral_component_amounts':base.component_amounts}
    if damage!='missing_array':arrays[SPECTRAL_POST_PROCESS_ARRAY_KEY]=overlay
    path=tmp_path/'broken.npz';np.savez(path,**arrays)
    with np.load(path,allow_pickle=False) as data:
        with pytest.raises(ValueError):load_spectral_medium(data,{'spectral_medium':payload},scene().shape)
