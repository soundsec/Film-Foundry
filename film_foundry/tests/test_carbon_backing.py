from copy import deepcopy
import numpy as np
import pytest
from film_foundry.contracts.carbon_backing import FIELDS, validate_carbon_backing
from film_foundry.contracts.process_events import EVENT_MODEL_V8, EVENT_MODEL_V7, validate_process_events
from film_foundry.core.process_operations import form_process_surface, surface_density_rows, BathContactRate
from film_foundry.tests.test_process_operations import config as base_config


def config(*events):
    cfg=base_config();cfg.film.carbon_backing=validate_carbon_backing({})
    cfg.chemistry.process_events={'model':EVENT_MODEL_V8,'events':list(events)} if events else None
    return cfg


def form(cfg):
    return form_process_surface(cfg,(24,36),np.random.default_rng(2))


PRE={'kind':'backing_condition','duration_s':60.}
WASH={'kind':'backing_wash','duration_s':120.,'water_changes':6.,'agitation':1.,'back_rubbing':1.}


def assert_balance(fields,amount=40.):
    assert all(np.isfinite(fields[key]).all() and np.min(fields[key])>=0 for key in FIELDS)
    assert sum(float(fields[key].mean(dtype=np.float64)) for key in FIELDS[:4])==pytest.approx(amount,abs=1e-5)


def test_backing_softening_washing_order_and_finite_redeposit():
    intact,_=form(config())
    softened,_=form(config(PRE))
    for key in FIELDS[:4]:np.testing.assert_array_equal(intact[key],softened[key])
    clean,audit=form(config(PRE,WASH))
    reversed_order,_=form(config(WASH,PRE))
    assert clean['backing_bound_mg_m2'].mean()<reversed_order['backing_bound_mg_m2'].mean()
    assert clean['backing_removed_mg_m2'].mean()>39.
    dirty={**WASH,'water_changes':0.,'emulsion_contact':1.,'distribution_variation':.8,'distribution_scale_mm':3.}
    deposited,_=form(config(PRE,dirty))
    assert deposited['backing_front_mg_m2'].mean()>15.
    assert not np.any(deposited['backing_removed_mg_m2'])
    assert np.ptp(deposited['backing_front_mg_m2'])>1.
    repeated,_=form(config(PRE,dirty,{**WASH,'front_rubbing':1.,'emulsion_contact':0.},WASH))
    assert repeated['backing_front_mg_m2'].mean()<deposited['backing_front_mg_m2'].mean()*.01
    assert repeated['backing_removed_mg_m2'].mean()>39.
    for fields in (intact,softened,clean,reversed_order,deposited,repeated):assert_balance(fields)
    assert audit['model']=='film_process_surface_v3'


def test_local_contact_zero_time_generic_rinse_and_auxiliary_are_distinct():
    local={**WASH,'radius_mm':5.,'water_changes':0.,'emulsion_contact':1.}
    fields,_=form(config(PRE,local))
    assert fields['backing_bound_mg_m2'][0,0]==40.
    assert fields['backing_front_mg_m2'][0,0]==0.
    assert fields['backing_front_mg_m2'][12,18]>1.
    stopped,_=form(config(PRE,{**local,'duration_s':0.}))
    assert np.all(stopped['backing_bound_mg_m2']==40.)
    rinsed,_=form(config(PRE,local,{'kind':'wash','duration_s':600.,'water_changes':30.}))
    np.testing.assert_array_equal(rinsed['backing_front_mg_m2'],fields['backing_front_mg_m2'])
    assert rinsed['backing_loose_mg_m2'].mean()<fields['backing_loose_mg_m2'].mean()
    cfg=config();cfg.chemistry.process_steps=({'action':'remove_auxiliary'},)
    intact,_=form(cfg);assert np.all(intact['backing_bound_mg_m2']==40.)
    for current in (fields,stopped,rinsed,intact):assert_balance(current)
    zero=config(PRE,WASH);zero.film.carbon_backing['initial_mg_m2']=0.
    empty,_=form(zero);assert not set(FIELDS)&set(empty)


def test_v8_preserves_v7_silver_dye_and_wash_is_shared_with_inhibitor():
    events=[{'kind':'silver_bath','after_bath':1},{'kind':'dye_bath','after_bath':2,'radius_mm':5.}]
    cfg=config(*events);cfg.film.carbon_backing=None
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    new,a=form(cfg);cfg.chemistry.process_events['model']=EVENT_MODEL_V7;old,b=form(cfg)
    for key in old:np.testing.assert_array_equal(new[key],old[key])
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    program=program_from_develop_recipe(cfg.chemistry)
    cfg.chemistry.process_events['model']=EVENT_MODEL_V8
    assert program==program_from_develop_recipe(cfg.chemistry)
    cfg=config({'kind':'developer_inhibitor'},PRE,WASH)
    washed=BathContactRate(cfg,(24,36)).inhibitor_audit
    cfg.chemistry.process_events['events'][-1]['water_changes']=0.
    dirty=BathContactRate(cfg,(24,36)).inhibitor_audit
    assert washed['remaining_mmol_m2']<dirty['remaining_mmol_m2']
    with pytest.raises(ValueError):validate_process_events({'model':EVENT_MODEL_V7,'events':[PRE]})
    with pytest.raises(ValueError):validate_process_events({'model':EVENT_MODEL_V8,'events':[{**PRE,'before_bath':1,'after_bath':1}]})
    with pytest.raises(ValueError,match='nonexistent'):form(config({**PRE,'before_bath':32}))


