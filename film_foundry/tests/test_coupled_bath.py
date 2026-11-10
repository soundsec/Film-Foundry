from copy import deepcopy
from dataclasses import replace
import math
import numpy as np
import pytest

from film_foundry.contracts.bath_solution import MODEL,MODEL_V2,validate_solution
from film_foundry.contracts.process_events import EVENT_MODEL_V4
from film_foundry.core.bath_kinetics import solution_rate_per_second
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.film_process.operators import apply_process_program
from film_foundry.core.process_operations import BathContactRate
from film_foundry.tests.test_bath_solution import configured
from film_foundry.tests.test_custom_process_steps import _material,_latent


def coupled(action='bleach_silver',**secondary):
    return validate_solution({'model':MODEL_V2,'action':action,'rate_per_minute':1.,
        'secondary':{'action':'fix_halide','rate_per_minute':1.,**secondary}},action)


def test_blix_conserves_target_pools_and_approximates_shared_clock_chain():
    cfg=configured();cfg.chemistry.time_min=1.
    cfg.chemistry.process_steps=({'action':'bleach_silver','solution':coupled()},)
    state=_latent();state.metallic_silver[:]=.6;state.halide[:]=.4;state.dye[:]=.2
    program=program_from_develop_recipe(cfg.chemistry)
    result=apply_process_program(_material(),state,program,validate_each_step=True)
    k=solution_rate_per_second(coupled(),cfg.chemistry)
    remaining_silver=.6*math.exp(-k*60)
    remaining_bleached=.6*k*60*math.exp(-k*60)
    np.testing.assert_allclose(result.state.metallic_silver,remaining_silver,rtol=1e-6)
    np.testing.assert_allclose(result.state.bleached_halide,remaining_bleached,rtol=.003)
    np.testing.assert_array_equal(result.state.dye,state.dye)
    assert len(program.steps)==24 and sum(step.bath_entry for step in program.steps)==1
    assert all(step.bath_index==0 for step in program.steps)
    # Real fixing removes both unexposed and newly bleached salts.
    assert result.state.halide.sum()<state.halide.sum()
    assert np.all(result.state.halide+result.state.metallic_silver+result.state.bleached_halide<=1.)


def test_monobath_competes_for_halide_and_disabled_secondary_preserves_v1():
    cfg=configured();cfg.chemistry.process_steps=({'action':'develop_silver','solution':coupled('develop_silver',activity_remaining=0.)},)
    def run():return apply_process_program(_material(),_latent(),program_from_develop_recipe(cfg.chemistry)).state
    disabled=run()
    cfg.chemistry.process_steps[0]['solution']['model']=MODEL
    cfg.chemistry.process_steps[0]['solution'].pop('secondary')
    pure=run();np.testing.assert_array_equal(disabled.metallic_silver,pure.metallic_silver)
    cfg.chemistry.process_steps[0]['solution']=coupled('develop_silver',rate_per_minute=3.)
    fast_fix=run()
    cfg.chemistry.process_steps[0]['solution']['secondary']['rate_per_minute']=.1
    slow_fix=run()
    assert fast_fix.metallic_silver.sum()<slow_fix.metallic_silver.sum()
    assert fast_fix.halide.sum()<slow_fix.halide.sum()


def test_expanded_bath_indices_keep_contact_and_incident_on_source_row():
    cfg=configured()
    cfg.chemistry.process_steps=({'action':'develop_color','dye_damage_strength':.1},
        {'action':'bleach_silver','solution':coupled()}, {'action':'fix_halide'})
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[
        {'kind':'bath_contact','bath':2,'immersion_fraction':.5,'angle_degrees':0.},
        {'kind':'fixer_contact','before_bath':2,'concentration_mmol_l':1.},
        {'kind':'fixer_contact','before_bath':3,'concentration_mmol_l':1.}]}
    program=program_from_develop_recipe(cfg.chemistry)
    provider=BathContactRate(cfg,(1,3))
    result=apply_process_program(_material(),_latent(),program,local_development_rate=provider)
    assert [s.label for s in result.trace if s.label.startswith('fixer_contact')]==['fixer_contact_before_bath_2','fixer_contact_before_bath_3']
    assert result.state.metallic_silver[0,0].mean()<result.state.metallic_silver[0,2].mean()
    # Validate source rows, never the much longer expanded operator list.
    cfg.chemistry.process_events['events'][2]['before_bath']=4
    with pytest.raises(ValueError,match='nonexistent'):BathContactRate(cfg,(1,3))


