from copy import deepcopy
import numpy as np
import pytest
from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity,EMULSION_COMPONENTS
from film_foundry.contracts.process_events import EVENT_MODEL_V9,validate_process_events
from film_foundry.tests.test_process_operations import config as original_config
from film_foundry.core.process_operations import form_process_surface

WEAR={'kind':'emulsion_wear','pressure':.7,'duration_s':30.}


def config(*events):
    cfg=original_config();cfg.film.emulsion_integrity=validate_emulsion_integrity({})
    cfg.chemistry.process_events={'model':EVENT_MODEL_V9,'events':list(events)}
    return cfg


def form(cfg):return form_process_surface(cfg,(24,36),np.random.default_rng(3))


def test_wear_threshold_hardening_damage_and_irreversible_area():
    plain,_=form(config(WEAR))
    hard,_=form(config({'kind':'harden','concentration_g_l':20.,'duration_s':600.},WEAR))
    damaged,_=form(config({'kind':'temperature','temperature_c':60.,'transfer_s':0.},WEAR))
    assert hard['emulsion_coverage'].mean()>plain['emulsion_coverage'].mean()>damaged['emulsion_coverage'].mean()
    repeated,audit=form(config(WEAR,WEAR,{'kind':'wash'}))
    assert np.all(repeated['emulsion_coverage']<=plain['emulsion_coverage'])
    assert audit['balance']['emulsion_retained_area_fraction']+audit['balance']['emulsion_removed_area_fraction']==pytest.approx(1.)
    local,_=form(config({**WEAR,'radius_mm':5.}))
    assert local['emulsion_coverage'][0,0]==1. and local['emulsion_coverage'][12,18]<1.
    for key in ('pressure','duration_s'):
        untouched,_=form(config({**WEAR,key:0.}));assert np.all(untouched['emulsion_coverage']==1.)
    unknown=config(WEAR);unknown.film.emulsion_integrity=None
    with pytest.raises(ValueError,match='declared'):form(unknown)
    for position in ({'before_bath':1},{'after_bath':1}):
        with pytest.raises(ValueError,match='after all'):validate_process_events({'model':EVENT_MODEL_V9,'events':[{**WEAR,**position}]})


def test_existing_front_deposits_leave_but_later_deposits_and_backing_remain():
    from film_foundry.contracts.carbon_backing import validate_carbon_backing,FIELDS
    from film_foundry.tests.test_carbon_backing import PRE,WASH
    silver={'kind':'silver_bath'};stain={'kind':'contaminate','contact_s':600.}
    cfg=config(silver,stain,WEAR);before,b=form(cfg)
    cfg.chemistry.process_events['events']=[WEAR,silver,stain];after,a=form(cfg)
    assert before['surface_silver_mg_m2'].mean()<after['surface_silver_mg_m2'].mean()
    assert before['bound_residue_mg_m2'].mean()<after['bound_residue_mg_m2'].mean()
    assert b['balance']['silver_supplied_mg_m2']==pytest.approx(b['balance']['silver_discharged_mg_m2']+b['balance']['silver_deposited_mg_m2']+b['balance']['surface_silver_removed_mg_m2'],abs=1e-6)
    cfg=config(WEAR);cfg.film.carbon_backing=validate_carbon_backing({})
    retained,_=form(cfg);assert np.all(retained['backing_bound_mg_m2']==40.)
    cfg.chemistry.process_events['events']=[PRE,{**WASH,'water_changes':0.,'emulsion_contact':1.},WEAR]
    fields,_=form(cfg)
    assert fields['backing_removed_mg_m2'].mean()>30.
    assert sum(float(fields[key].mean(dtype=np.float64)) for key in FIELDS[:4])==pytest.approx(40.,abs=1e-5)
    # Once the coating is gone, new front deposits belong to bare support.
    strip={**WEAR,'pressure':1.,'duration_s':600.}
    cfg=config(strip,silver,stain);cfg.film.emulsion_integrity['adhesion']=0.
    bare,_=form(cfg);cfg.chemistry.process_events['events'].append(strip)
    again,_=form(cfg)
    assert not np.any(again['emulsion_coverage'])
    np.testing.assert_array_equal(bare['surface_silver_mg_m2'],again['surface_silver_mg_m2'])
    np.testing.assert_array_equal(bare['bound_residue_mg_m2'],again['bound_residue_mg_m2'])
    assert not np.any(again['emulsion_silver_mg_m2'])
    cfg.film.carbon_backing=validate_carbon_backing({})
    cfg.chemistry.process_events['events']=[strip,PRE,{**WASH,'after_bath':0,'water_changes':0.,'emulsion_contact':1.}]
    carbon_on_base,_=form(cfg);cfg.chemistry.process_events['events'].append(strip)
    carbon_again,_=form(cfg)
    np.testing.assert_array_equal(carbon_on_base['backing_front_mg_m2'],carbon_again['backing_front_mg_m2'])