@pytest.mark.parametrize('spectral',[False,True])
def test_frozen_exposure_archive_optical_master_and_readonly_scan(tmp_path,spectral):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.tests.test_spectral_grain import configuration,scene
    cfg=configuration() if spectral else base_config()
    cfg.enable_grain=False;cfg.process_control_mode='principle'
    cfg.film.carbon_backing=validate_carbon_backing({})
    cfg.chemistry.process_events=config(PRE,{**WASH,'water_changes':0.,'emulsion_contact':1.,'radius_mm':7.}).chemistry.process_events
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    latent=tmp_path/'latent.darkroom_latent.npz';run_request(WorkflowRequest('expose',source,latent,cfg))
    shared=config_from_share_code(encode_recipe_share(cfg))
    assert shared.film.carbon_backing==cfg.film.carbon_backing
    cfg.film.carbon_backing=None # later settings cannot change frozen material
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'carbon',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    fields=medium.process_surface;audit=medium.metadata['film_process_model']['process_surface']
    assert_balance(fields);assert audit['carbon_backing']['initial_mg_m2']==40.
    assert not fields['backing_front_mg_m2'].flags.writeable
    saved=medium.optical_density_rgb.copy()
    full=scan_medium_output(medium,cfg).output_srgb
    scan_only=load_developed_medium_for_scan(result.medium_path)
    assert scan_only.process_surface is None
    np.testing.assert_array_equal(full,scan_medium_output(scan_only,cfg).output_srgb)
    np.testing.assert_array_equal(saved,medium.optical_density_rgb)
    # Compare one exposure with and without the explicit backing. Other pools
    # and density contributions must not be recomputed by the observer.
    from film_foundry.core.engine import develop_negative
    baseline=deepcopy(shared);baseline.film.carbon_backing=None
    a=develop_negative(scene(),shared,rng=np.random.default_rng(11))
    b=develop_negative(scene(),baseline,rng=np.random.default_rng(11))
    delta=surface_density_rows(a.process_surface,a.metadata['film_process_model']['process_surface'],scene().shape,0,31)
    np.testing.assert_allclose(a.optical_density_rgb-b.optical_density_rgb,delta,atol=2e-6)


def test_editors_roundtrip_scheduling_material_and_expected_mode(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor,ProcessResponseDialog
    from film_foundry.core.effect_intent import compile_effect_intent
    from film_foundry.core.process_couplings import describe_process_couplings
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();cfg=config(PRE,{**WASH,'after_bath':1})
    value=validate_process_events(cfg.chemistry.process_events)
    editor=ProcessOperationsEditor(value);material=ProcessResponseDialog(None,carbon_backing=cfg.film.carbon_backing)
    try:
        assert editor.read()==value
        editor.select(0);editor.position.setCurrentIndex(editor.position.findData('after_bath'))
        editor.controls['after_bath'].setValue(0)
        row=editor.read()['events'][0];assert row['after_bath']==0 and 'before_bath' not in row
        material.backing_controls['initial_mg_m2'].setValue(70.);material.apply()
        assert material.carbon_backing['initial_mg_m2']==70.
        from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
        cfg.film.carbon_backing=material.carbon_backing
        actual=FilmMaterialEditor(config=cfg)
        try:assert actual._config_from_controls().film.carbon_backing==material.carbon_backing
        finally:actual.root.destroy()
        assert 'carbon_backing_routing' in {x['code'] for x in describe_process_couplings(cfg)['findings']}
        from film_foundry.contracts.effect_intent import validate_effect_intent
        cfg.chemistry.effect_intent=validate_effect_intent({'model':'process_effect_intent_v1','enabled':True,'amounts':{'chemical_stain':.3}})
        compiled,_=compile_effect_intent(cfg)
        assert compiled.chemistry.process_events['model']==EVENT_MODEL_V8
        assert compiled.chemistry.process_events['events'][:2]==value['events']
    finally:
        editor.close();material.close();app.processEvents()


def test_prebath_clock_and_front_deposit_absorbs_silver_reflection():
    from types import SimpleNamespace
    from film_foundry.core.film_surface_optics import observe_surface
    from film_foundry.core.material_family import ViewingConditions
    cfg=config(PRE,{**WASH,'water_changes':0.,'emulsion_contact':1.},{'kind':'silver_bath'})
    cfg.film.process_response={'model':'creative_process_response_v4'}
    cfg.chemistry.process_steps=({'action':'remove_auxiliary','time_min':1.},)
    fields,audit=form(cfg)
    # (60s+10s) first bath + (60s+10s) prebath + two (120s+10s) contacts.
    assert audit['wet_seconds']==400.
    optical=surface_density_rows(fields,audit,(24,36),0,24)+.3
    medium=SimpleNamespace(process_surface=fields,optical_density_rgb=optical,metadata={'film_process_model':{'process_surface':audit}})
    dark=observe_surface(medium,ViewingConditions(background_rgb=(0.,0.,0.)))
    clean=deepcopy(medium);clean.process_surface['backing_front_mg_m2'].fill(0.)
    clear=observe_surface(clean,ViewingConditions(background_rgb=(0.,0.,0.)))
    assert np.all(dark<clear*.1)
