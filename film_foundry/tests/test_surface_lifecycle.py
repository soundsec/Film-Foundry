from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.contracts.process_events import EVENT_MODEL_V7,EVENT_MODEL_V6,SURFACE_MODEL_V2,validate_process_events
from film_foundry.core.process_operations import form_process_surface,BathContactRate
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.film_process.operators import apply_process_program
from film_foundry.tests.test_bath_solution import configured,solution
from film_foundry.tests.test_custom_process_steps import _material,_latent


def config(*events):
    cfg=configured();cfg.chemistry.process_events={'model':EVENT_MODEL_V7,'events':list(events)}
    return cfg


def form(cfg):return form_process_surface(cfg,(24,36),np.random.default_rng(2))


def test_surface_silver_bleach_fix_are_separate_mass_conserving_states():
    cfg=config({'kind':'silver_bath','after_bath':1})
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    cleared,a=form(cfg)
    assert a['model']==SURFACE_MODEL_V2
    assert not np.any(cleared['surface_silver_mg_m2']) and not np.any(cleared['surface_halide_mg_m2'])
    cfg.chemistry.process_steps=cfg.chemistry.process_steps[:2]
    salty,b=form(cfg);assert salty['surface_halide_mg_m2'].mean()>0 and not np.any(salty['surface_silver_mg_m2'])
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'fix_halide'})
    metal,c=form(cfg);assert metal['surface_silver_mg_m2'].mean()>0 and not np.any(metal['surface_halide_mg_m2'])
    for audit in (a,b,c):
        ledger=audit['balance']
        assert ledger['silver_supplied_mg_m2']==pytest.approx(sum(ledger[key] for key in ('silver_deposited_mg_m2','silver_discharged_mg_m2','surface_halide_remaining_mg_m2','surface_halide_fixed_mg_m2','surface_silver_removed_mg_m2')),abs=1e-6)
    cfg.chemistry.process_events['events'][0]['after_bath']=0
    late,_=form(cfg);np.testing.assert_array_equal(late['surface_silver_mg_m2'],metal['surface_silver_mg_m2'])


def test_surface_contact_coupled_clearing_and_premature_fixer():
    cfg=config({'kind':'bath_contact','bath':2,'immersion_fraction':.5,'angle_degrees':0.},{'kind':'silver_bath','after_bath':1})
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    fields,a=form(cfg)
    assert fields['surface_silver_mg_m2'][:,:15].max()==0
    assert fields['surface_silver_mg_m2'][:,-15:].min()>0
    cfg=config({'kind':'silver_bath','after_bath':1})
    cfg.chemistry.process_steps=({'action':'develop_color'}, {'action':'bleach_silver','solution':{**solution('bleach_silver'),'model':'bath_solution_v2','secondary':{'action':'fix_halide','rate_per_minute':.5}}})
    cleared,_=form(cfg)
    cfg.chemistry.process_steps[1]['solution']['secondary']['rate_per_minute']=0.
    salty,_=form(cfg)
    np.testing.assert_allclose(cleared['surface_silver_mg_m2'],salty['surface_silver_mg_m2'],rtol=3e-6)
    assert cleared['surface_halide_mg_m2'].mean()<salty['surface_halide_mg_m2'].mean()
    cfg.chemistry.process_steps=(*cfg.chemistry.process_steps,{'action':'remove_auxiliary'})
    cfg.chemistry.process_events['events'].append({'kind':'fixer_contact','before_bath':3,'concentration_mmol_l':100.,'duration_s':120.})
    fixed,_=form(cfg)
    assert fixed['surface_halide_mg_m2'].mean()<salty['surface_halide_mg_m2'].mean()*.001
    np.testing.assert_array_equal(fixed['surface_silver_mg_m2'],salty['surface_silver_mg_m2'])


