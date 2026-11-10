"""Local bath response, shared final grain and public stage boundaries."""
from copy import deepcopy
from dataclasses import replace
import json
import numpy as np
import pytest

from film_foundry.contracts.formation_history import MODEL,LOCAL_MODEL,LEGACY,capabilities
from film_foundry.core.film_process.local_grain import LocalGrainCollector,LocalGrainResponse
from film_foundry.tests.test_bath_formation_history import run
from film_foundry.tests.test_film_process_work import source,same_medium
from film_foundry.tests.test_film_preparation_checkpoint import prepared_config


def local_config(silver=False):
    from film_foundry.model.config import DarkroomConfig
    c=DarkroomConfig(enable_mtf=False,enable_halation=False,random_seed=19,seed_strategy='fixed')
    c.process_control_mode='expected';c.chemistry.formation_history_model=LOCAL_MODEL
    c.chemistry.process_variation=0.;c.chemistry.uneven_development=0.
    if silver:c.film.color_process='bw';c.chemistry.program_key='bw_negative'
    action='develop_silver' if silver else 'develop_color'
    c.chemistry.process_steps=(
        {'action':action,'developer_type':'fine_grain','time_min':2.,'temperature_c':18.},
        {'action':action,'developer_type':'push','time_min':4.,'temperature_c':36.},
        {'action':'fix_halide'})
    c.chemistry.process_events={'model':'film_process_events_v11','events':[
        {'kind':'bath_contact','bath':1,'immersion_fraction':.5}]}
    c.film.silver_grain_strength=.12 if silver else 0.
    c.film.silver_grain_radius=.008
    c.film.grain_density_correlation_radius=.006
    return c


def scene():
    y,x=np.mgrid[:72,:104]
    return np.stack((.15+.3*x/103,.20+.25*y/71,.25+.15*x/103),axis=-1).astype(np.float32)


def test_local_moments_keep_product_ownership_through_removal_and_shear():
    from film_foundry.core.film_process.formation_history import ensure_history,add_product,BathFormationResponse
    from film_foundry.core.emulsion_shear import transport_state,shift_rows
    from film_foundry.core.film_process.emulsion import remove_emulsion
    from film_foundry.tests.test_custom_process_steps import _latent
    state=_latent();ensure_history(state)
    a=np.zeros_like(state.halide);a[:,:1]=.2
    b=np.zeros_like(state.halide);b[:,1:]=.4
    state.metallic_silver+=a+b
    add_product(state,'silver',a,None,BathFormationResponse(0.,.5,.7,.8))
    add_product(state,'silver',b,None,BathFormationResponse(0.,2.,1.8,2.2))
    first=LocalGrainCollector(state.halide.shape,('silver','dye'))
    with pytest.raises(ValueError,match='not complete'):first.response('silver')
    first.capture(state)
    np.testing.assert_allclose(first.response('silver').grain_factor,[[.5,2.,2.]])
    np.testing.assert_allclose(first.response('silver').at_shape((1,1)).grain_factor,[[1.7]])
    assert not first.response('dye').grain_factor.any()
    transport_state(state,np.array([.5]))
    remove_emulsion(state,state.emulsion_coverage*.3)
    collector=LocalGrainCollector(state.halide.shape,('silver',));collector.capture(state)
    response=collector.response('silver')
    mass=state.metallic_silver.sum(axis=-1);expected=state.formation_history['silver_grain_factor'].sum(axis=-1)/mass
    np.testing.assert_allclose(response.grain_factor,expected)
    assert .5<response.grain_factor[0,1]<2.
    remove_emulsion(state,np.zeros_like(state.emulsion_coverage))
    empty=LocalGrainCollector(state.halide.shape,('silver',));empty.capture(state)
    assert not empty.response('silver').grain_factor.any()
    assert not response.grain_factor.flags.writeable


@pytest.mark.parametrize('silver',[False,True])
def test_local_response_changes_grain_not_chemistry_and_keeps_rng_and_tiles(silver):
    from film_foundry.core.engine import develop_negative
    c=local_config(silver);c.processing.material_tile_rows=0
    rng=np.random.default_rng(19);new=develop_negative(scene(),c,rng=rng)
    old=deepcopy(c);old.chemistry.formation_history_model=MODEL
    old_rng=np.random.default_rng(19);global_result=develop_negative(scene(),old,rng=old_rng)
    np.testing.assert_array_equal(new.density_cmy,global_result.density_cmy)
    assert np.max(np.abs(new.optical_density_rgb-global_result.optical_density_rgb))>1e-4
    assert rng.bit_generator.state==old_rng.bit_generator.state
    audit=new.metadata['film_process_model']['formation_history']
    assert audit['grain_resolution']=='retained_mass_weighted_local_v2'
    assert audit['local_fields'][0]['shape'][:2]==list(scene().shape[:2])
    tiled=deepcopy(c);tiled.processing.material_tile_rows=13
    result=develop_negative(scene(),tiled,rng=np.random.default_rng(19))
    np.testing.assert_allclose(new.optical_density_rgb,result.optical_density_rgb,atol=1e-6,rtol=1e-6)
    disabled=deepcopy(c);disabled.enable_grain=False
    old_disabled=deepcopy(disabled);old_disabled.chemistry.formation_history_model=MODEL
    np.testing.assert_array_equal(develop_negative(scene(),disabled,rng=np.random.default_rng(19)).optical_density_rgb,
                                  develop_negative(scene(),old_disabled,rng=np.random.default_rng(19)).optical_density_rgb)


