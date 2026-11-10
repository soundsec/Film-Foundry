"""Film-family event ordering, actual inventories, and optical source ownership."""
from copy import deepcopy
import numpy as np
import pytest
from film_foundry.tests.test_emulsion_integrity import config as legacy_config,WEAR
from film_foundry.tests.test_custom_process_steps import _material,_latent
from film_foundry.contracts.process_events import EVENT_MODEL_V10,validate_process_events
from film_foundry.core.process_operations import BathContactRate
from film_foundry.core.film_process.operators import apply_process_program


def config(*events):
    cfg=legacy_config(*events)
    cfg.chemistry.process_events['model']=EVENT_MODEL_V10
    cfg.chemistry.process_steps=({'action':'develop_color'},{'action':'bleach_silver'},{'action':'fix_halide'})
    return cfg


def run(cfg,state=None,provider=None):
    state=_latent() if state is None else state
    provider=provider or BathContactRate(cfg,state.halide.shape[:2])
    return apply_process_program(_material(),state,provider.program,local_development_rate=provider)


def test_wear_position_removes_the_actual_species_and_preserves_silver_budget():
    strip={**WEAR,'pressure':1.,'duration_s':600.}
    early=config({**strip,'before_bath':1});early.film.emulsion_integrity['adhesion']=0.
    late=deepcopy(early);late.chemistry.process_events['events']=[{**strip,'after_bath':1}]
    a,b=run(early),run(late)
    assert a.state.emulsion_removed['halide']==pytest.approx(9.)
    assert a.state.emulsion_removed['metallic_silver']==0.
    assert a.state.emulsion_removed['dye']==0.
    assert b.state.emulsion_removed['metallic_silver']>0.
    assert b.state.emulsion_removed['dye']>0.
    assert not np.any(a.state.dye) and not np.any(b.state.dye)
    for result in (a,b):
        assert all(step.reacted_amount==0. for step in result.trace if step.action in ('fix_halide','bleach_silver'))
        removed=result.state.emulsion_removed
        assert sum(removed.get(key,0.) for key in ('halide','metallic_silver','bleached_halide'))==pytest.approx(9.,abs=1e-6)
    partial=run(config({**WEAR,'after_bath':1}))
    removed=partial.state.emulsion_removed
    fixed=sum(step.reacted_amount for step in partial.trace if step.action=='fix_halide')
    remaining=sum(partial.state.totals()[key] for key in ('halide','metallic_silver','bleached_halide'))
    assert remaining+fixed+sum(removed.get(key,0.) for key in ('halide','metallic_silver','bleached_halide'))==pytest.approx(9.,abs=2e-6)
    np.testing.assert_allclose(partial.final_medium.retained_pool('dye'),partial.state.dye,atol=1e-7)


def test_list_order_at_same_boundary_and_dye_attack_share_the_primary_executor():
    wear={**WEAR,'before_bath':1}
    fixer={'kind':'fixer_contact','before_bath':1,'duration_s':40.}
    a,b=run(config(wear,fixer)),run(config(fixer,wear))
    assert a.state.emulsion_removed['halide']>b.state.emulsion_removed['halide']
    np.testing.assert_allclose(a.state.dye,b.state.dye,atol=1e-7)
    wear={**WEAR,'after_bath':1}
    dye={'kind':'dye_bath','after_bath':1,'duration_s':80.}
    a,b=run(config(wear,dye)),run(config(dye,wear))
    assert a.state.emulsion_removed['dye']>b.state.emulsion_removed['dye']
    assert [s.action for s in a.trace][1:3]==['remove_emulsion','destroy_dye']
    with pytest.raises(ValueError,match='not both'):
        validate_process_events(config({**wear,'before_bath':1}).chemistry.process_events)
    with pytest.raises(ValueError,match='nonexistent'):
        BathContactRate(config({**wear,'after_bath':25}),(1,3))


def test_repeated_local_removal_tiled_and_full_use_the_same_timeline():
    cfg=config({**WEAR,'before_bath':1,'radius_mm':8.},
        {**WEAR,'after_bath':2,'radius_mm':6.,'center_y':.65})
    state=_latent()
    for name in ('halide','developability','metallic_silver','coupler','dye'):
        setattr(state,name,np.tile(getattr(state,name),(13,9,1)))
    provider=BathContactRate(cfg,(13,27));whole=run(cfg,state,provider)
    pieces=[]
    for start,stop in ((0,6),(6,13)):
        piece=state.copy()
        for name in ('halide','developability','metallic_silver','coupler','dye'):
            setattr(piece,name,getattr(piece,name)[start:stop].copy())
        pieces.append(run(cfg,piece,provider.slice_rows(start,stop)))
    for name in ('halide','metallic_silver','dye','bleached_halide','emulsion_coverage'):
        np.testing.assert_array_equal(getattr(whole.state,name),np.concatenate([getattr(p.state,name) for p in pieces]))
    for key,value in whole.state.emulsion_removed.items():
        assert value==pytest.approx(sum(p.state.emulsion_removed[key] for p in pieces),abs=1e-6)
    assert whole.state.emulsion_coverage[0,0]==1.


