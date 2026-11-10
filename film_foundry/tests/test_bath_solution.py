from copy import deepcopy
from dataclasses import asdict,replace
import numpy as np
import pytest
from film_foundry.contracts.bath_solution import MODEL,validate_solution
from film_foundry.model.config import DarkroomConfig
from film_foundry.core.film_process.recipe import program_from_develop_recipe

def solution(action,**values):return validate_solution({'model':MODEL,'action':action,**values},action)

def configured():
    cfg=DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False,random_seed=9,seed_strategy='fixed')
    cfg.chemistry.process_steps=({'action':'develop_color'},
        {'action':'bleach_silver','solution':solution('bleach_silver')},
        {'action':'fix_halide','solution':solution('fix_halide')})
    return cfg

def test_independent_bath_rate_and_legacy_completion_identity():
    cfg=configured();before=asdict(cfg)
    first=program_from_develop_recipe(cfg.chemistry)
    other=deepcopy(cfg)
    other.chemistry.process_steps=tuple({**row,'developer_type':'exhausted','fixer_type':'hardening','fixer_exhaustion':1.} if i else row for i,row in enumerate(cfg.chemistry.process_steps))
    changed=program_from_develop_recipe(other.chemistry)
    assert [s.strength for s in first.steps]==[s.strength for s in changed.steps]
    assert asdict(cfg)==before
    dead=deepcopy(cfg);dead.chemistry.process_steps[1]['solution']['activity_remaining']=0
    assert program_from_develop_recipe(dead.chemistry).steps[1].strength==0
    cold=deepcopy(cfg);cold.chemistry.process_steps[1]['temperature_c']=5.
    assert program_from_develop_recipe(cold.chemistry).steps[1].strength<first.steps[1].strength
    legacy=replace(cfg.chemistry,process_steps=({'action':'develop_color'},{'action':'fix_halide'}))
    explicit_none=replace(legacy,process_steps=tuple({**row,'solution':None} for row in legacy.process_steps))
    assert program_from_develop_recipe(legacy)==program_from_develop_recipe(explicit_none)
    from film_foundry.core.process_controls import condition_driven_config
    assert condition_driven_config(cfg).chemistry.process_steps[1]['solution']==cfg.chemistry.process_steps[1]['solution']

def test_contact_uses_same_rate_and_solution_hardener_changes_material_history():
    from film_foundry.core.process_operations import BathContactRate,form_process_surface
    from film_foundry.contracts.process_events import EVENT_MODEL_V2
    cfg=configured();cfg.chemistry.process_events={'model':EVENT_MODEL_V2,'events':[{'kind':'bath_contact','bath':2,'fill_s':400.},
        {'kind':'temperature','temperature_c':60.,'transfer_s':0.}]}
    program=program_from_develop_recipe(cfg.chemistry)
    field=BathContactRate(cfg,(24,36)).contact_for_step(1,program.steps[1])
    assert field[0].mean()>field[-1].mean()
    cfg.chemistry.process_steps[1]['developer_type']='exhausted'
    np.testing.assert_array_equal(field,BathContactRate(cfg,(24,36)).contact_for_step(1,program.steps[1]))
    soft,a=form_process_surface(cfg,(24,36),np.random.default_rng(1))
    cfg.chemistry.process_steps[2]['solution']['hardener_g_l']=2.
    hard,b=form_process_surface(cfg,(24,36),np.random.default_rng(1))
    assert hard['emulsion_damage'].mean()<soft['emulsion_damage'].mean()
    assert b['final_hardening_fraction']>a['final_hardening_fraction']
    assert any(row['operation']['kind']=='solution_hardening' for row in b['events'])

