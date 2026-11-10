from copy import deepcopy
from dataclasses import asdict
import math
import numpy as np
import pytest

from film_foundry.contracts.effect_intent import MODEL,MODEL_V2,EFFECTS_V2
from film_foundry.contracts.process_events import EVENT_MODEL_V6,RESPONSE_MODEL_V3,validate_process_events
from film_foundry.core.effect_intent import compile_effect_intent
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.film_process.operators import apply_process_program,CompatibilityProfile
from film_foundry.core.process_operations import form_process_surface,BathContactRate
from film_foundry.tests.test_bath_solution import configured
from film_foundry.tests.test_custom_process_steps import _material,_latent


def config(amount=.5):
    cfg=configured();cfg.process_control_mode='expected'
    cfg.chemistry.effect_intent={'model':MODEL_V2,'enabled':True,'amounts':{'dye_damage_strength':amount}}
    return cfg


def test_dye_bath_consumes_only_surviving_dye_and_preserves_bath_identity():
    cfg=configured();cfg.chemistry.process_steps=({'action':'remove_auxiliary'},)
    initial=_latent();initial.dye[:]=.4;initial.metallic_silver[:]=.2;initial.halide[:]=.8
    plain=apply_process_program(_material(),initial,program_from_develop_recipe(cfg.chemistry)).state
    cfg.chemistry.process_events={'model':EVENT_MODEL_V6,'events':[{'kind':'dye_bath','concentration_mmol_l':2.,'duration_s':30.,'temperature_c':40.}]}
    result=apply_process_program(_material(),initial,program_from_develop_recipe(cfg.chemistry),compatibility=CompatibilityProfile(dye_attack_l_mmol_s=.003))
    np.testing.assert_allclose(result.state.dye,plain.dye*math.exp(-.003*2*30*2),rtol=2e-7)
    for key in ('halide','developability','metallic_silver','coupler'):
        np.testing.assert_array_equal(getattr(result.state,key),getattr(plain,key))
    assert result.state.masking_coupler_remaining==plain.masking_coupler_remaining
    assert result.trace[-1].dye_destruction_basis=='chemical' and result.trace[-1].reacted_amount>0
    cfg.chemistry.process_events['events'].append({'kind':'fixer_contact','before_bath':2})
    with pytest.raises(ValueError,match='nonexistent'):BathContactRate(cfg,(1,3))


@pytest.mark.parametrize('custom',(False,True))
def test_dye_intent_five_points_are_linear_and_zero_retains_previous_program(custom):
    outputs=[]
    for value in (0.,.25,.5,.75,1.):
        cfg=config(value)
        if not custom:cfg.chemistry.process_steps=None
        compiled,audit=compile_effect_intent(cfg,(24,36))
        state=apply_process_program(_material(),_latent(),program_from_develop_recipe(compiled.chemistry)).state
        outputs.append(float(state.dye.mean()))
        assert not any(step.chemical_contact for step in program_from_develop_recipe(compiled.chemistry).steps) if value==0 else audit['targets']['remaining_dye_loss_fraction']==.75*value
    np.testing.assert_allclose(outputs,np.asarray(outputs[0])*(1-.75*np.linspace(0,1,5)),rtol=2e-6)


def test_dye_intent_modes_freeze_response_and_have_no_empty_surface_pass():
    from film_foundry.core.engine import develop_negative,expose_image_to_latent,develop_negative_from_latent
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.scan_runtime import scan_medium_output
    cfg=config();scene=np.linspace(.02,.9,24*36*3,dtype=np.float32).reshape(24,36,3)
    compiled,_=compile_effect_intent(cfg,scene.shape[:2]);principle=condition_driven_config(compiled)
    a=develop_negative(scene,cfg);b=develop_negative(scene,principle)
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    np.testing.assert_array_equal(scan_medium_output(a,cfg).output_srgb,scan_medium_output(b,principle).output_srgb)
    assert a.process_surface is None
    combined=deepcopy(cfg);combined.chemistry.effect_intent['amounts']={key:.5 for key in EFFECTS_V2}
    resolved,_=compile_effect_intent(combined,scene.shape[:2])
    np.testing.assert_array_equal(develop_negative(scene,combined).optical_density_rgb,
        develop_negative(scene,condition_driven_config(resolved)).optical_density_rgb)
    latent=expose_image_to_latent(scene,cfg)
    baseline=develop_negative_from_latent(latent.copy(),cfg)
    cfg.film.process_response={'model':RESPONSE_MODEL_V3,'dye_attack_l_mmol_s':0.}
    changed=develop_negative_from_latent(latent.copy(),cfg)
    np.testing.assert_array_equal(baseline.optical_density_rgb,changed.optical_density_rgb)


