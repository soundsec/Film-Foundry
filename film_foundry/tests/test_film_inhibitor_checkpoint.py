"""Carried inhibitor inventory and contact derivatives across reaction boundaries."""
from copy import deepcopy
import numpy as np
import pytest
from film_foundry.tests.test_film_process_work import source,same_medium,scene,config
from film_foundry.tests.test_film_surface_checkpoint import wet_config
from film_foundry.tests.test_bath_solution import solution
from film_foundry.core.engine import expose_image_to_latent,develop_negative_from_latent
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint
from film_foundry.app.film_process_work import start_work,continue_work,describe_work


def inhibitor_config():
    c=wet_config()
    c.chemistry.process_steps=(
        {'action':'develop_silver','time_min':.6,'solution':{
            **solution('develop_silver'),'model':'bath_solution_v2','hardener_g_l':1.,
            'secondary':{'action':'fix_halide','rate_per_minute':.12}}},
        {'action':'develop_color','time_min':2.},
        {'action':'bleach_silver','strength':.7}, {'action':'fix_halide','strength':.6})
    events=c.chemistry.process_events['events']
    events[0]={'kind':'bath_contact','bath':1,'fill_s':16.,'immersion_fraction':.8}
    events.insert(1,{'kind':'bath_contact','bath':2,'fill_s':50.,'bubble_radius_mm':3.,'bubble_release_s':40.})
    events.insert(2,{'kind':'developer_inhibitor','before_bath':1,'concentration_mmol_l':70.,'radius_mm':9.})
    events.extend([{'kind':'developer_inhibitor','before_bath':2,'concentration_mmol_l':20.,'radius_mm':4.},
        {'kind':'wash','after_bath':1,'water_changes':2.,'duration_s':70.},
        {'kind':'harden','after_bath':1,'duration_s':50.}])
    return c


@pytest.mark.parametrize('spectral',[False,True])
def test_every_reaction_restores_real_inhibitor_without_replaying_past(tmp_path,monkeypatch,spectral):
    from film_foundry.core.film_process.inhibitor import InhibitorProcessSession
    from film_foundry.core import process_operations
    c=inhibitor_config();c.chemistry.development_adjacency_strength=0.;c.chemistry.dir_process_effectiveness=0.
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        c.film.spectral_material_profile=configuration().film.spectral_material_profile
    path=source(tmp_path,c);latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    def forbidden(*args,**kwargs):raise AssertionError('Live continuation rebuilt the full inhibitor plan')
    monkeypatch.setattr(process_operations,'compile_inhibitor_fields',forbidden)
    calls=[];original=InhibitorProcessSession.apply_event
    def tracked(self,event):
        if hasattr(self,'total_steps'):calls.append((self.cursor,event['kind']))
        return original(self,event)
    monkeypatch.setattr(InhibitorProcessSession,'apply_event',tracked)
    previous=tmp_path/'0.ffprocess.npz';start_work(path,previous,c,stop_after=0)
    meta,_,_=load_checkpoint(previous);saved=meta['inhibitor_state']
    assert meta['kind']=='film_process_work_v5' and saved['cursor']==0 and not np.any(saved['load'])
    assert saved['added']==saved['washed']==0 and not calls
    total=describe_work(previous)['total_steps'];cursor=0;hardening_array=False;midbath_rate=False
    for stop in range(1,total+1):
        calls.clear();out=tmp_path/f'{stop}.ffprocess.npz';continue_work(previous,out,stop_after=stop)
        meta,_,_=load_checkpoint(out);saved=meta['inhibitor_state']
        assert saved['next_step']==meta['surface_state']['next_step']==stop
        assert [i for i,_ in calls]==list(range(cursor,saved['cursor']))
        assert saved['added']==pytest.approx(saved['washed']+saved['load'].mean(dtype=np.float64),rel=2e-5,abs=1e-6)
        hardening_array|=isinstance(saved['hardening'],np.ndarray)
        step=meta['boundary']['binding']['program']['steps'][stop-1]
        if step['reaction_window'] is not None and step['action']=='develop_silver':
            assert saved['rate'] is not None and saved['rate'].min()<1
            midbath_rate=True
        cursor=saved['cursor'];previous=out
    assert midbath_rate and hardening_array and saved['washed']>0
    calls.clear();result=continue_work(previous,tmp_path/'finished.npz');assert not calls
    same_medium(expected,load_developed_negative_npz(result.output_path))


