from copy import deepcopy
import numpy as np
import pytest

from film_foundry.contracts.film_time import validate_response,RESPONSE_MODEL_V2
from film_foundry.core.film_process.time_history import light_surface_halide
from film_foundry.tests.test_film_time_history import history
from film_foundry.tests.test_surface_lifecycle import config as surface_config,form


def config():
    c=surface_config({'kind':'silver_bath','after_bath':1,'radius_mm':8.})
    c.enable_grain=False;c.enable_mtf=False;c.enable_halation=False
    c.film.process_response={'model':'creative_process_response_v4'}
    c.film.time_response=validate_response({'model':RESPONSE_MODEL_V2})
    c.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide','strength':.2})
    c.chemistry.post_process_light=history('light',100.)
    c.output.save_scanner_raw=False;c.output.export_transparent_plate=False
    return c


def test_surface_printout_is_finite_preserves_attachment_and_splits_time():
    response=config().film.time_response
    initial={'surface_halide_mg_m2':np.array([[0.,3.,8.]],np.float32),
        'surface_silver_mg_m2':np.array([[2.,0.,1.]],np.float32),
        'emulsion_halide_mg_m2':np.array([[0.,1.,7.]],np.float32),
        'emulsion_silver_mg_m2':np.array([[1.,0.,.4]],np.float32),
        'emulsion_coverage':np.full((1,3),.2,np.float32)}
    one=deepcopy(initial);split=deepcopy(initial)
    report=light_surface_halide(one,history('light',100.),response)
    for _ in range(4):light_surface_halide(split,history('light',25.),response)
    for key in one:np.testing.assert_allclose(one[key],split[key],atol=1e-6)
    for prefix in ('surface','emulsion'):
        np.testing.assert_allclose(one[prefix+'_halide_mg_m2']+one[prefix+'_silver_mg_m2'],
            initial[prefix+'_halide_mg_m2']+initial[prefix+'_silver_mg_m2'],atol=1e-6)
    assert report['converted_mg_m2']>0
    assert one['surface_silver_mg_m2'][0,0]==2.
    for c,h in ((None,history('light',100.)),({**response,'model':'film_time_response_v1'},history('light',100.)),(response,history('light',0.)),(response,None)):
        untouched=deepcopy(initial);light_surface_halide(untouched,h,c)
        for key in initial:np.testing.assert_array_equal(initial[key],untouched[key])


def test_surface_light_follows_fixing_and_cannot_restore_removed_salt():
    c=config();light,a=form(c)
    old=deepcopy(c);old.film.time_response['model']='film_time_response_v1'
    dark,b=form(old)
    assert light['surface_silver_mg_m2'].max()>dark['surface_silver_mg_m2'].max()
    assert light['surface_halide_mg_m2'].sum()<dark['surface_halide_mg_m2'].sum()
    np.testing.assert_allclose(light['surface_silver_mg_m2']+light['surface_halide_mg_m2'],dark['surface_silver_mg_m2']+dark['surface_halide_mg_m2'],atol=1e-6)
    assert a['events'][-1]['operation']['kind']=='surface_residual_light'
    for audit in (a,b):
        ledger=audit['balance']
        assert ledger['silver_supplied_mg_m2']==pytest.approx(sum(ledger[k] for k in ('silver_deposited_mg_m2','silver_discharged_mg_m2','surface_halide_remaining_mg_m2','surface_halide_fixed_mg_m2','surface_silver_removed_mg_m2')),abs=1e-6)
    c.chemistry.process_steps=(*c.chemistry.process_steps[:2],{'action':'fix_halide'})
    fixed,_=form(c);c.chemistry.post_process_light=None;unlit,_=form(c)
    for key in fixed:np.testing.assert_array_equal(fixed[key],unlit[key])


@pytest.mark.parametrize('spectral',[False,True])
def test_surface_light_frozen_archive_and_observation(tmp_path,spectral):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.core.engine import develop_negative
    from film_foundry.tests.test_spectral_grain import configuration,scene
    c=config()
    if spectral:
        p=configuration();c.film.spectral_material_profile=p.film.spectral_material_profile;c.scanner.spectral_observation_profile=p.scanner.spectral_observation_profile
    c.processing.material_tile_rows=7;c.processing.material_tile_threshold_megapixels=.0001
    new=develop_negative(scene(),c,rng=np.random.default_rng(3))
    old=deepcopy(c);old.film.time_response['model']='film_time_response_v1'
    previous=develop_negative(scene(),old,rng=np.random.default_rng(3))
    assert np.max(np.abs(new.optical_density_rgb-previous.optical_density_rgb))>1e-5
    assert new.metadata['film_process_model']['pool_totals']==previous.metadata['film_process_model']['pool_totals']
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    latent=tmp_path/'source.darkroom_latent.npz';run_request(WorkflowRequest('expose',source,latent,c))
    c.film.time_response=None
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'result',c))
    medium=load_developed_negative_npz(result.medium_path);fast=load_developed_medium_for_scan(result.medium_path)
    before={k:v.copy() for k,v in medium.process_surface.items()}
    assert medium.metadata['film_process_model']['process_surface']['balance']['surface_halide_printed_mg_m2']>0
    np.testing.assert_array_equal(scan_medium_output(medium,c).output_srgb,scan_medium_output(fast,c).output_srgb)
    for key in before:np.testing.assert_array_equal(before[key],medium.process_surface[key])


def test_material_ui_and_share_keep_explicit_version(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.film_time_editor import FilmTimeDialog
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    app=_application();c=config();c.film.time_response['model']='film_time_response_v1'
    dialog=FilmTimeDialog(c,material=True)
    try:
        assert not dialog.surface_light.isChecked()
        dialog.apply();assert dialog.response==c.film.time_response
        dialog.surface_light.setChecked(True);dialog.apply();c.film.time_response=dialog.response
        assert dialog.response['model']==RESPONSE_MODEL_V2
        restored=config_from_share_code(encode_recipe_share(c))
        assert restored.film.time_response==c.film.time_response
        from film_foundry.core.process_couplings import describe_process_couplings
        assert 'surface_salt_light' in {r['code'] for r in describe_process_couplings(c)['findings']}
    finally:dialog.close();app.processEvents()
