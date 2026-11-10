from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import numpy as np
import pytest
from film_foundry.model.config import DarkroomConfig
from film_foundry.contracts.process_events import EVENT_MODEL,validate_process_events
from film_foundry.core.process_operations import form_process_surface,BathContactRate

def config(*events):
    cfg=DarkroomConfig(enable_grain=False,enable_mtf=False,enable_halation=False,seed_strategy='fixed',random_seed=14)
    cfg.process_control_mode='principle';cfg.chemistry.incident_identity_seed=7
    cfg.chemistry.process_events={'model':EVENT_MODEL,'events':list(events)} if events else None
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    return cfg

def form(cfg):return form_process_surface(cfg,(48,72,3),np.random.default_rng(5))

def test_quantity_balance_and_order_of_contamination_and_washing():
    dirt={'kind':'contaminate','concentration_mg_l':1500.,'retained_ml_m2':20.,'contact_s':40.}
    wash={'kind':'wash','duration_s':300,'water_changes':6}
    dirty,a=form(config(dirt));clean,b=form(config(dirt,wash));late,c=form(config(wash,dirt))
    assert b['balance']['residue_remaining_mg_m2']<a['balance']['residue_remaining_mg_m2']
    np.testing.assert_array_equal(dirty['bound_residue_mg_m2'],clean['bound_residue_mg_m2'])
    assert c['balance']['residue_remaining_mg_m2']==pytest.approx(a['balance']['residue_remaining_mg_m2'])
    for audit in (a,b,c):
        balance=audit['balance'];assert balance['residue_added_mg_m2']==pytest.approx(balance['residue_remaining_mg_m2']+balance['residue_washed_mg_m2'],abs=1e-5)
    still,_=form(config(dirt,{**wash,'water_changes':0}))
    np.testing.assert_array_equal(still['soluble_residue_mg_m2'],dirty['soluble_residue_mg_m2'])

def test_thermal_history_hardening_and_no_healing():
    jump={'kind':'temperature','temperature_c':60.,'transfer_s':0.,'hold_s':30.}
    fast,_=form(config(jump));slow,_=form(config({**jump,'transfer_s':600.}))
    hardened,_=form(config({'kind':'harden','concentration_g_l':10.,'duration_s':120.},jump))
    washed,_=form(config(jump,{'kind':'wash','temperature_c':60.}))
    constant,_=form(config({'kind':'temperature','temperature_c':20.,'transfer_s':0.}))
    assert fast['emulsion_damage'].mean()>slow['emulsion_damage'].mean()
    assert fast['emulsion_damage'].mean()>hardened['emulsion_damage'].mean()
    np.testing.assert_array_equal(washed['emulsion_damage'],fast['emulsion_damage'])
    assert not np.any(constant['emulsion_damage'])
    cfg=config();cfg.chemistry.process_steps=({'action':'develop_silver','temperature_c':20.},
        {'action':'fix_halide','temperature_c':60.,'bath_transfer_s':0.})
    actual,audit=form(cfg);assert np.any(actual['emulsion_damage']) and audit['events'][0]['operation']['kind']=='bath_temperature'

def test_silver_requires_external_mass_and_reducing_capacity():
    event={'kind':'silver_bath','silver_mg_l':1000.,'solution_ml_m2':100.,'reducer_mmol_l':.1,'duration_s':600.}
    fields,audit=form(config(event,{'kind':'wash'}));balance=audit['balance']
    assert 0<fields['surface_silver_mg_m2'].mean()<=.1*.1*107.8682
    assert balance['silver_supplied_mg_m2']==pytest.approx(balance['silver_deposited_mg_m2']+balance['silver_discharged_mg_m2'])
    original,_=form(config(event));np.testing.assert_array_equal(fields['surface_silver_mg_m2'],original['surface_silver_mg_m2'])
    for key in ('silver_mg_l','reducer_mmol_l','duration_s'):
        zero,_=form(config({**event,key:0.}));assert not np.any(zero['surface_silver_mg_m2'])

def test_bath_contact_changes_actual_pools_and_is_tile_invariant():
    from film_foundry.tests.test_custom_process_steps import _material,_latent
    from film_foundry.core.film_process.operators import apply_process_program
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    cfg=config({'kind':'bath_contact','bath':1,'immersion_fraction':.5,'angle_degrees':0.})
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'fix_halide'})
    program=program_from_develop_recipe(cfg.chemistry)
    result=apply_process_program(_material(),_latent(),program,local_development_rate=BathContactRate(cfg,(1,3)))
    assert np.any(result.state.dye[0,0]) and not np.any(result.state.dye[0,2])
    # Incomplete fixing leaves actual halide, never a screen-space haze.
    cfg.chemistry.process_events['events'][0]['bath']=2
    result=apply_process_program(_material(),_latent(),program,local_development_rate=BathContactRate(cfg,(1,3)))
    assert np.all(result.state.halide[0,0]==0) and np.any(result.state.halide[0,2]>0)
    rate=BathContactRate(cfg,(13,19));full=rate.contact_for_step(1,program.steps[1])
    tiles=np.concatenate([rate.slice_rows(a,b).contact_for_step(1,program.steps[1]) for a,b in ((0,4),(4,9),(9,13))])
    np.testing.assert_array_equal(full,tiles)