def test_fractional_coverage_mixes_transmission_in_rgb_and_spectrum():
    from film_foundry.core.emulsion_integrity import mix_density
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.spectral_optics import SpectralMedium,capture_spectral_medium
    c=np.full((3,5),.5,np.float32);intact=np.full((3,5,3),2.,np.float32);support=np.full(3,.1,np.float32)
    out=mix_density(intact,support,c)
    np.testing.assert_allclose(10**(-out),.5*10**(-intact)+.5*10**(-support),rtol=2e-7)
    assert np.max(out)<.5*(2.+.1) # density scaling would make partial holes too dark
    cfg=configuration();cfg.enable_grain=False
    medium=develop_negative(scene(),cfg,rng=np.random.default_rng(1)).spectral_medium
    coverage=np.full(medium.component_amounts.shape[:2],.35,np.float32)
    mixed=SpectralMedium(medium.profile,medium.component_amounts,medium.base_component_amounts,emulsion_coverage=coverage)
    bare=medium.component_amounts.copy();bare[...,list(EMULSION_COMPONENTS)]=0.
    support_medium=SpectralMedium(medium.profile,bare,medium.base_component_amounts)
    expected=.35*capture_spectral_medium(medium,cfg.scanner.spectral_observation_profile)+.65*capture_spectral_medium(support_medium,cfg.scanner.spectral_observation_profile)
    np.testing.assert_allclose(capture_spectral_medium(mixed,cfg.scanner.spectral_observation_profile),expected,rtol=3e-6,atol=1e-7)
    retained=mixed.retained_component_rows(slice(None))
    np.testing.assert_array_equal(retained[...,6],medium.component_amounts[...,6])
    np.testing.assert_array_equal(retained[...,0],medium.component_amounts[...,0]*coverage)