def test_dye_erosion_runs_only_on_dye_present_at_its_position():
    cfg=config({'kind':'dye_bath','after_bath':1,'concentration_mmol_l':5.,'duration_s':120.})
    cfg.chemistry.process_steps=({'action':'remove_auxiliary'},{'action':'develop_color'},{'action':'fix_halide'})
    early=apply_process_program(_material(),_latent(),program_from_develop_recipe(cfg.chemistry)).state
    cfg.chemistry.process_events['events'][0]['after_bath']=2
    late=apply_process_program(_material(),_latent(),program_from_develop_recipe(cfg.chemistry)).state
    assert late.dye.mean()<early.dye.mean()
    np.testing.assert_array_equal(late.metallic_silver,early.metallic_silver)
    np.testing.assert_array_equal(late.halide,early.halide)
    program=program_from_develop_recipe(cfg.chemistry)
    assert [step.bath_index for step in program.steps]==[0,1,1,2]
    cfg.chemistry.process_events['events'][0]['after_bath']=4
    with pytest.raises(ValueError,match='nonexistent'):program_from_develop_recipe(cfg.chemistry)


def test_local_dye_contact_is_tile_invariant_and_requires_a_provider():
    cfg=config({'kind':'dye_bath','radius_mm':7.,'concentration_mmol_l':5.})
    cfg.chemistry.process_steps=({'action':'remove_auxiliary'},)
    program=program_from_develop_recipe(cfg.chemistry);step=program.steps[-1]
    provider=BathContactRate(cfg,(25,37))
    full=provider.contact_for_step(0,step)
    tiled=np.concatenate([provider.slice_rows(a,b).contact_for_step(0,step) for a,b in ((0,7),(7,19),(19,25))])
    np.testing.assert_array_equal(full,tiled)
    assert full[0,0]==0 and full[12,18]==1
    latent=_latent();latent.dye[:]=.5
    with pytest.raises(ValueError,match='provider'):apply_process_program(_material(),latent,program)
    result=apply_process_program(_material(),latent,program,local_development_rate=BathContactRate(cfg,(1,3))).state
    # At 1x3 each sample covers 12 mm; the edge pixel partly intersects the disc.
    assert .5>=result.dye[0,0,0]>result.dye[0,1,0]


def test_new_events_roundtrip_and_old_semantics_stay_unchanged():
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    cfg=config({'kind':'dye_bath','after_bath':2,'radius_mm':5.},{'kind':'silver_bath','after_bath':1})
    clean=validate_process_events(cfg.chemistry.process_events)
    app=_application();editor=ProcessOperationsEditor(clean)
    try:assert editor.read()==clean
    finally:editor.close();app.processEvents()
    legacy=deepcopy(cfg);legacy.chemistry.process_events={'model':EVENT_MODEL_V6,'events':[{'kind':'silver_bath','after_bath':1}]}
    with pytest.raises(ValueError,match='silver-removal'):form(legacy)
    plain=config({'kind':'silver_bath'})
    modern,a=form(plain);plain.chemistry.process_events['model']=EVENT_MODEL_V6;old,b=form(plain)
    for key in old:np.testing.assert_array_equal(modern[key],old[key])


def test_full_spectral_archive_contains_surface_halide_and_scan_is_readonly(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    cfg=configuration();cfg.enable_grain=False
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'remove_auxiliary'})
    cfg.chemistry.process_events=config({'kind':'silver_bath','after_bath':1},{'kind':'dye_bath','after_bath':2,'radius_mm':5.}).chemistry.process_events
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    source=tmp_path/'input.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'output.png',cfg))
    medium=load_developed_negative_npz(result.medium_path)
    assert np.any(medium.process_surface['surface_halide_mg_m2'])
    saved=medium.process_surface['surface_halide_mg_m2'].copy()
    a=scan_medium_output(medium,cfg).output_srgb
    b=scan_medium_output(load_developed_medium_for_scan(result.medium_path),cfg).output_srgb
    np.testing.assert_array_equal(a,b);np.testing.assert_array_equal(saved,medium.process_surface['surface_halide_mg_m2'])
    restored=config_from_share_code(encode_recipe_share(cfg))
    assert restored.chemistry.process_events['model']==EVENT_MODEL_V7