def test_events_save_reload_observe_and_keep_exposed_material_response(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import load_developed_medium
    from film_foundry.core.scan_runtime import scan_medium_output
    cfg=config({'kind':'contaminate','concentration_mg_l':2000.,'retained_ml_m2':20.},
        {'kind':'silver_bath'}, {'kind':'temperature','temperature_c':60.,'transfer_s':0.})
    from film_foundry.contracts.process_events import EVENT_MODEL_V2
    cfg.chemistry.process_events['model']=EVENT_MODEL_V2
    cfg.chemistry.process_events['events'][0]['after_bath']=1
    image=tmp_path/'source.png';Image.fromarray(np.full((24,36,3),110,np.uint8)).save(image)
    latent=tmp_path/'source.darkroom_latent.npz';run_request(WorkflowRequest('expose',image,latent,cfg))
    # A later workspace material must not alter the frozen exposed response.
    cfg.film.process_response={'structure_density':0.,'residue_density_rgb_per_mg_m2':(0,0,0)}
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'out',cfg))
    from film_foundry.core.negative_io import load_developed_negative_npz
    medium=load_developed_negative_npz(result.medium_path)
    assert medium.process_surface is not None
    audit=medium.metadata['film_process_model']['process_surface']
    assert audit['response']['structure_density']==.1
    assert np.any(medium.process_surface['surface_silver_mg_m2'])
    assert not medium.process_surface['emulsion_damage'].flags.writeable
    stamp=Path(result.medium_path).stat().st_mtime_ns;optical=medium.optical_density_rgb.copy()
    first=scan_medium_output(medium,cfg).output_srgb;second=scan_medium_output(medium,cfg).output_srgb
    np.testing.assert_array_equal(first,scan_medium_output(load_developed_medium(result.medium_path),cfg).output_srgb)
    np.testing.assert_array_equal(first,second);np.testing.assert_array_equal(optical,medium.optical_density_rgb)
    assert Path(result.medium_path).stat().st_mtime_ns==stamp
    assert medium.metadata['process_control']['mode']=='principle'
    assert medium.metadata['film_process_model']['process_operations']['model']==EVENT_MODEL_V2

def test_no_events_preserve_result_and_bad_events_rejected():
    from film_foundry.core.engine import develop_negative
    image=np.full((20,30,3),.4,np.float32);cfg=config();before=asdict(cfg)
    first=develop_negative(image,cfg,rng=np.random.default_rng(9))
    cfg.chemistry.process_events={'model':EVENT_MODEL,'events':[]}
    other=develop_negative(image,cfg,rng=np.random.default_rng(9))
    np.testing.assert_array_equal(first.optical_density_rgb,other.optical_density_rgb)
    assert first.process_surface is None
    cfg.chemistry.process_events=None;assert asdict(cfg)==before
    for raw in ({'model':'unknown','events':[]},{'model':EVENT_MODEL,'events':[{'kind':'wash','duration_s':float('nan')}]},
                {'model':EVENT_MODEL,'events':[{'kind':'wash'},{'kind':'bath_contact'}]}):
        with pytest.raises(ValueError):validate_process_events(raw)