def test_inhibitor_only_inactive_surface_and_v2_compatibility(tmp_path):
    from film_foundry.app.film_process_work import _run
    from film_foundry.model.config import DarkroomConfig
    c=config();c.chemistry.process_events={'model':'film_process_events_v3','events':[
        {'kind':'developer_inhibitor','before_bath':1,'concentration_mmol_l':40.}]}
    path=source(tmp_path,c);v3=tmp_path/'v3.ffprocess.npz';start_work(path,v3,c,stop_after=1)
    meta,latent,_=load_checkpoint(v3)
    assert meta['surface_state']['active'] is False and meta['inhibitor_state']['active'] is True
    oldmeta=deepcopy(meta);oldmeta['scope']='frozen_program_primary_and_surface_v2'
    old=tmp_path/'v2.ffprocess.npz'
    _run(path,old,latent,DarkroomConfig.from_dict(meta['config']),oldmeta,None,1,None)
    assert load_checkpoint(old)[0]['kind']=='film_process_work_v2'
    a=continue_work(v3,tmp_path/'new.npz');b=continue_work(old,tmp_path/'old.npz')
    same_medium(load_developed_negative_npz(a.output_path),load_developed_negative_npz(b.output_path))


def test_no_inhibitor_keeps_explicit_empty_state_and_rejects_bad_inventory(tmp_path):
    c=config();path=source(tmp_path,c);empty=tmp_path/'empty.ffprocess.npz';start_work(path,empty,c)
    assert load_checkpoint(empty)[0]['inhibitor_state']['active'] is False
    c=inhibitor_config();path=source(tmp_path,c);cp=tmp_path/'used.ffprocess.npz';start_work(path,cp,c,stop_after=4)
    meta,latent,state=load_checkpoint(cp)
    for problem in ('cursor','mass','definition'):
        bad=deepcopy(meta);inventory=bad['inhibitor_state']
        if problem=='cursor':inventory['cursor']-=1
        elif problem=='mass':inventory['load']+=10.
        else:inventory['definition']['response']['initial_hardening']=.99
        output=tmp_path/f'{problem}.ffprocess.npz';save_checkpoint(output,bad,latent,state)
        with pytest.raises(ValueError,match='Inhibitor'):
            continue_work(output,tmp_path/f'{problem}.npz')


def test_private_preparation_isolated_and_shared_between_queries(tmp_path,monkeypatch):
    from film_foundry.core import process_operations
    c=inhibitor_config();c.chemistry.development_adjacency_strength=.25
    path=source(tmp_path,c);latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    provider=process_operations.BathContactRate(c,(23,37),incremental_surface=True,incremental_inhibitor=True)
    calls=[];original=process_operations.compile_inhibitor_fields
    def count(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(process_operations,'compile_inhibitor_fields',count)
    a=provider.at_shape((11,18));b=provider.at_shape((16,25))
    assert len(calls)==1 and a.inhibitor_fields is b.inhibitor_fields
    assert provider.inhibitor_session.cursor==0 and not np.any(provider.inhibitor_session.load)
    cp=tmp_path/'pause.ffprocess.npz';start_work(path,cp,c,stop_after=4)
    final=continue_work(cp,tmp_path/'finished.npz');same_medium(expected,load_developed_negative_npz(final.output_path))


def test_contact_preparation_reused_and_inhibitor_sampled_once_per_step(monkeypatch):
    from film_foundry.core import process_operations as ops
    c=inhibitor_config();provider=ops.BathContactRate(c,(23,37))
    steps=[(i,s) for i,s in enumerate(provider.program.steps) if s.bath_index==0 and s.action.value=='develop_silver']
    assert len(steps)>1
    built=[];sampled=[];build=ops.build_effective_development;sample=ops.sample_control_rows
    def counted_build(*a,**kw):built.append(1);return build(*a,**kw)
    def counted_sample(*a,**kw):sampled.append(1);return sample(*a,**kw)
    monkeypatch.setattr(ops,'build_effective_development',counted_build)
    monkeypatch.setattr(ops,'sample_control_rows',counted_sample)
    for index,step in steps:provider.contact_for_step(index,step)
    assert len(built)==1 and len(sampled)==len(steps)
