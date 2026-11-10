"""Film-only formation provenance, removal, contact and actual saved consumers."""
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.model.config import DarkroomConfig, DevelopRecipeConfig
from film_foundry.tests.test_custom_process_steps import _latent, _material
from film_foundry.core.film_process.operators import apply_process_program
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.film_process.formation_history import response_from_totals


def run(rows, *, state=None, material=None, provider=None, **settings):
    recipe=DevelopRecipeConfig(process_steps=tuple(rows), **settings)
    return apply_process_program(material or _material(), state or _latent(),
        program_from_develop_recipe(recipe), local_development_rate=provider)


def test_fog_is_bounded_conversion_and_can_be_disabled_or_removed():
    dark=_latent();dark.developability[:]=0.;dark.coupler[:]=.001
    row={'action':'develop_color','temperature_c':40.,'time_min':12.}
    formed=run([row],state=dark)
    totals=formed.state.totals()
    assert totals['metallic_silver']>0 and totals['formation_silver_fog']==pytest.approx(totals['metallic_silver'])
    np.testing.assert_allclose(formed.state.halide+formed.state.metallic_silver,1.,atol=1e-7)
    np.testing.assert_allclose(formed.state.coupler+formed.state.dye,.001,atol=1e-9)
    assert totals['formation_dye_fog']==pytest.approx(totals['dye'])
    assert formed.state.developability.max()==0.
    cleared=run([row,{'action':'bleach_silver'},{'action':'fix_halide'}],state=dark)
    assert cleared.state.metallic_silver.max()==0.
    assert cleared.state.totals()['formation_silver_fog']==0.
    assert cleared.state.totals()['formation_dye_fog']>0.
    for result in (run([row],state=dark,provider=np.zeros((1,3),np.float32)),
                   run([row],state=dark,material=replace(_material(),chemical_fog_susceptibility=0.)),
                   run([{**row,'strength':0.}],state=dark)):
        assert not np.any(result.state.metallic_silver)
        np.testing.assert_array_equal(result.state.halide,dark.halide)
    # Zero global clock is legal for the low-level built-in program.
    recipe=DevelopRecipeConfig(time_min=0.,temperature_c=40.)
    result=apply_process_program(_material(),dark,program_from_develop_recipe(recipe))
    assert result.state.totals()['formation_created_silver_fog']==0.


def test_surviving_products_keep_only_their_own_bath_response():
    first={'action':'develop_color','developer_type':'push','temperature_c':35.,'strength':.6}
    later={'action':'develop_silver','developer_type':'fine_grain','temperature_c':18.}
    result=run([first,{'action':'bleach_silver'}, {'action':'activate_remaining_halide'},later])
    totals=result.state.totals()
    silver=response_from_totals(totals,'silver');dye=response_from_totals(totals,'dye')
    program=program_from_develop_recipe(DevelopRecipeConfig(process_steps=(first,{'action':'bleach_silver'},{'action':'activate_remaining_halide'},later)))
    assert silver['grain_factor']==pytest.approx(program.steps[-1].formation_response.grain_factor,rel=1e-6)
    assert dye['grain_factor']==pytest.approx(program.steps[0].formation_response.grain_factor,rel=1e-6)
    assert dye['grain_factor']>silver['grain_factor']
    assert totals['formation_created_silver_fog']>totals['formation_silver_fog']
    erased=run([first,{'action':'destroy_dye','strength':1.}])
    assert erased.state.totals()['formation_dye_grain_factor']==0.
    assert erased.state.totals()['formation_dye_fog']==0.
    assert erased.state.totals()['formation_silver_grain_factor']>0.
    # With partial removal, moments lose the same share as the actual product.
    partial=run([first,{'action':'remove_silver','strength':.4}])
    assert response_from_totals(partial.state.totals(),'silver')['grain_factor']==pytest.approx(dye['grain_factor'],rel=1e-6)