def test_operation_editor_roundtrip_and_material_response(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application,QtWidgets
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.ui.process_operations_editor import ProcessResponseDialog
    for name in ('UI','CAMERA','CATALOG'):
        key='FILM_FOUNDRY_'+('CATALOG' if name=='CATALOG' else name+'_SETTINGS')
        monkeypatch.setenv(key,str(tmp_path/(name+'.json')))
    _application();dialog=RollProcessDialog(config({'kind':'contaminate'},{'kind':'wash'}))
    try:
        dialog.operations.select(0);dialog.operations.controls['concentration_mg_l'].setValue(2500)
        dialog.operations.list.setCurrentRow(1);dialog.apply()
        assert dialog.result()==QtWidgets.QDialog.DialogCode.Accepted
        assert dialog.config.chemistry.process_events['events'][0]['concentration_mg_l']==2500
        response=ProcessResponseDialog(None);response.controls['initial_hardening'].setValue(.8);response.apply()
        assert response.value['initial_hardening']==.8;response.close()
        from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
        cfg=config();cfg.film.process_response=response.value
        editor=FilmMaterialEditor(config=cfg)
        try:assert editor._config_from_controls().film.process_response==response.value
        finally:editor.root.destroy()
    finally:dialog.close()

def test_full_spectral_file_and_tiled_contact_preserve_material_state(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.tests.test_spectral_grain import configuration,scene
    cfg=configuration();cfg.enable_grain=False;cfg.process_control_mode='principle'
    cfg.seed_strategy='fixed';cfg.random_seed=14;cfg.chemistry.incident_identity_seed=7
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    plain=develop_negative(scene(),cfg,rng=np.random.default_rng(14))
    cfg.chemistry.process_events={'model':EVENT_MODEL,'events':[
        {'kind':'contaminate','radius_mm':8.}, {'kind':'silver_bath'},
        {'kind':'temperature','temperature_c':60.,'transfer_s':0.}]}
    decorated=develop_negative(scene(),cfg,rng=np.random.default_rng(14))
    np.testing.assert_array_equal(plain.density_cmy,decorated.density_cmy)
    np.testing.assert_array_equal(plain.spectral_medium.component_amounts,decorated.spectral_medium.component_amounts)
    assert np.max(abs(plain.optical_density_rgb-decorated.optical_density_rgb))>.01
    assert 'process_surface' in decorated.metadata['film_process_model']['spectral_post_process_formation']['source_components']
    rgb=deepcopy(cfg);rgb.film.spectral_material_profile=None;rgb.scanner.spectral_observation_profile=None
    np.testing.assert_array_equal(decorated.optical_density_rgb,develop_negative(scene(),rgb,rng=np.random.default_rng(14)).optical_density_rgb)
    source=tmp_path/'source.png';Image.fromarray(np.uint8(np.rint(scene()*255))).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'full.png',cfg))
    assert result.output_path.exists() and result.medium_path.exists()
    restored=load_developed_negative_npz(result.medium_path)
    scan_only=load_developed_medium_for_scan(result.medium_path)
    assert restored.process_surface is not None
    np.testing.assert_array_equal(scan_medium_output(restored,cfg).output_srgb,scan_medium_output(scan_only,cfg).output_srgb)
    cfg.chemistry.process_events={'model':EVENT_MODEL,'events':[{'kind':'bath_contact','bath':1,'immersion_fraction':.6,'fill_s':30.}]}
    tiled=develop_negative(scene(),cfg,rng=np.random.default_rng(14))
    cfg.processing.material_tile_threshold_megapixels=100
    full=develop_negative(scene(),cfg,rng=np.random.default_rng(14))
    np.testing.assert_allclose(tiled.optical_density_rgb,full.optical_density_rgb,atol=1e-6,rtol=1e-6)
    assert np.max(abs(tiled.density_cmy-plain.density_cmy))>.01

def test_surface_archive_rejects_missing_fields_and_unknown_version(tmp_path):
    from film_foundry.contracts.process_events import SURFACE_FIELDS
    from film_foundry.core.process_operations import load_surface_fields,surface_archive_header
    fields,audit=form(config({'kind':'contaminate'}))
    metadata={'film_process_model':{'process_surface':audit}}
    path=tmp_path/'surface.npz'
    np.savez(path,**{f'process_surface_{key}':value for key,value in fields.items()})
    with np.load(path,allow_pickle=False) as data:
        broken=deepcopy(metadata);broken['film_process_model']['process_surface']['model']='unknown'
        with pytest.raises(ValueError,match='model'):surface_archive_header(data,broken)
        broken=deepcopy(metadata);broken['film_process_model']['process_surface']['grid_shape']=[1,1]
        with pytest.raises(ValueError,match='grid'):load_surface_fields(data,broken)
    np.savez(path,**{f'process_surface_{key}':fields[key] for key in SURFACE_FIELDS[:-1]})
    with np.load(path,allow_pickle=False) as data:
        with pytest.raises(ValueError,match='Incomplete'):surface_archive_header(data,metadata)

def test_between_bath_operations_obey_causal_order_and_validate_positions():
    from film_foundry.contracts.process_events import EVENT_MODEL_V2
    cfg=config();cfg.chemistry.process_steps=({'action':'develop_silver','temperature_c':20.},
        {'action':'fix_halide','temperature_c':60.,'bath_transfer_s':0.})
    harden={'kind':'harden','concentration_g_l':10.,'duration_s':120.}
    cfg.chemistry.process_events={'model':EVENT_MODEL_V2,'events':[{**harden,'after_bath':1}]}
    protected,audit=form(cfg)
    cfg.chemistry.process_events['events'][0]['after_bath']=2
    late,_=form(cfg)
    assert protected['emulsion_damage'].mean()<late['emulsion_damage'].mean()
    assert audit['events'][0]['operation']['kind']=='harden'
    assert audit['events'][1]['operation']['kind']=='bath_temperature'
    cfg.chemistry.process_events['events']=[{'kind':'wash','after_bath':2,'temperature_c':60.},
        {'kind':'contaminate','after_bath':1}]
    cleaned,audit=form(cfg)
    assert [e['operation']['kind'] for e in audit['events']]==['contaminate','bath_temperature','wash']
    assert audit['balance']['residue_washed_mg_m2']>0
    cfg.chemistry.process_events['events'][0]['after_bath']=3
    with pytest.raises(ValueError,match='nonexistent'):form(cfg)
    cfg.chemistry.process_events={'model':EVENT_MODEL_V2,'events':[{'kind':'silver_bath','after_bath':1}]}
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    with pytest.raises(ValueError,match='silver-removal'):form(cfg)

