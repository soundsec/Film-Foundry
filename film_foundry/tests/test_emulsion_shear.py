"""Horizontal material transport, chemistry order, archival and UI boundaries."""
from copy import deepcopy
import numpy as np
import pytest
from film_foundry.tests.test_ordered_emulsion import config as prior_config,run
from film_foundry.tests.test_emulsion_integrity import WEAR
from film_foundry.tests.test_custom_process_steps import _latent
from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity,OWNED_POOLS
from film_foundry.core.emulsion_shear import shift_rows,transport_state,transport_surface
from film_foundry.core.process_operations import BathContactRate,form_process_surface

SHEAR={'kind':'emulsion_shear','pressure':.9,'duration_s':40.,'travel_mm':3.,'band_width_mm':0.}


def config(*events):
    c=prior_config(*events);c.chemistry.process_events['model']='film_process_events_v11'
    c.film.emulsion_integrity=validate_emulsion_integrity({'model':'wet_emulsion_integrity_v2'})
    return c


def expanded_state(h=11,w=33):
    state=_latent()
    for name in ('halide','developability','metallic_silver','coupler','dye'):
        base=getattr(state,name)
        value=np.broadcast_to(base[:,1:2],(h,w,3)).copy()
        setattr(state,name,value)
    state.developability[:,w//3:2*w//3]=.95
    return state


def test_subpixel_area_transport_and_outside_inventory_do_not_wrap_or_return():
    field=np.zeros((1,5),np.float32);field[0,2]=1.
    np.testing.assert_array_equal(shift_rows(field,np.array([.5])),[[0,0,.5,.5,0]])
    state=expanded_state(1,5);state.halide[:]=0.;state.halide[0,-1]=1.
    state.developability[:]=.7;state.coupler[:]=0.
    transport_state(state,np.array([.5]))
    assert state.halide.sum()==pytest.approx(1.5)
    assert state.emulsion_outside['halide']==pytest.approx(1.5)
    np.testing.assert_allclose(state.developability[0,-1],.7)
    assert not np.any(state.halide[0,0])
    transport_state(state,np.array([-.5]))
    assert state.halide.sum()==pytest.approx(1.5)
    assert state.emulsion_outside['halide']==pytest.approx(1.5)


def test_surface_owned_deposits_move_and_bare_support_and_backing_stay():
    from film_foundry.contracts.carbon_backing import validate_carbon_backing,FIELDS
    c=config(SHEAR);c.film.carbon_backing=validate_carbon_backing({})
    fields,_=form_process_surface(c,(5,9),np.random.default_rng(4))
    fields['emulsion_coverage'][:]=1.
    for total,owned in OWNED_POOLS.items():
        if total not in fields:continue
        fields[total][:]=0.;fields[owned][:]=0.
        fields[owned][:,2]=2.;fields[total][:,2]=2.
        fields[total][:,5]=3. # Independent bare-support share.
    before=[fields[key].copy() for key in FIELDS[:2]]
    hard,report=transport_surface(fields,np.ones(5),(9.,5.),.4)
    for total,owned in OWNED_POOLS.items():
        assert np.all(fields[owned][:,3]==2.)
        assert np.all(fields[total][:,5]==3.)
        assert not np.any(fields[total][:,2])
    for key,value in zip(FIELDS[:2],before):np.testing.assert_array_equal(fields[key],value)
    assert hard==.4 and report['outside_area_fraction']==pytest.approx(1/9)


def test_shear_responds_to_material_and_baths_contact_the_new_position():
    c=config({**SHEAR,'before_bath':1})
    plain=BathContactRate(c,(11,33))
    hard=config({'kind':'harden','after_bath':0,'concentration_g_l':100.,'duration_s':600.},SHEAR)
    normal=config(SHEAR)
    assert np.max(BathContactRate(hard,(11,33)).shear_transitions[1])<np.max(BathContactRate(normal,(11,33)).shear_transitions[0])
    for key in ('pressure','duration_s','travel_mm'):
        zero=BathContactRate(config({**SHEAR,key:0.}),(11,33))
        assert not np.any(zero.shear_transitions[0])
    old=prior_config();old.chemistry.process_events=c.chemistry.process_events
    with pytest.raises(ValueError,match='v2'):BathContactRate(old,(11,33))
    # A fixer touches frame-left coordinates; moving latent-bearing material
    # first changes which halide it can remove, rather than moving its mask.
    fixer={'kind':'fixer_contact','before_bath':1,'center_x':.25,'radius_mm':5.,'duration_s':100.}
    shear={**SHEAR,'before_bath':1}
    a,b=run(config(shear,fixer),expanded_state()),run(config(fixer,shear),expanded_state())
    assert np.max(np.abs(a.state.dye-b.state.dye))>.005
    fixed=sum(step.reacted_amount for step in a.trace if step.action=='fix_halide')
    totals=a.state.totals();remain=sum(totals[key] for key in ('halide','metallic_silver','bleached_halide'))
    outside=sum(a.state.emulsion_outside.get(key,0.) for key in ('halide','metallic_silver','bleached_halide'))
    assert fixed+remain+outside==pytest.approx(11*33*3,abs=1e-4)


def test_primary_coverage_and_transport_are_full_width_and_tile_invariant():
    c=config({**SHEAR,'before_bath':1,'band_width_mm':.4},
        {**WEAR,'after_bath':1,'radius_mm':.3},
        {**SHEAR,'after_bath':2,'travel_mm':-1.5,'band_width_mm':.5})
    state=expanded_state(19,1030);provider=BathContactRate(c,(19,1030))
    whole=run(c,state,provider);coverage=provider.coverage_output.copy()
    np.testing.assert_array_equal(coverage,whole.state.emulsion_coverage)
    assert provider.prepared_surface[0]['emulsion_coverage'].shape!=coverage.shape
    pieces=[]
    for start,stop in ((0,8),(8,19)):
        piece=state.copy()
        for name in ('halide','developability','metallic_silver','coupler','dye'):
            setattr(piece,name,getattr(piece,name)[start:stop].copy())
        pieces.append(run(c,piece,provider.slice_rows(start,stop)))
    for name in ('halide','dye','emulsion_coverage'):
        np.testing.assert_array_equal(getattr(whole.state,name),np.concatenate([getattr(p.state,name) for p in pieces]))
    np.testing.assert_array_equal(coverage,provider.coverage_output)
    for key,value in whole.state.emulsion_outside.items():
        assert value==pytest.approx(sum(p.state.emulsion_outside[key] for p in pieces),abs=1e-5)


def test_engine_tiling_keeps_deformed_optical_sources_and_coverage():
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.core.engine import develop_negative
    c=configuration();c.enable_grain=False;c.process_control_mode='principle'
    c.film.emulsion_integrity=config().film.emulsion_integrity
    c.chemistry.process_events=config({**SHEAR,'before_bath':1,'band_width_mm':8.},
        {**WEAR,'after_bath':1,'radius_mm':5.}).chemistry.process_events
    c.processing.material_tile_threshold_megapixels=100.
    full=develop_negative(scene(),c,rng=np.random.default_rng(7))
    c.processing.material_tile_threshold_megapixels=.0001;c.processing.material_tile_rows=7
    tiled=develop_negative(scene(),c,rng=np.random.default_rng(7))
    np.testing.assert_array_equal(full.spectral_medium.emulsion_coverage,tiled.spectral_medium.emulsion_coverage)
    np.testing.assert_allclose(full.optical_density_rgb,tiled.optical_density_rgb,atol=1e-6,rtol=1e-6)
    np.testing.assert_allclose(full.spectral_medium.component_amounts,tiled.spectral_medium.component_amounts,atol=1e-6,rtol=1e-6)


@pytest.mark.parametrize('spectral',[False,True])
def test_archive_latent_recipe_and_readonly_observation(tmp_path,spectral):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.app.preview_inputs import preview_medium
    cfg=configuration() if spectral else config()
    cfg.enable_grain=False;cfg.process_control_mode='principle'
    cfg.film.emulsion_integrity=config().film.emulsion_integrity
    cfg.chemistry.process_events=config({**SHEAR,'before_bath':1},
        {'kind':'silver_bath','after_bath':1},
        {**SHEAR,'after_bath':2,'travel_mm':1.}).chemistry.process_events
    if spectral:
        from film_foundry.tests.test_carbon_backing import PRE,WASH
        from film_foundry.contracts.carbon_backing import validate_carbon_backing
        cfg.film.carbon_backing=validate_carbon_backing({})
        cfg.chemistry.process_events['events'][:0]=[PRE,{**WASH,'water_changes':0.,'emulsion_contact':1.}]
    cfg.chemistry.development_adjacency_strength=.2
    cfg.film.dir_material_response_matrix=((.1,.1,.1),(.1,.1,.1),(.1,.1,.1))
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    shared=config_from_share_code(encode_recipe_share(cfg))
    assert shared.film.emulsion_integrity['model']=='wet_emulsion_integrity_v2'
    assert shared.chemistry.process_events['model']=='film_process_events_v11'
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    latent=tmp_path/'latent.darkroom_latent.npz';run_request(WorkflowRequest('expose',source,latent,cfg))
    cfg.film.emulsion_integrity=None
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'out',cfg))
    medium=load_developed_negative_npz(result.medium_path);fast=load_developed_medium_for_scan(result.medium_path)
    audit=medium.metadata['film_process_model']['process_surface']
    assert audit['model']=='film_process_surface_v5'
    assert audit['ownership']['coordinate_displacement']=='horizontal_emulsion_shear_v1'
    assert audit['ownership']['outside_primary_pools']['halide']>0.
    assert not medium.process_surface['emulsion_shift_x_mm'].flags.writeable
    original=medium.optical_density_rgb.copy()
    np.testing.assert_array_equal(scan_medium_output(medium,cfg).output_srgb,scan_medium_output(fast,cfg).output_srgb)
    np.testing.assert_array_equal(original,medium.optical_density_rgb)
    assert preview_medium(medium,20).frame_shape[:2]==(13,20)
    if spectral:
        from film_foundry.tests.test_carbon_backing import assert_balance
        assert np.all(medium.spectral_medium.emulsion_coverage[:,0]==0.)
        assert_balance(medium.process_surface)
        assert audit['balance']['outside_backing_front_mg_m2']>0.