def test_coupled_bath_boundaries_and_provisional_estimate_stop_before_later_wear():
    from film_foundry.tests.test_coupled_bath import coupled
    from film_foundry.core.film_process.operators import provisional_formed_silver_for_bath,CompatibilityProfile
    cfg=config({**WEAR,'after_bath':1})
    cfg.chemistry.process_steps=({'action':'develop_silver','solution':coupled('develop_silver')},
        {'action':'fix_halide'})
    provider=BathContactRate(cfg,(1,3));program=provider.program
    index=next(i for i,s in enumerate(program.steps) if s.action.value=='remove_emulsion')
    assert index==24 and program.steps[index].bath_index==0
    assert program.steps[index+1].bath_index==1
    source=_latent()
    estimate=provisional_formed_silver_for_bath(source,program,0,CompatibilityProfile(),provider)
    normal=deepcopy(cfg);normal.chemistry.process_events['events']=[]
    expected=run(normal)
    np.testing.assert_array_equal(estimate,expected.state.metallic_silver)
    assert np.all(run(cfg).state.metallic_silver<estimate)
    np.testing.assert_array_equal(source.halide,1.)


@pytest.mark.parametrize('spectral',[False,True])
def test_full_pipeline_archive_and_readonly_scan_with_prebath_loss(tmp_path,spectral):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    from film_foundry.core.scan_runtime import scan_medium_output
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    cfg=configuration() if spectral else config()
    cfg.enable_grain=False;cfg.process_control_mode='principle'
    cfg.film.emulsion_integrity=config().film.emulsion_integrity
    cfg.film.emulsion_integrity['adhesion']=0.
    cfg.chemistry.process_events=config({**WEAR,'pressure':1.,'duration_s':600.,'radius_mm':5.,'before_bath':1}).chemistry.process_events
    cfg.chemistry.development_adjacency_strength=.25
    cfg.film.dir_material_response_matrix=((.1,.1,.1),(.1,.1,.1),(.1,.1,.1))
    cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False
    assert config_from_share_code(encode_recipe_share(cfg)).chemistry.process_events['model']==EVENT_MODEL_V10
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    result=run_request(WorkflowRequest('full',source,tmp_path/'result.png',cfg))
    medium=load_developed_negative_npz(result.medium_path);fast=load_developed_medium_for_scan(result.medium_path)
    audit=medium.metadata['film_process_model']['process_surface']
    assert audit['ownership']['chemical_pool_totals_stage']=='after_ordered_processing'
    assert audit['ownership']['removed_primary_pools']['halide']>0.
    assert audit['ownership']['removed_primary_pools']['dye']==0.
    assert 'removed_spectral_component_sums' not in audit['ownership']
    np.testing.assert_allclose(medium.optical_density_rgb[15,23],audit['ownership']['support_density_rgb'],atol=1e-6)
    original=medium.optical_density_rgb.copy()
    np.testing.assert_array_equal(scan_medium_output(medium,cfg).output_srgb,scan_medium_output(fast,cfg).output_srgb)
    np.testing.assert_array_equal(medium.optical_density_rgb,original)
    if spectral:
        from film_foundry.contracts.emulsion_integrity import EMULSION_COMPONENTS
        assert np.all(medium.spectral_medium.retained_component_rows(slice(None))[15,23,list(EMULSION_COMPONENTS)]==0.)


def test_editor_new_wear_exposes_position_and_preserves_legacy(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();old=ProcessOperationsEditor(legacy_config(WEAR).chemistry.process_events)
    new=ProcessOperationsEditor()
    try:
        assert old.read()['model']=='film_process_events_v9'
        assert old.controls['after_bath'].maximum()==0
        new.kind.setCurrentIndex(new.kind.findData('emulsion_wear'));new.add()
        assert new.controls['after_bath'].maximum()==32
        new.position.setCurrentIndex(new.position.findData('before_bath'))
        new.controls['before_bath'].setValue(2)
        value=new.read();assert value['model']==EVENT_MODEL_V10
        assert value['events'][0]['before_bath']==2
        cfg=config();cfg.chemistry.process_events=value
        cfg.chemistry.effect_intent={'model':'process_effect_intent_v1','amounts':{'chemical_stain':.1}}
        compiled,_=compile_effect_intent(cfg)
        assert compiled.chemistry.process_events['model']==EVENT_MODEL_V10
        assert compiled.chemistry.process_events['events'][0]==value['events'][0]
    finally:old.close();new.close();app.processEvents()
