"""Stage ownership and retained-salt accounting, without clock or scan effects."""
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.contracts.film_time import validate_response,validate_history,MODELS
from film_foundry.model.config import DarkroomConfig,DevelopRecipeConfig
from film_foundry.tests.test_custom_process_steps import _latent,_material
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.film_process.operators import apply_process_program


def history(kind,duration,temperature=20.):
    row=({'duration_hours':duration,'relative_illuminance':1.,'light_rgb':[1.,1.,1.]} if kind=='light' else
         {('duration_days' if kind=='storage' else 'duration_hours'):duration,'temperature_c':temperature,'relative_humidity':.5})
    return validate_history({'model':MODELS[kind],'segments':[row]},kind)


def response(**fields):return validate_response({'model':'film_time_response_v1',**fields})


def config():
    c=DarkroomConfig(enable_mtf=False,enable_halation=False,enable_grain=False)
    c.film.time_response=response();return c


def test_waiting_is_closed_form_competition_not_chemical_development():
    from film_foundry.core.film_process.time_history import age_latent
    initial=_latent();initial.developability[:]=(.0,.5,1.)
    before=initial.copy();one=initial.copy();split=initial.copy();r=response()
    age_latent(one,history('latent',960.,32.),r)
    for _ in range(4):age_latent(split,history('latent',240.,32.),r)
    np.testing.assert_allclose(one.developability,split.developability,atol=1e-7)
    assert np.all(one.developability[...,0]>0.) and np.all(one.developability[...,-1]<1.)
    for key in ('halide','metallic_silver','coupler','dye'):np.testing.assert_array_equal(getattr(one,key),getattr(before,key))
    zero=initial.copy();age_latent(zero,history('latent',0.,60.),r)
    np.testing.assert_array_equal(zero.developability,before.developability)


def test_storage_is_frozen_at_exposure_and_unused_slots_keep_only_storage_fog(tmp_path):
    from film_foundry.core.engine import expose_linear_to_latent,develop_negative_from_latent
    from film_foundry.core.latent_io import save_exposed_latent,load_exposed_latent,unexposed_latent_like
    from film_foundry.core.film_process.time_history import storage_response
    c=config();c.film.storage_history=history('storage',3650.,35.)
    image=np.linspace(0.,1.,7*11*3,dtype=np.float32).reshape(7,11,3)
    latent=expose_linear_to_latent(image,c,rng=np.random.default_rng(4))
    fresh=deepcopy(c);fresh.film.storage_history=None
    normal=expose_linear_to_latent(image,fresh,rng=np.random.default_rng(4))
    assert not np.array_equal(latent.developability,normal.developability)
    speed,fog=storage_response(c.film,3)
    assert np.all(speed<1) and np.all(fog>0)
    blank=unexposed_latent_like(latent)
    np.testing.assert_array_equal(blank.developability,np.broadcast_to(fog,blank.developability.shape))
    archive=tmp_path/'source.darkroom_latent.npz';save_exposed_latent(archive,latent,identity={})
    loaded,_=load_exposed_latent(archive)
    np.testing.assert_array_equal(loaded.developability,latent.developability)
    changed=deepcopy(c);changed.film.storage_history=history('storage',0.);changed.film.time_response=None
    # Frozen material replaces the current draft; storage is never applied a second time.
    a=develop_negative_from_latent(loaded.copy(),c,rng=np.random.default_rng(8))
    b=develop_negative_from_latent(loaded.copy(),changed,rng=np.random.default_rng(8))
    np.testing.assert_array_equal(a.optical_density_rgb,b.optical_density_rgb)
    np.testing.assert_array_equal(loaded.developability,latent.developability)