def test_actual_editors_opt_in_shear_without_upgrading_old_material(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor,ProcessResponseDialog
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();old=prior_config().film.emulsion_integrity
    dialog=ProcessResponseDialog(None,emulsion_integrity=old);editor=ProcessOperationsEditor()
    try:
        dialog.apply();assert dialog.emulsion_integrity==old
        dialog.close();dialog=ProcessResponseDialog(None,emulsion_integrity=old)
        dialog.shear_enabled.setChecked(True);dialog.shear_rate.setValue(.3);dialog.apply()
        assert dialog.emulsion_integrity['model']=='wet_emulsion_integrity_v2'
        assert dialog.emulsion_integrity['shear_rate_s']==.3
        editor.kind.setCurrentIndex(editor.kind.findData('emulsion_shear'));editor.add()
        editor.position.setCurrentIndex(editor.position.findData('before_bath'))
        editor.controls['travel_mm'].setValue(-2.5)
        value=editor.read();assert value['model']=='film_process_events_v11'
        assert value['events'][0]['before_bath']==1
        cfg=config();cfg.chemistry.process_events=value
        cfg.chemistry.effect_intent={'model':'process_effect_intent_v1','amounts':{'chemical_stain':.1}}
        compiled,_=compile_effect_intent(cfg)
        assert compiled.chemistry.process_events['model']=='film_process_events_v11'
        assert compiled.chemistry.process_events['events'][0]==value['events'][0]
    finally:dialog.close();editor.close();app.processEvents()
