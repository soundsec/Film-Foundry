"""Wet inventory continuity and old primary-only checkpoint compatibility."""
from copy import deepcopy
import numpy as np
import pytest

from film_foundry.app.film_process_work import start_work,continue_work,describe_work
from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint
from film_foundry.core.film_process.surface import SurfaceProcessSession
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.engine import develop_negative_from_latent
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.tests.test_film_process_work import source,same_medium,config
from film_foundry.tests.test_emulsion_shear import config as shear_config,SHEAR
from film_foundry.tests.test_emulsion_integrity import WEAR
from film_foundry.tests.test_bath_solution import solution


def wet_config():
    from film_foundry.contracts.carbon_backing import validate_carbon_backing
    from film_foundry.contracts.film_time import validate_response
    from film_foundry.tests.test_film_time_history import history
    c=shear_config({'kind':'bath_contact','bath':2,'fill_s':70.},
        {'kind':'backing_condition','before_bath':1},
        {**WEAR,'before_bath':1},
        {'kind':'contaminate','after_bath':1},
        {'kind':'silver_bath','after_bath':1},
        {**SHEAR,'after_bath':1},
        {'kind':'backing_wash','after_bath':2},
        {'kind':'harden','after_bath':2},
        {**WEAR,'after_bath':2},
        {'kind':'wash','temperature_c':60.,'after_bath':2},
        {'kind':'silver_bath'}, {**SHEAR,'travel_mm':-2.}, {'kind':'wash'})
    c.random_seed=19;c.enable_grain=True
    c.film.carbon_backing=validate_carbon_backing({})
    c.film.time_response=validate_response({'model':'film_time_response_v2'})
    c.chemistry.latent_wait=history('latent',25.)
    c.chemistry.post_process_light=history('light',100.)
    c.chemistry.process_steps=(c.chemistry.process_steps[0],
        {'action':'bleach_silver','temperature_c':15.,'solution':{
            **solution('bleach_silver'),'model':'bath_solution_v2','hardener_g_l':2.,
            'secondary':{'action':'fix_halide','rate_per_minute':.5}}},
        {'action':'fix_halide','strength':.3,'temperature_c':35.})
    return c


def test_every_boundary_preserves_surface_and_never_replays_actual_events(tmp_path,monkeypatch):
    c=wet_config();path=source(tmp_path,c);latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    calls=[];original=SurfaceProcessSession.apply_event
    def track(self,event):
        if hasattr(self,'total_steps'):calls.append((self.cursor,event['kind']))
        return original(self,event)
    monkeypatch.setattr(SurfaceProcessSession,'apply_event',track)
    previous=tmp_path/'0.ffprocess.npz';start_work(path,previous,c,stop_after=0)
    meta,_,_=load_checkpoint(previous);initial=meta['surface_state']
    assert initial['cursor']==0 and initial['wet_seconds']==0 and not calls
    assert np.all(initial['fields']['emulsion_coverage']==1)
    assert not np.any(initial['fields']['surface_silver_mg_m2'])
    total=describe_work(previous)['total_steps'];last_cursor=0;last_wet=0.;array_hardening=False
    for stop in range(1,total+1):
        calls.clear();out=tmp_path/f'{stop}.ffprocess.npz'
        continue_work(previous,out,stop_after=stop)
        meta,_,state=load_checkpoint(out);wet=meta['surface_state']
        assert wet['next_step']==stop and meta['boundary']['next_step']==stop
        assert [i for i,_ in calls]==list(range(last_cursor,wet['cursor']))
        assert wet['wet_seconds']>=last_wet
        assert wet['light_done']==(stop==total)
        assert max(wet['fields']['emulsion_damage'].shape)<=512
        array_hardening |= isinstance(wet['hardening'],np.ndarray)
        # No future wash can enter the first exposure-history boundary.
        if stop==1:assert wet['cursor']==0 and wet['wet_seconds']==0
        if state.emulsion_coverage is not None:
            np.testing.assert_allclose(state.emulsion_coverage,wet['fields']['emulsion_coverage'],atol=2e-6)
        last_cursor=wet['cursor'];last_wet=wet['wet_seconds'];previous=out
    assert array_hardening
    assert wet['ledger'].get('surface_halide_printed_mg_m2',0)>=0
    calls.clear();result=continue_work(previous,tmp_path/'finished.npz')
    assert not calls
    same_medium(expected,load_developed_negative_npz(result.output_path))