def test_light_converts_only_remaining_salts_without_dye_or_new_inventory():
    r=response();material=replace(_material(),time_response=r)
    rows=({'action':'develop_color'}, {'action':'bleach_silver','strength':.8}, {'action':'fix_halide','strength':.2})
    recipe=DevelopRecipeConfig(process_steps=rows)
    dark=apply_process_program(material,_latent(),program_from_develop_recipe(recipe))
    lit=apply_process_program(material,_latent(),program_from_develop_recipe(replace(recipe,post_process_light=history('light',30.))))
    for result in (dark,lit):
        fixed=sum(s.reacted_amount for s in result.trace if s.action=='fix_halide')
        assert sum(result.state.totals()[key] for key in ('halide','metallic_silver','bleached_halide'))+fixed==pytest.approx(9.,abs=2e-6)
    np.testing.assert_array_equal(lit.state.dye,dark.state.dye)
    assert lit.state.metallic_silver.sum()>dark.state.metallic_silver.sum()
    assert lit.state.halide.sum()<dark.state.halide.sum()
    assert lit.state.bleached_halide.sum()<dark.state.bleached_halide.sum()
    removed=(dark.state.halide.sum()-lit.state.halide.sum())+(dark.state.bleached_halide.sum()-lit.state.bleached_halide.sum())
    assert lit.state.metallic_silver.sum()-dark.state.metallic_silver.sum()==pytest.approx(removed,abs=2e-6)
    fixed=replace(recipe,process_steps=(*rows[:2],{'action':'fix_halide'}))
    a=apply_process_program(material,_latent(),program_from_develop_recipe(fixed))
    b=apply_process_program(material,_latent(),program_from_develop_recipe(replace(fixed,post_process_light=history('light',1000.))))
    np.testing.assert_array_equal(a.final_medium.optical_density_rgb(),b.final_medium.optical_density_rgb())


def test_waiting_precedes_baths_without_reindexing_incidents_or_future_light():
    from film_foundry.core.process_operations import BathContactRate,source_bath_rows
    from film_foundry.core.film_process.operators import provisional_formed_silver_for_bath
    from film_foundry.core.film_process.integration import _compatibility_for_program
    c=config();c.chemistry.process_steps=({'action':'develop_color'},)
    c.film.time_response={'model':'film_time_response_v1'}
    c.chemistry.formation_history_model='legacy_global_v1'
    c.chemistry.latent_wait=history('latent',2400.)
    c.chemistry.post_process_light=history('light',1000.)
    c.chemistry.process_events={'model':'film_process_events_v11','events':[{'kind':'fixer_contact','before_bath':1}]}
    from film_foundry.contracts.process_events import validate_process_events
    c.chemistry.process_events=validate_process_events(c.chemistry.process_events)
    provider=BathContactRate(c,(1,3));program=provider.program
    assert len(source_bath_rows(c,program))==1
    assert all(s.label for s in program.steps)
    assert program.steps[0].action.value=='age_latent'
    assert program.steps[1].process_event_index==0
    assert program.steps[-1].action.value=='light_residual_halide'
    compatibility=_compatibility_for_program(c.film,'custom',3,program=program)
    provisional=provisional_formed_silver_for_bath(_latent(),program,0,compatibility,provider)
    no_light=replace(program,steps=program.steps[:-1])
    actual=apply_process_program(replace(_material(),time_response=c.film.time_response),_latent(),no_light,compatibility,local_development_rate=provider)
    np.testing.assert_array_equal(provisional,actual.state.metallic_silver)