def test_dye_zero_master_bypass_and_old_intent_compatibility():
    from film_foundry.core.engine import develop_negative
    cfg=config();cfg.chemistry.dye_damage_strength=.6
    cfg.chemistry.effect_intent['enabled']=False
    off,_=compile_effect_intent(cfg);zero,_=compile_effect_intent(config(0.))
    scene=np.full((12,18,3),.4,np.float32)
    np.testing.assert_array_equal(develop_negative(scene,off).optical_density_rgb,develop_negative(scene,zero).optical_density_rgb)
    cfg.chemistry.effect_intent={'model':MODEL,'enabled':True,'amounts':{}}
    old,_=compile_effect_intent(cfg);assert old.chemistry.dye_damage_strength==.6
    with pytest.raises(ValueError):validate_process_events({'model':'film_process_events_v5','events':[{'kind':'dye_bath'}]})
    cfg=config();cfg.film.process_response={'model':RESPONSE_MODEL_V3,'dye_attack_l_mmol_s':0.}
    resolved,audit=compile_effect_intent(cfg);assert not audit['generated_events'] and 'dye_unreachable' in audit['targets']
    cfg=config();cfg.film.color_process='monochrome';cfg.mode='bw_negative'
    resolved,audit=compile_effect_intent(cfg);assert not audit['generated_events'] and 'dye_unreachable' in audit['targets']


def test_explicit_dye_bath_temperature_can_damage_emulsion_even_without_dye_reaction():
    cfg=configured();cfg.chemistry.process_events={'model':EVENT_MODEL_V6,'events':[{'kind':'dye_bath','concentration_mmol_l':0.,'temperature_c':60.,'transfer_s':0.}]}
    fields,audit=form_process_surface(cfg,(24,36),np.random.default_rng(5))
    assert fields['emulsion_damage'].mean()>0
    cfg.chemistry.process_events['events'][0]['temperature_c']=cfg.chemistry.temperature_c
    assert form_process_surface(cfg,(24,36),np.random.default_rng(5))==(None,None)


def test_dye_spectral_archive_share_and_editors(tmp_path,monkeypatch):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor,ProcessResponseDialog
    cfg=configuration();cfg.enable_grain=False;cfg.chemistry.effect_intent=config().chemistry.effect_intent
    base=deepcopy(cfg);base.chemistry.effect_intent['enabled']=False
    a=develop_negative(scene(),cfg);b=develop_negative(scene(),base)
    assert not np.array_equal(a.spectral_medium.component_amounts,b.spectral_medium.component_amounts)
    frozen=a.spectral_medium.component_amounts.copy();scan_medium_output(a,cfg)
    np.testing.assert_array_equal(frozen,a.spectral_medium.component_amounts)
    source=tmp_path/'chart.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    result=run_request(WorkflowRequest('full',source,tmp_path/'scan.png',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    assert medium.metadata['process_control']['effect_intent']['intent']['model']==MODEL_V2
    assert any(item['code']=='dye_loss_remaining_image' for item in medium.metadata['process_control']['couplings']['findings'])
    assert config_from_share_code(encode_recipe_share(cfg)).chemistry.effect_intent['amounts']['dye_damage_strength']==.5
    app=_application();compiled,_=compile_effect_intent(cfg)
    editor=ProcessOperationsEditor(compiled.chemistry.process_events)
    response=ProcessResponseDialog(None)
    try:
        assert editor.read()==compiled.chemistry.process_events
        response.controls['dye_attack_l_mmol_s'].setValue(.003);response.apply()
        assert response.value['model']==RESPONSE_MODEL_V3 and response.value['dye_attack_l_mmol_s']==.003
    finally:editor.close();response.close();app.processEvents()


def test_couplings_are_read_only_and_disabled_intent_preserves_manual_conditions():
    from film_foundry.core.process_couplings import describe_process_couplings
    from film_foundry.ui.process_couplings_dialog import LINKS
    cfg=config();cfg.chemistry.effect_intent['amounts'].update(chemical_stain=.5,uneven_development=.4)
    before=asdict(cfg);report=describe_process_couplings(cfg)
    codes={item['code'] for item in report['findings']}
    assert {'access_and_density','residue_density_and_colour','dye_loss_remaining_image'}<=codes
    assert asdict(cfg)==before and codes<=set(LINKS)
    cfg.chemistry.effect_intent['enabled']=False
    cfg.chemistry.process_events={'model':EVENT_MODEL_V6,'events':[{'kind':'temperature','temperature_c':60.}]}
    codes={item['code'] for item in describe_process_couplings(cfg)['findings']}
    assert 'intent_bypassed' in codes and 'thermal_damage_threshold' in codes
    assert not codes&{'access_and_density','residue_density_and_colour','dye_loss_remaining_image'}
    cfg.film.color_process='monochrome';cfg.chemistry.process_steps=None;cfg.chemistry.program_key='color_negative'
    assert 'silver_only_colour_clearing' in {item['code'] for item in describe_process_couplings(cfg)['findings']}
    cfg=config();cfg.chemistry.process_steps=None;cfg.chemistry.program_key='bw_negative'
    assert 'no_dye_pool' in {item['code'] for item in describe_process_couplings(cfg)['findings']}