def test_each_local_grain_factor_has_an_actual_consumer_and_silver_tiles_match():
    from film_foundry.core.density_grain import apply_density_grain,REDUCED_DENSITY_GRAIN_MODEL
    from film_foundry.core.development import build_effective_development
    from film_foundry.core.silver_grain import SilverGrainPlan,apply_metallic_silver_grain
    c=local_config();shape=(72,104,3);density=np.full(shape,.6,np.float32)
    neutral=LocalGrainResponse('dye',*(np.ones(shape,np.float32) for _ in range(3)))
    dev=build_effective_development(c.chemistry)
    baseline=apply_density_grain(density,c.film,c.chemistry,rng=np.random.default_rng(5),local_response=neutral,development_override=dev)
    for key in ('grain_factor','grain_radius_factor','grain_clump_factor'):
        values=np.ones(shape,np.float32);values[:,52:]=2.
        response=replace(neutral,**{key:values})
        result=apply_density_grain(density,c.film,c.chemistry,rng=np.random.default_rng(5),local_response=response,development_override=dev)
        assert np.max(np.abs(result-baseline))>1e-4,key
    reduced=apply_density_grain(density,c.film,c.chemistry,rng=np.random.default_rng(5),local_response=response,
                                development_override=dev,model=REDUCED_DENSITY_GRAIN_MODEL,work_long_edge=52)
    assert reduced.shape==shape and np.isfinite(reduced).all()
    gain=np.full(shape[:2],.1,np.float32);radius=np.full(shape[:2],.008,np.float32);radius[:,52:]=.016
    clump=np.full(shape[:2],.2,np.float32);clump[:,52:]=.8
    plan=SilverGrainPlan(shape[:2],19,.1,.008,.2,local_parameters=(gain,radius,clump))
    a=density.copy();b=density.copy();silver=density[...,0]
    apply_metallic_silver_grain(a,silver,plan)
    for start in range(0,72,11):apply_metallic_silver_grain(b[start:start+11],silver[start:start+11],plan,row_offset=start)
    np.testing.assert_array_equal(a,b)


@pytest.mark.parametrize('spectral',[False,True])
def test_local_saved_continuation_and_future_bath_revision(tmp_path,spectral):
    from film_foundry.app.film_process_work import start_work,continue_work,describe_work
    from film_foundry.app.film_process_revision import revise_work
    from film_foundry.tests.test_film_process_revision import override
    from film_foundry.core.film_process.checkpoint_io import load_checkpoint
    from film_foundry.core.engine import develop_negative_from_latent
    from film_foundry.core.negative_io import load_developed_negative_npz
    from film_foundry.model.config import DarkroomConfig
    c=prepared_config();c.chemistry.formation_history_model=LOCAL_MODEL
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        c.film.spectral_material_profile=configuration().film.spectral_material_profile
    path=source(tmp_path,c);initial=tmp_path/'zero.ffprocess.npz';start_work(path,initial,c)
    info=describe_work(initial);caps=info['capabilities']
    assert caps['formation']['local_response'] and caps['baths'][0]['refreshes']==['adjacency','dir']
    assert caps['stages'][1]['state']=='pending'
    assert caps['operations']['finish_processing'] and not caps['operations']['finalize']
    json.dumps(caps)
    stop=next(i for i,s in enumerate(info['steps']) if s['bath_index']==1)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(initial,mid,stop_after=stop)
    branch=tmp_path/'branch.ffprocess.npz';revise_work(mid,branch,[override(2,'develop_color',time_min=.5)])
    meta,latent,_=load_checkpoint(branch)
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(meta['config']),rng=np.random.default_rng(19))
    path.unlink();initial.unlink();mid.unlink()
    result=continue_work(branch,tmp_path/'final.npz');same_medium(expected,load_developed_negative_npz(result.output_path))


def test_configuration_and_actual_editors_preserve_explicit_versions(tmp_path,monkeypatch):
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.app.workflow import encode_recipe_share,config_from_share_code
    from film_foundry.ui.qt import _application
    from film_foundry.ui.roll_process_dialog import RollProcessDialog
    from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
    for key in ('FILM_FOUNDRY_UI_SETTINGS','FILM_FOUNDRY_CAMERA_SETTINGS','FILM_FOUNDRY_CATALOG'):
        monkeypatch.setenv(key,str(tmp_path/(key+'.json')))
    assert DarkroomConfig().chemistry.formation_history_model==LOCAL_MODEL
    assert DarkroomConfig.from_dict({'develop':{}}).chemistry.formation_history_model==LEGACY
    c=DarkroomConfig();app=_application();page=RollProcessDialog(c);editor=DevelopProcessEditor(config=c)
    try:
        assert page.formation_history.currentData()==LOCAL_MODEL
        assert editor._config_from_controls().chemistry.formation_history_model==LOCAL_MODEL
        restored=config_from_share_code(encode_recipe_share(c));assert restored.chemistry.formation_history_model==LOCAL_MODEL
        page.formation_history.setCurrentIndex(page.formation_history.findData(MODEL));page.apply()
        assert page.config.chemistry.formation_history_model==MODEL
        assert not capabilities(MODEL)['local_response'] and capabilities(LOCAL_MODEL)['fields'][0]['editable'] is False
    finally:page.close();editor.root.destroy();app.processEvents()