def test_history_moves_and_is_removed_with_the_emulsion():
    from film_foundry.core.emulsion_shear import transport_state,shift_rows
    from film_foundry.core.film_process.emulsion import remove_emulsion
    result=run([{'action':'develop_color','temperature_c':33.}])
    state=result.state.copy();before={k:v.copy() for k,v in state.formation_history.items()}
    created=dict(state.formation_created)
    transport_state(state,np.array([.5]))
    for key,value in before.items():np.testing.assert_array_equal(state.formation_history[key],shift_rows(value,np.array([.5])))
    response=response_from_totals(state.totals(),'dye')
    remove_emulsion(state,state.emulsion_coverage*.3)
    assert response_from_totals(state.totals(),'dye')['grain_factor']==pytest.approx(response['grain_factor'],rel=1e-6)
    assert state.formation_created==created
    remove_emulsion(state,np.zeros_like(state.emulsion_coverage))
    assert all(not np.any(v) for v in state.formation_history.values())


def test_final_density_cannot_be_reweighted_by_an_unrelated_global_bath():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced
    from film_foundry.tests.test_spectral_grain import scene
    cfg=DarkroomConfig()
    # Every real bath explicitly supplies its own conditions.
    rows=tuple({'action':action,'time_min':8.,'temperature_c':20.,'developer_type':'standard'}
               for action in ('develop_color','bleach_silver','fix_halide'))
    recipe=DevelopRecipeConfig(process_steps=rows)
    a=develop_color_negative_reduced(scene(),cfg.film,recipe)
    b=develop_color_negative_reduced(scene(),cfg.film,replace(recipe,time_min=14.,temperature_c=36.,developer_type='push'))
    np.testing.assert_array_equal(a.process_result.state.dye,b.process_result.state.dye)
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    assert a.effective_development.d_min_shift==0. and a.effective_development.d_max_factor==1.


def test_shared_bath_clock_and_partial_immersion_use_actual_wet_time():
    from film_foundry.core.process_operations import BathContactRate,contact_duration,frame_extent
    from film_foundry.contracts.process_events import validate_process_events
    solution={'model':'bath_solution_v2','action':'develop_silver','rate_per_minute':.3,
              'secondary':{'action':'fix_halide','rate_per_minute':.2}}
    c=DarkroomConfig();c.chemistry.process_steps=({'action':'develop_silver','time_min':2.,'solution':solution},)
    c.chemistry.process_events=validate_process_events({'model':'film_process_events_v11','events':[
        {'kind':'bath_contact','bath':1,'immersion_fraction':.7,'fill_s':180.,'angle_degrees':0.}]})
    provider=BathContactRate(c,(1,3));program=provider.program
    doses=sum(s.formation_response.fog_dose for s in program.steps if s.formation_response is not None)
    one=replace(c.chemistry,process_steps=({'action':'develop_silver','time_min':2.,'solution':{k:v for k,v in solution.items() if k!='secondary'} | {'model':'bath_solution_v1'}},))
    assert doses==pytest.approx(program_from_develop_recipe(one).steps[0].formation_response.fog_dose)
    integrated=np.zeros((1,3),np.float64)
    for i,step in enumerate(program.steps):
        provider.contact_for_step(i,step)
        if step.formation_response is not None:integrated+=step.formation_response.fog_dose*provider.fog_rate_for_step(step.label,step.action.value)
    event=c.chemistry.process_events['events'][0]
    wet=contact_duration((1,3),frame_extent(c,(1,3)),0,1,120.,event)
    np.testing.assert_allclose(integrated,doses*wet/120.,atol=1e-9)
    dark=_latent();dark.developability[:]=0.
    result=apply_process_program(_material(),dark,program,local_development_rate=provider)
    fixed=sum(s.reacted_amount for s in result.trace if s.action=='fix_halide')
    assert result.state.halide.sum()+result.state.metallic_silver.sum()+fixed==pytest.approx(9.,abs=2e-6)
    assert not np.any(result.state.metallic_silver[:,-1])