def test_coupled_contact_window_and_tiles_share_same_process():
    cfg=configured();cfg.chemistry.time_min=1.
    cfg.chemistry.process_steps=({'action':'develop_silver','solution':coupled('develop_silver')},)
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[
        {'kind':'bath_contact','bath':1,'fill_s':60.,'angle_degrees':90.},
        {'kind':'developer_inhibitor','before_bath':1}]}
    provider=BathContactRate(cfg,(24,36));program=provider.program
    for index,step in enumerate(program.steps):
        full=provider.contact_for_step(index,step)
        split=np.concatenate([provider.slice_rows(0,9).contact_for_step(index,step),provider.slice_rows(9,24).contact_for_step(index,step)])
        np.testing.assert_array_equal(full,split)
    # Inhibitor applies only to the developing component, not the fixer.
    cfg.chemistry.process_events['events'].pop()
    plain=BathContactRate(cfg,(24,36))
    fixing=next((i,s) for i,s in enumerate(program.steps) if s.action.value=='fix_halide')
    np.testing.assert_array_equal(provider.contact_for_step(*fixing),plain.contact_for_step(*fixing))
    assert provider._wet.nbytes<=512*512*4
    large=BathContactRate(cfg,(513,513));large.contact_for_step(*fixing)
    assert large._wet is None


def test_first_development_field_applies_to_every_reaction_interval():
    from film_foundry.core.spatial_fields import StepDevelopmentRateField
    from film_foundry.core.film_process.operators import provisional_formed_silver_for_bath,CompatibilityProfile
    cfg=configured();cfg.chemistry.process_steps=({'action':'develop_silver','solution':coupled('develop_silver')},)
    program=program_from_develop_recipe(cfg.chemistry)
    field=StepDevelopmentRateField(full_shape=(1,3),first_step_label=program.steps[0].label,first_step_rate=np.zeros((1,3),np.float32))
    suppressed=apply_process_program(_material(),_latent(),program,local_development_rate=field)
    np.testing.assert_array_equal(suppressed.state.metallic_silver,0.)
    formed=apply_process_program(_material(),_latent(),program).state.metallic_silver
    source=_latent();before=source.halide.copy()
    provisional=provisional_formed_silver_for_bath(source,program,0,CompatibilityProfile())
    np.testing.assert_array_equal(provisional,formed)
    np.testing.assert_array_equal(source.halide,before)


def test_monobath_engine_with_adjacency_and_contact_is_tile_consistent():
    from film_foundry.core.engine import develop_negative
    cfg=configured();cfg.mode='bw_negative'
    cfg.chemistry.process_steps=({'action':'develop_silver','solution':coupled('develop_silver')},)
    cfg.chemistry.development_adjacency_strength=.7
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[{'kind':'bath_contact','bath':1,'fill_s':150.}]}
    scene=np.full((24,36,3),.06,np.float32);scene[:,:18]=.85
    cfg.processing.material_tile_threshold_megapixels=100.
    full=develop_negative(scene,cfg)
    cfg.processing.material_tile_threshold_megapixels=.0001;cfg.processing.material_tile_rows=7
    tiled=develop_negative(scene,cfg)
    np.testing.assert_allclose(full.optical_density_rgb,tiled.optical_density_rgb,atol=1e-6,rtol=1e-6)


def test_v2_archive_share_and_editor(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.ui.qt import _application
    from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    cfg=configured();cfg.process_control_mode='principle'
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver','solution':coupled()})
    cfg.chemistry.process_steps[1]['solution']['hardener_g_l']=1.
    cfg.chemistry.process_events={'model':EVENT_MODEL_V4,'events':[{'kind':'bath_contact','bath':2,'immersion_fraction':.7}]}
    source=tmp_path/'source.png';Image.fromarray(np.full((24,36,3),110,np.uint8)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'output.png',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    assert medium.metadata['process_control']['bath_kinetics'][1]['model']==MODEL_V2
    audit=medium.metadata['film_process_model']['process_surface']
    assert sum(e['operation']['kind']=='solution_hardening' for e in audit['events'])==1
    stamp=result.medium_path.stat().st_mtime_ns
    assert np.isfinite(scan_medium_output(medium,cfg).output_srgb).all()
    assert result.medium_path.stat().st_mtime_ns==stamp
    shared=config_from_share_code(encode_recipe_share(cfg))
    assert shared.chemistry.process_steps[1]['solution']==cfg.chemistry.process_steps[1]['solution']
    _application();dialog=BathSolutionDialog('bleach_silver',coupled());dialog.apply()
    assert dialog.value==coupled();dialog.close()
    rows=RollProcessDialog(cfg);rows.apply()
    assert rows.config.chemistry.process_steps[1]['solution']==cfg.chemistry.process_steps[1]['solution'];rows.close()
    for bad in (dict(coupled(),action='develop_color'),dict(coupled(),secondary={'action':'remove_silver'}),dict(coupled(),secondary={'action':'fix_halide','rate_per_minute':float('nan')})):
        with pytest.raises(ValueError):validate_solution(bad,bad['action'])