@pytest.mark.parametrize('version',[7,9])
def test_legacy_event_models_remain_equivalent_when_segmented(tmp_path,version):
    c=wet_config();c.chemistry.latent_wait=None;c.chemistry.post_process_light=None
    c.chemistry.process_events={'model':f'film_process_events_v{version}','events':[
        {'kind':'silver_bath','after_bath':1},
        {'kind':'dye_bath','after_bath':1,'temperature_c':30.},
        {'kind':'contaminate','after_bath':2},
        *([WEAR] if version==9 else []),
        {'kind':'wash','temperature_c':55.}]}
    path=source(tmp_path,c);latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    paused=tmp_path/'work.ffprocess.npz';start_work(path,paused,c,stop_after=2)
    result=continue_work(paused,tmp_path/'final.npz')
    same_medium(expected,load_developed_negative_npz(result.output_path))


def test_surface_definition_cursor_and_legacy_checkpoint(tmp_path):
    from film_foundry.app.film_process_work import _run
    from film_foundry.model.config import DarkroomConfig
    c=wet_config();path=source(tmp_path,c);new=tmp_path/'new.ffprocess.npz'
    start_work(path,new,c,stop_after=2)
    meta,latent,state=load_checkpoint(new)
    bad=deepcopy(meta);bad['surface_state']['cursor']+=1
    wrong=tmp_path/'wrong.ffprocess.npz';save_checkpoint(wrong,bad,latent,state)
    with pytest.raises(ValueError,match='cursors differ'):continue_work(wrong,tmp_path/'bad.npz')
    from film_foundry.contracts.process_events import validate_process_response
    # The latent's material snapshot is authoritative. Alter the saved surface
    # definition itself, rather than a UI material field overridden by latent I/O.
    changed=deepcopy(meta);changed['surface_state']['definition']['response']=validate_process_response({'initial_hardening':.9})
    save_checkpoint(wrong,changed,latent,state)
    with pytest.raises(ValueError,match='differs'):continue_work(wrong,tmp_path/'bad.npz')
    # Emit an authentic v1 checkpoint through the still-supported old mode.
    oldmeta=deepcopy(meta);oldmeta.pop('surface_state');oldmeta['scope']='frozen_program_primary_pools_v1'
    old=tmp_path/'old.ffprocess.npz'
    _run(path,old,latent,DarkroomConfig.from_dict(meta['config']),oldmeta,None,2,None)
    assert load_checkpoint(old)[0]['kind']=='film_process_work_v1'
    old_result=continue_work(old,tmp_path/'old-final.npz')
    new_result=continue_work(new,tmp_path/'new-final.npz')
    same_medium(load_developed_negative_npz(old_result.output_path),load_developed_negative_npz(new_result.output_path))


def test_provisional_adjacency_plan_cannot_advance_live_surface(tmp_path):
    c=wet_config();c.chemistry.development_adjacency_strength=.2
    c.chemistry.uneven_development=.3
    path=source(tmp_path,c);latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    cp=tmp_path/'zero.ffprocess.npz';start_work(path,cp,c,stop_after=0)
    surface=load_checkpoint(cp)[0]['surface_state']
    assert surface['cursor']==0 and surface['wet_seconds']==0
    result=continue_work(cp,tmp_path/'finished.npz')
    same_medium(expected,load_developed_negative_npz(result.output_path))