@pytest.mark.parametrize('spectral',[False,True])
def test_engine_grain_tiling_frozen_material_and_saved_observation(tmp_path,spectral):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.core.engine import develop_negative,scan_medium_output
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    c=configuration(independent=True);c.chemistry.silver_retention=.4
    c.chemistry.process_steps=({'action':'develop_color','temperature_c':34.,'developer_type':'push'},
        {'action':'bleach_silver','strength':.6},{'action':'fix_halide'})
    c.film.chemical_fog_susceptibility=2.3
    if not spectral:c.film.spectral_material_profile=None;c.scanner.spectral_observation_profile=None
    c.chemistry.development_adjacency_strength=.2
    c.film.dir_material_response_matrix=((.1,.1,.1),(.1,.1,.1),(.1,.1,.1))
    tiled=develop_negative(scene(),c,rng=np.random.default_rng(11))
    full_config=deepcopy(c);full_config.processing.material_tile_rows=0
    full=develop_negative(scene(),full_config,rng=np.random.default_rng(11))
    np.testing.assert_allclose(full.optical_density_rgb,tiled.optical_density_rgb,atol=1e-6,rtol=1e-6)
    if spectral:np.testing.assert_allclose(full.spectral_medium.component_amounts,tiled.spectral_medium.component_amounts,atol=1e-6,rtol=1e-6)
    history=tiled.metadata['film_process_model']['formation_history']
    assert history['created_fog']['silver']>history['retained_fog']['silver']>0.
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    latent=tmp_path/'frozen.darkroom_latent.npz';run_request(WorkflowRequest('expose',source,latent,c))
    from film_foundry.core.latent_io import load_exposed_latent
    frozen,_=load_exposed_latent(latent)
    assert frozen.material_snapshot.parameters()['chemical_fog_susceptibility']==2.3
    c.film.chemical_fog_susceptibility=0.
    c.output.save_scanner_raw=False;c.output.export_transparent_plate=False
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'out',c))
    medium=load_developed_negative_npz(result.medium_path);fast=load_developed_medium_for_scan(result.medium_path)
    assert medium.metadata['film_process_model']['formation_history']['created_fog']['silver']>0.
    original=medium.optical_density_rgb.copy()
    np.testing.assert_array_equal(scan_medium_output(medium,c).output_srgb,scan_medium_output(fast,c).output_srgb)
    np.testing.assert_array_equal(original,medium.optical_density_rgb)


def test_actual_editors_share_and_modes_preserve_model(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.effect_intent import compile_effect_intent
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    old=DarkroomConfig.from_dict({'develop':{'time_min':9.}})
    assert old.chemistry.formation_history_model=='legacy_global_v1'
    app=_application();editor=DevelopProcessEditor(config=old);material=FilmMaterialEditor(config=old)
    baths=RollProcessDialog(old)
    try:
        assert editor._config_from_controls().chemistry.formation_history_model=='legacy_global_v1'
        editor.history_display.set(next(k for k,v in editor.history_labels.items() if v=='bath_products_v1'))
        cfg=editor._config_from_controls();material.scalar_vars['chemical_fog_susceptibility'].set(2.4)
        cfg.film=material._config_from_controls().film
        restored=config_from_share_code(encode_recipe_share(cfg))
        assert restored.film.chemical_fog_susceptibility==2.4
        assert restored.chemistry.formation_history_model=='bath_products_v1'
        assert condition_driven_config(restored).chemistry.formation_history_model=='bath_products_v1'
        restored.chemistry.effect_intent={'model':'process_effect_intent_v1','amounts':{'chemical_stain':.2}}
        compiled,_=compile_effect_intent(restored)
        assert compiled.chemistry.formation_history_model=='bath_products_v1'
        assert baths.formation_history.currentData()=='legacy_global_v1'
        baths.formation_history.setCurrentIndex(baths.formation_history.findData('bath_products_v1'))
        baths.apply()
        assert baths.result()==1
        assert baths.config.chemistry.formation_history_model=='bath_products_v1'
    finally:
        baths.close();editor.root.destroy();material.root.destroy();app.processEvents()
