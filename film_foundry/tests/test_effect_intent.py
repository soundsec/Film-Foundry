from copy import deepcopy
from dataclasses import asdict
import numpy as np
import pytest
from film_foundry.contracts.effect_intent import MODEL,EFFECTS
from film_foundry.core.effect_intent import compile_effect_intent
from film_foundry.core.process_controls import condition_driven_config
from film_foundry.core.process_operations import form_process_surface,BathContactRate
from film_foundry.tests.test_bath_solution import configured


def config(**amounts):
    cfg=configured();cfg.process_control_mode='expected';cfg.chemistry.incident_identity_seed=19
    cfg.chemistry.effect_intent={'model':MODEL,'enabled':True,'amounts':amounts}
    return cfg


@pytest.mark.parametrize('key',EFFECTS)
def test_material_response_is_linear_across_slider_range(key):
    measured=[]
    for amount in (0.,.25,.5,.75,1.):
        cfg=config(**{key:amount});compiled,audit=compile_effect_intent(cfg,(64,96))
        if key=='uneven_development':
            provider=BathContactRate(compiled,(64,96))
            measured.append(1-float(provider.inhibitor_fields[0].mean()) if provider.inhibitor_fields else 0.)
        else:
            fields,_=form_process_surface(compiled,(64,96),np.random.default_rng(1))
            name={'chemical_stain':'soluble_residue_mg_m2','silver_plating':'surface_silver_mg_m2','reticulation_strength':'emulsion_damage'}[key]
            measured.append(float(fields[name].mean()) if fields else 0.)
    assert measured[-1]>0
    np.testing.assert_allclose(measured,np.linspace(0,measured[-1],5),rtol=2e-4,atol=2e-5)


def test_off_zero_idempotence_and_explicit_operations_preserved():
    from film_foundry.core.engine import develop_negative
    from film_foundry.contracts.process_events import EVENT_MODEL_V4
    cfg=config(**{key:.7 for key in EFFECTS});cfg.chemistry.chemical_stain=.9
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[{'kind':'contaminate','concentration_mg_l':300.}]}
    before=asdict(cfg);compiled,audit=compile_effect_intent(cfg,(24,36))
    again,empty=compile_effect_intent(compiled,(24,36))
    assert asdict(again)==asdict(compiled) and empty is None and asdict(cfg)==before
    cfg.chemistry.effect_intent['enabled']=False
    off,_=compile_effect_intent(cfg);zero=deepcopy(cfg);zero.chemistry.effect_intent['enabled']=True;zero.chemistry.effect_intent['amounts']={}
    empty,_=compile_effect_intent(zero)
    assert off.chemistry.process_events==cfg.chemistry.process_events
    scene=np.full((24,36,3),.4,np.float32)
    np.testing.assert_array_equal(develop_negative(scene,off).optical_density_rgb,develop_negative(scene,empty).optical_density_rgb)


def test_compiled_principle_matches_expected_and_uses_frozen_material(tmp_path):
    from film_foundry.core.engine import develop_negative,expose_image_to_latent,develop_negative_from_latent
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    cfg=config(**{key:.6 for key in EFFECTS});scene=np.linspace(.02,.95,24*36*3,dtype=np.float32).reshape(24,36,3)
    expected=develop_negative(scene,cfg)
    compiled,audit=compile_effect_intent(cfg,(24,36));principle=condition_driven_config(compiled)
    actual=develop_negative(scene,principle)
    np.testing.assert_array_equal(expected.optical_density_rgb,actual.optical_density_rgb)
    np.testing.assert_array_equal(scan_medium_output(expected,cfg).output_srgb,scan_medium_output(actual,principle).output_srgb)
    assert expected.metadata['process_control']['effect_intent']['intent']['model']==MODEL
    restored=config_from_share_code(encode_recipe_share(cfg))
    assert restored.chemistry.effect_intent['amounts']==cfg.chemistry.effect_intent['amounts']
    cfg.film.spatial_reference_short_edge_mm=24.
    latent=expose_image_to_latent(scene,cfg)
    baseline=develop_negative_from_latent(latent.copy(),cfg)
    changed=deepcopy(cfg);changed.film.process_response={'model':'creative_process_response_v1','initial_hardening':1.,'thermal_expansion_per_c':0.}
    changed.chemistry.frame_long_edge_mm=120.
    frozen=develop_negative_from_latent(latent.copy(),changed)
    np.testing.assert_array_equal(baseline.optical_density_rgb,frozen.optical_density_rgb)


def test_new_mapping_removes_legacy_grain_amplification_and_unreachable_damage():
    from film_foundry.core.development import build_effective_development
    cfg=config(chemical_stain=.8,uneven_development=.8)
    cfg.chemistry.chemical_stain=.8;cfg.chemistry.uneven_development=.8
    old=build_effective_development(cfg.chemistry)
    compiled,_=compile_effect_intent(cfg)
    base=deepcopy(cfg);base.chemistry.chemical_stain=base.chemistry.uneven_development=0.
    assert build_effective_development(compiled.chemistry).grain_factor==build_effective_development(base.chemistry).grain_factor<old.grain_factor
    cfg=config(reticulation_strength=1.);cfg.film.process_response={'model':'creative_process_response_v1','thermal_expansion_per_c':0.}
    compiled,audit=compile_effect_intent(cfg)
    assert not audit['generated_events'] and 'damage_unreachable' in audit['targets']