def test_inhibitor_changes_formation_and_wash_only_restores_later_reaction(tmp_path):
    from film_foundry.contracts.process_events import EVENT_MODEL_V3
    from film_foundry.core.engine import develop_negative
    cfg=config();cfg.chemistry.process_steps=({'action':'develop_color','time_min':1.},
        {'action':'develop_color','time_min':8.},{'action':'bleach_silver'},{'action':'fix_halide'})
    scene=np.full((24,36,3),.35,np.float32)
    plain=develop_negative(scene,cfg,rng=np.random.default_rng(4))
    cfg.chemistry.process_events={'model':EVENT_MODEL_V3,'events':[{'kind':'developer_inhibitor',
        'before_bath':1,'concentration_mmol_l':100.,'radius_mm':8.}]}
    dirty=develop_negative(scene,cfg,rng=np.random.default_rng(4))
    unchanged=BathContactRate(cfg,scene.shape)
    assert unchanged.inhibitor_fields[0] is unchanged.inhibitor_fields[1]
    assert unchanged.inhibitor_audit['cached_fields']==1
    cfg.chemistry.process_events['events'].append({'kind':'wash','after_bath':1,'water_changes':10,'duration_s':1000})
    cleaned=develop_negative(scene,cfg,rng=np.random.default_rng(4))
    assert np.sum(dirty.density_cmy)<np.sum(cleaned.density_cmy)<np.sum(plain.density_cmy)
    audit=cleaned.metadata['film_process_model']['bath_inhibitor']
    assert audit['added_mmol_m2']==pytest.approx(audit['washed_mmol_m2']+audit['remaining_mmol_m2'],abs=1e-6)
    assert audit['baths'][1]['mean_activity_fraction']>audit['baths'][0]['mean_activity_fraction']
    assert dirty.process_surface is None
    provider=BathContactRate(cfg,(2400,3600));tile=provider.slice_rows(0,8)
    assert tile.inhibitor_fields is provider.inhibitor_fields
    assert max(provider.inhibitor_fields[0].shape)<=512
    assert provider.inhibitor_audit['cached_bytes']<=2*512*512*4
    cfg.processing.material_tile_rows=7;cfg.processing.material_tile_threshold_megapixels=.0001
    tiled=develop_negative(scene,cfg,rng=np.random.default_rng(4))
    np.testing.assert_allclose(cleaned.optical_density_rgb,tiled.optical_density_rgb,atol=1e-6,rtol=1e-6)
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene*255)).save(source)
    result=run_request(WorkflowRequest('develop',source,tmp_path/'medium.npz',cfg))
    restored=load_developed_negative_npz(result.medium_path)
    assert restored.metadata['film_process_model']['bath_inhibitor']['model']=='mobile_development_inhibitor_v1'

def test_inhibitor_zero_identity_ui_and_version():
    from film_foundry.contracts.process_events import EVENT_MODEL_V3
    from film_foundry.core.engine import develop_negative
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    cfg=config();scene=np.full((16,24,3),.4,np.float32)
    plain=develop_negative(scene,cfg,rng=np.random.default_rng(1))
    cfg.chemistry.process_events={'model':EVENT_MODEL_V3,'events':[{'kind':'developer_inhibitor','concentration_mmol_l':0}]}
    zero=develop_negative(scene,cfg,rng=np.random.default_rng(1))
    np.testing.assert_array_equal(plain.optical_density_rgb,zero.optical_density_rgb)
    assert zero.metadata['film_process_model']['bath_inhibitor']['cached_bytes']==0
    _application();editor=ProcessOperationsEditor(cfg.chemistry.process_events)
    try:
        editor.controls['before_bath'].setValue(2)
        value=editor.read();assert value['model']==EVENT_MODEL_V3 and value['events'][0]['before_bath']==2
    finally:editor.close()
    cfg.chemistry.process_events['model']=EVENT_MODEL
    with pytest.raises(ValueError,match='v3'):validate_process_events(cfg.chemistry.process_events)