@pytest.mark.parametrize('spectral',[False,True])
def test_real_latent_freeze_archive_scan_preview_and_bare_support(tmp_path,spectral):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan,probe_scan_archive_memory
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.app.preview_inputs import preview_medium
    cfg=configuration() if spectral else original_config();cfg.enable_grain=False;cfg.process_control_mode='principle'
    cfg.film.emulsion_integrity=validate_emulsion_integrity({'adhesion':0.})
    cfg.chemistry.process_events=config({**WEAR,'duration_s':600.,'radius_mm':5.}).chemistry.process_events
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    latent=tmp_path/'latent.darkroom_latent.npz';run_request(WorkflowRequest('expose',source,latent,cfg))
    assert config_from_share_code(encode_recipe_share(cfg)).film.emulsion_integrity==cfg.film.emulsion_integrity
    cfg.film.emulsion_integrity=None
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'out',cfg))
    medium=load_developed_negative_npz(result.medium_path);audit=medium.metadata['film_process_model']['process_surface']
    assert not medium.process_surface['emulsion_coverage'].flags.writeable
    np.testing.assert_allclose(medium.optical_density_rgb[15,23],audit['ownership']['support_density_rgb'],atol=1e-6)
    saved=medium.optical_density_rgb.copy();scan_only=load_developed_medium_for_scan(result.medium_path)
    np.testing.assert_array_equal(scan_medium_output(medium,cfg).output_srgb,scan_medium_output(scan_only,cfg).output_srgb)
    np.testing.assert_array_equal(saved,medium.optical_density_rgb)
    assert scan_only.process_surface is None
    assert probe_scan_archive_memory(result.medium_path)[:2]==(47,31)
    preview=preview_medium(medium,20);assert preview.frame_shape[:2]==(13,20)
    if spectral:
        spec=medium.spectral_medium
        assert spec.emulsion_coverage[15,23]==0.
        retained=spec.retained_component_rows(slice(None))
        for index in EMULSION_COMPONENTS:
            assert retained[15,23,index]==0.
            lost=np.sum(spec.component_amounts[...,index]-retained[...,index],dtype=np.float64)
            assert lost==pytest.approx(audit['ownership']['removed_spectral_component_sums'][index],abs=1e-5)
        assert preview.spectral_medium.emulsion_coverage.shape==(13,20)


def test_material_and_event_editors_reach_the_public_recipe(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor,ProcessResponseDialog
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();cfg=config(WEAR);value=validate_process_events(cfg.chemistry.process_events)
    operations=ProcessOperationsEditor(value);material=ProcessResponseDialog(None,emulsion_integrity=cfg.film.emulsion_integrity)
    try:
        assert operations.read()==value
        assert operations.controls['after_bath'].maximum()==0
        material.integrity_controls['adhesion'].setValue(.2);material.apply()
        cfg.film.emulsion_integrity=material.emulsion_integrity
        editor=FilmMaterialEditor(config=cfg)
        try:assert editor._config_from_controls().film.emulsion_integrity['adhesion']==.2
        finally:editor.root.destroy()
        cfg.chemistry.effect_intent={'model':'process_effect_intent_v1','amounts':{'chemical_stain':.1}}
        compiled,_=compile_effect_intent(cfg)
        assert compiled.chemistry.process_events['model']==EVENT_MODEL_V9
        assert compiled.chemistry.process_events['events'][0]==value['events'][0]
    finally:operations.close();material.close();app.processEvents()


def test_carbon_and_legacy_overlay_archive_keep_two_optical_locations(tmp_path):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.tests.test_carbon_backing import PRE,WASH,config as carbon_config
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.spectral_optics import capture_spectral_medium
    cfg=configuration();cfg.enable_grain=False;cfg.process_control_mode='normal'
    cfg.random_seed=5;cfg.seed_strategy='fixed';cfg.chemistry.chemical_stain=.3
    cfg.film.emulsion_integrity=config().film.emulsion_integrity
    cfg.film.carbon_backing=carbon_config().film.carbon_backing
    cfg.chemistry.process_events=config(PRE,WASH,WEAR,{'kind':'silver_bath'}).chemistry.process_events
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'result.png',cfg))
    full=load_developed_negative_npz(result.medium_path);fast=load_developed_medium_for_scan(result.medium_path)
    assert 'backing_bound_mg_m2' in full.process_surface and 'emulsion_coverage' in full.process_surface
    assert full.spectral_medium.covered_density_rgb is not None
    assert np.any(full.spectral_medium.covered_density_rgb)
    assert np.any(full.spectral_medium.post_process_density_rgb)
    assert not np.shares_memory(full.spectral_medium.covered_density_rgb,full.spectral_medium.post_process_density_rgb)
    np.testing.assert_array_equal(capture_spectral_medium(full.spectral_medium,cfg.scanner.spectral_observation_profile),capture_spectral_medium(fast.spectral_medium,cfg.scanner.spectral_observation_profile))