@pytest.mark.parametrize('spectral',[False,True])
def test_pipeline_tiling_archive_and_scan_never_advance_time(tmp_path,spectral):
    from PIL import Image
    from film_foundry.tests.test_spectral_grain import configuration,scene
    from film_foundry.core.engine import develop_negative,scan_medium_output
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.core.negative_io import load_developed_negative_npz,load_developed_medium_for_scan
    c=configuration(independent=True);c.film.time_response=response();c.film.storage_history=history('storage',1000.,30.)
    c.chemistry.latent_wait=history('latent',500.,32.);c.chemistry.post_process_light=history('light',20.)
    c.chemistry.halide_fixing_completion=.3
    c.chemistry.development_adjacency_strength=.2;c.film.dir_material_response_matrix=((.1,.1,.1),)*3
    if not spectral:c.film.spectral_material_profile=None;c.scanner.spectral_observation_profile=None
    tiled=develop_negative(scene(),c,rng=np.random.default_rng(12))
    whole=deepcopy(c);whole.processing.material_tile_rows=0
    full=develop_negative(scene(),whole,rng=np.random.default_rng(12))
    np.testing.assert_allclose(full.optical_density_rgb,tiled.optical_density_rgb,atol=1e-6,rtol=1e-6)
    if spectral:np.testing.assert_allclose(full.spectral_medium.component_amounts,tiled.spectral_medium.component_amounts,atol=1e-6,rtol=1e-6)
    trace=tiled.metadata['film_process_model']['process_trace']
    assert trace[0]['action']=='age_latent' and trace[-1]['action']=='light_residual_halide' and trace[-1]['reacted_amount']>0
    source=tmp_path/'source.png';Image.fromarray(np.uint8(scene()*255)).save(source)
    latent=tmp_path/'latent.darkroom_latent.npz';run_request(WorkflowRequest('expose',source,latent,c))
    c.output.save_scanner_raw=False;c.output.export_transparent_plate=False
    result=run_request(WorkflowRequest('develop_latent',latent,tmp_path/'out',c))
    medium=load_developed_negative_npz(result.medium_path);fast=load_developed_medium_for_scan(result.medium_path)
    original=medium.optical_density_rgb.copy()
    one=scan_medium_output(medium,c).output_srgb;two=scan_medium_output(medium,c).output_srgb
    np.testing.assert_array_equal(one,two);np.testing.assert_array_equal(one,scan_medium_output(fast,c).output_srgb)
    np.testing.assert_array_equal(original,medium.optical_density_rgb)
    assert medium.metadata['film_process_model']['time_history']['clock']=='explicit_durations_only'


def test_configuration_ui_share_and_modes_keep_separate_clocks(monkeypatch,tmp_path):
    from film_foundry.ui.qt import _application
    from film_foundry.ui.film_time_editor import FilmTimeDialog
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.core.process_controls import condition_driven_config
    from film_foundry.core.effect_intent import compile_effect_intent
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    app=_application();c=config();c.film.storage_history=history('storage',365.);c.chemistry.latent_wait=history('latent',48.);c.chemistry.post_process_light=history('light',3.)
    material=FilmTimeDialog(c,material=True);process=FilmTimeDialog(c);baths=RollProcessDialog(c)
    editor=DevelopProcessEditor(config=c);stock=FilmMaterialEditor(config=c)
    try:
        material.apply();process.apply();baths.apply()
        assert material.result()==process.result()==baths.result()==1
        assert material.storage_value==c.film.storage_history and process.wait_value==c.chemistry.latent_wait
        assert editor._config_from_controls().chemistry.post_process_light==c.chemistry.post_process_light
        assert stock._config_from_controls().film.storage_history==c.film.storage_history
        restored=config_from_share_code(encode_recipe_share(c))
        assert restored.film.time_response==c.film.time_response
        assert condition_driven_config(restored).chemistry.latent_wait==c.chemistry.latent_wait
        restored.chemistry.effect_intent={'model':'process_effect_intent_v1','amounts':{'chemical_stain':.1}}
        compiled,_=compile_effect_intent(restored)
        assert compiled.chemistry.post_process_light==c.chemistry.post_process_light
        assert DarkroomConfig.from_dict({}).film.time_response is None
    finally:
        material.close();process.close();baths.close();editor.root.destroy();stock.root.destroy();app.processEvents()


def test_zero_clocks_preserve_formation_and_random_stream():
    from film_foundry.core.engine import develop_negative
    from film_foundry.tests.test_spectral_grain import scene
    c=config();c.enable_grain=True;c.film.silver_grain_strength=.08;c.chemistry.silver_retention=.3
    first_rng=np.random.default_rng(17);second_rng=np.random.default_rng(17)
    original=develop_negative(scene(),c,rng=first_rng)
    c.film.storage_history=history('storage',0.,60.)
    c.chemistry.latent_wait=history('latent',0.,60.);c.chemistry.post_process_light=history('light',0.)
    zero=develop_negative(scene(),c,rng=second_rng)
    np.testing.assert_array_equal(original.optical_density_rgb,zero.optical_density_rgb)
    np.testing.assert_array_equal(first_rng.integers(0,2**32,8),second_rng.integers(0,2**32,8))