@pytest.mark.parametrize('physical',(False,True))
def test_intent_archive_and_metadata_only_latent_inspection(tmp_path,physical):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.latent_io import inspect_latent_material
    from film_foundry.core.negative_io import load_developed_negative_npz
    cfg=config(chemical_stain=.5,uneven_development=.5)
    cfg.film.spatial_reference_short_edge_mm=24. if physical else None
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    source=tmp_path/'source.png';Image.fromarray(np.full((24,36,3),110,np.uint8)).save(source)
    latent=run_request(WorkflowRequest('expose',source,tmp_path/'sample.darkroom_latent.npz',cfg))
    material,geometry,shape=inspect_latent_material(latent.output_path)
    assert shape==(24,36)
    assert (geometry.full_shape==(24,36)) if physical else geometry is None
    assert material.parameters()['process_response']['initial_hardening']==.4
    result=run_request(WorkflowRequest('full',source,tmp_path/'scan.png',cfg))
    stored=load_developed_negative_npz(result.medium_path)
    assert stored.process_surface is not None
    assert stored.metadata['process_control']['effect_intent']['targets']['residue_mg_m2']==10.


def test_desktop_master_switch_and_event_inspector_roundtrip(tmp_path,monkeypatch):
    from film_foundry.ui.qt import _application
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    from film_foundry.contracts.process_events import EVENT_MODEL_V5
    for name in ('UI_SETTINGS','CAMERA_SETTINGS','CATALOG'):
        monkeypatch.setenv('FILM_FOUNDRY_'+name,str(tmp_path/(name+'.json')))
    app=_application();panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        panel.expert_mode.set(True);panel.chemical_stain.set(.5);panel.effect_intent_enabled.set(False)
        cfg=panel._config();assert cfg.chemistry.effect_intent['amounts']['chemical_stain']==.5
        assert not cfg.chemistry.effect_intent['enabled']
        panel._apply_loaded_config(cfg)
        assert not panel.effect_intent_enabled.get() and panel.chemical_stain.get()==.5
        cfg.chemistry.effect_intent['enabled']=True
        compiled,_=compile_effect_intent(cfg)
        editor=ProcessOperationsEditor(compiled.chemistry.process_events)
        try:assert editor.read()==compiled.chemistry.process_events
        finally:editor.close()
        old=deepcopy(cfg);old.chemistry.effect_intent=None;old.chemistry.chemical_stain=.3
        panel._apply_loaded_config(old)
        assert panel._config().chemistry.effect_intent is None
        assert not panel.effect_master_control.qt.isEnabled()
        panel._upgrade_effect_controls()
        assert panel._config().chemistry.effect_intent['amounts']['chemical_stain']==.3
    finally:panel.root.destroy();app.processEvents()


def test_packed_surface_observer_matches_individual_reads():
    import cv2
    from film_foundry.core.process_operations import surface_density_rows,prepare_surface_observer
    cfg,_=compile_effect_intent(config(**{key:.6 for key in EFFECTS}),(37,59))
    fields,audit=form_process_surface(cfg,(37,59),np.random.default_rng(4))
    h,w=73,119;gh,gw=fields['emulsion_damage'].shape
    xx=np.broadcast_to(((np.arange(w,dtype=np.float32)+.5)*gw/w-.5)[None,:],(h,w))
    yy=np.broadcast_to(((np.arange(h,dtype=np.float32)+.5)*gh/h-.5)[:,None],(h,w))
    def sample(key):return cv2.remap(fields[key],xx,yy,cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
    r=audit['response'];expected=(sample('soluble_residue_mg_m2')+sample('bound_residue_mg_m2'))[...,None]*np.asarray(r['residue_density_rgb_per_mg_m2'],np.float32)
    expected+=sample('surface_silver_mg_m2')[...,None]*r['silver_density_per_mg_m2']
    expected+=(sample('emulsion_damage')*sample('emulsion_texture'))[...,None]*r['structure_density']
    packed=prepare_surface_observer(fields)
    actual=np.concatenate([surface_density_rows(fields,audit,(h,w),a,min(a+13,h),prepared=packed) for a in range(0,h,13)])
    np.testing.assert_array_equal(actual,expected)


@pytest.mark.parametrize('half',(1e-5,100.))
def test_inhibitor_mapping_respects_sensitive_and_capacity_limited_materials(half):
    measured=[]
    for amount in (0.,.25,.5,.75,1.):
        cfg=config(uneven_development=amount)
        cfg.film.process_response={'model':'creative_process_response_v2','inhibitor_half_response_mmol_m2':half}
        compiled,audit=compile_effect_intent(cfg,(48,72))
        fields=BathContactRate(compiled,(48,72)).inhibitor_fields
        measured.append(1-float(fields[0].mean()) if fields else 0.)
    np.testing.assert_allclose(measured,np.linspace(0,measured[-1],5),rtol=2e-5,atol=2e-6)
    assert compiled.chemistry.process_events['events'][0]['concentration_mmol_l']<=10000.