def test_solution_full_archive_and_editor_roundtrip(tmp_path,monkeypatch):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.ui.qt import _application
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
    cfg=configured();cfg.process_control_mode='principle';cfg.chemistry.process_steps[2]['solution']['hardener_g_l']=1.
    source=tmp_path/'source.png';Image.fromarray(np.full((24,36,3),110,np.uint8)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'output.png',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    assert medium.metadata['process_control']['bath_kinetics'][1]['model']==MODEL
    assert medium.metadata['film_process_model']['process_surface']['final_hardening_fraction']>.4
    stamp=result.medium_path.stat().st_mtime_ns
    assert np.isfinite(scan_medium_output(medium,cfg).output_srgb).all()
    assert result.medium_path.stat().st_mtime_ns==stamp
    shared=config_from_share_code(encode_recipe_share(cfg))
    assert shared.chemistry.process_steps[2]['solution']['hardener_g_l']==1.
    _application();dialog=RollProcessDialog(cfg)
    try:
        assert dialog.rows()[1]['solution']==cfg.chemistry.process_steps[1]['solution']
        dialog.apply();assert dialog.config.chemistry.process_steps[2]['solution']['hardener_g_l']==1.
    finally:dialog.close()
    editor=BathSolutionDialog('fix_halide');editor.enabled.setChecked(True);editor.controls['hardener_g_l'].setValue(2.);editor.apply()
    assert editor.value['hardener_g_l']==2.;editor.close()
    for bad in ({'model':MODEL,'action':'bleach_silver'}, {'model':MODEL,'action':'fix_halide','q10':float('nan')}):
        with pytest.raises(ValueError):validate_solution(bad,'fix_halide')


def test_solution_targets_actual_pools_and_legacy_optical_result_is_unchanged():
    from film_foundry.tests.test_custom_process_steps import _material,_latent
    from film_foundry.core.film_process.operators import apply_process_program
    from film_foundry.core.engine import develop_negative
    cfg=configured()
    active=apply_process_program(_material(),_latent(),program_from_develop_recipe(cfg.chemistry)).state
    cfg.chemistry.process_steps[1]['solution']['activity_remaining']=0.
    retained=apply_process_program(_material(),_latent(),program_from_develop_recipe(cfg.chemistry)).state
    assert retained.metallic_silver.sum()>active.metallic_silver.sum()
    np.testing.assert_array_equal(active.dye,retained.dye)
    cfg.chemistry.process_steps=tuple({key:value for key,value in row.items() if key!='solution'} for row in cfg.chemistry.process_steps)
    scene=np.full((24,36,3),.4,np.float32)
    legacy=develop_negative(scene,cfg)
    cfg.chemistry.process_steps=tuple({**row,'solution':None} for row in cfg.chemistry.process_steps)
    equivalent=develop_negative(scene,cfg)
    np.testing.assert_array_equal(legacy.optical_density_rgb,equivalent.optical_density_rgb)


def test_partial_immersion_hardens_only_contacted_material_and_limits_later_wash():
    from film_foundry.core.process_operations import form_process_surface,compile_inhibitor_fields
    from film_foundry.contracts.process_events import EVENT_MODEL_V3
    cfg=configured()
    cfg.chemistry.process_steps=({'action':'fix_halide','solution':solution('fix_halide',hardener_g_l=2.)},
        {'action':'develop_color'})
    cfg.chemistry.process_events={'model':EVENT_MODEL_V3,'events':[
        {'kind':'bath_contact','bath':1,'immersion_fraction':.5},
        {'kind':'developer_inhibitor','before_bath':1},
        {'kind':'contaminate','after_bath':1,'contact_s':0.},
        {'kind':'wash','after_bath':1,'temperature_c':20.},
        {'kind':'temperature','temperature_c':60.,'transfer_s':0.}]}
    fields,audit=form_process_surface(cfg,(24,36),np.random.default_rng(1))
    assert fields['emulsion_damage'][:12].mean()<fields['emulsion_damage'][12:].mean()
    assert fields['soluble_residue_mg_m2'][:12].mean()>fields['soluble_residue_mg_m2'][12:].mean()
    rates,history=compile_inhibitor_fields(cfg,(24,36),program_from_develop_recipe(cfg.chemistry))
    assert rates[1][:12].mean()<rates[1][12:].mean()
    assert audit['hardening_fraction_range'][0]<audit['hardening_fraction_range'][1]
    assert history['added_mmol_m2']==pytest.approx(history['washed_mmol_m2']+history['remaining_mmol_m2'],abs=1e-7)
