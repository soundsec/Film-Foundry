"""Only future deterministic preparation may change during a bath revision."""
import numpy as np
import pytest

from film_foundry.tests.test_film_process_work import source,config,same_medium
from film_foundry.tests.test_film_process_revision import override,same_pools
from film_foundry.tests.test_film_preparation_checkpoint import prepared_config
from film_foundry.app.film_process_work import start_work,continue_work,describe_work
from film_foundry.app.film_process_revision import revise_work
from film_foundry.core.film_process.checkpoint_io import load_checkpoint
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.core.engine import develop_negative_from_latent
from film_foundry.model.config import DarkroomConfig


def preserve_past(before,after,pools,changed):
    same_pools(pools,changed)
    assert before['boundary']['next_step']==after['boundary']['next_step']
    assert before['boundary']['trace']==after['boundary']['trace']
    for name in ('rng_before','rng_after'):
        assert before['spatial_preparation'][name]==after['spatial_preparation'][name]
    old=before['spatial_preparation']['arrays'];new=after['spatial_preparation']['arrays']
    if 'uneven' in old:np.testing.assert_array_equal(old['uneven'],new['uneven'])


def assert_complete(branch,tmp_path,monkeypatch):
    from film_foundry.core import engine
    from film_foundry.app import film_process_work
    meta,latent,_=load_checkpoint(branch);rng=np.random.default_rng(19)
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(meta['config']),rng=rng)
    actual=[];original=film_process_work._rng
    def tracked(value):
        item=original(value);actual.append(item);return item
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):raise AssertionError('Continuation rebuilt persisted fields')
        for name in ('build_development_adjacency_field','build_dir_material_response_field','uneven_development_rate_field'):
            m.setattr(engine,name,forbidden)
        m.setattr(film_process_work,'_rng',tracked)
        result=continue_work(branch,tmp_path/'final.npz')
    same_medium(expected,load_developed_negative_npz(result.output_path))
    assert actual[-1].bit_generator.state==rng.bit_generator.state
    rebuilt=continue_work(branch,tmp_path/'rebuilt.npz',reuse_preparation=False)
    same_medium(expected,load_developed_negative_npz(rebuilt.output_path))


@pytest.mark.parametrize('spectral',[False,True])
def test_unentered_first_development_refreshes_both_fields_and_keeps_random_identity(tmp_path,monkeypatch,spectral):
    from film_foundry.core import engine
    c=prepared_config();c.chemistry.incident_identity_seed=7 if spectral else None
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        c.film.spectral_material_profile=configuration().film.spectral_material_profile
    path=source(tmp_path,c);cp=tmp_path/'start.ffprocess.npz';start_work(path,cp,c)
    before,_,pools=load_checkpoint(cp)
    assert all(row['editable'] for row in describe_work(cp)['baths'])
    update=override(1,'develop_silver',time_min=.12,temperature_c=24.)
    calls=[]
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):raise AssertionError('Bath revision resampled common unevenness')
        m.setattr(engine,'uneven_development_rate_field',forbidden)
        for name in ('build_development_adjacency_field','build_dir_material_response_field'):
            original=getattr(engine,name)
            def tracked(*a,_name=name,_original=original,**kw):
                calls.append(_name);return _original(*a,**kw)
            m.setattr(engine,name,tracked)
        out=tmp_path/'new.ffprocess.npz';revise_work(cp,out,[update])
    assert calls==['build_development_adjacency_field','build_dir_material_response_field']
    after,_,changed=load_checkpoint(out);preserve_past(before,after,pools,changed)
    assert after['bath_revisions'][-1]['refreshed_preparation']==['adjacency','dir']
    for key in ('adjacency','dir_0'):
        assert np.any(before['spatial_preparation']['arrays'][key]!=after['spatial_preparation']['arrays'][key])
    path.unlink();cp.unlink()
    assert_complete(out,tmp_path,monkeypatch)


def test_completed_first_development_keeps_adjacency_and_refreshes_only_future_dir(tmp_path,monkeypatch):
    from film_foundry.core import engine
    c=prepared_config();path=source(tmp_path,c);initial=tmp_path/'initial.ffprocess.npz';start_work(path,initial,c)
    stop=next(i for i,s in enumerate(describe_work(initial)['steps']) if s['bath_index']==1)
    cp=tmp_path/'mid.ffprocess.npz';continue_work(initial,cp,stop_after=stop)
    before,_,pools=load_checkpoint(cp);info=describe_work(cp)
    assert not info['baths'][0]['editable'] and info['baths'][1]['editable']
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):raise AssertionError('Rebuilt an already used field')
        m.setattr(engine,'build_development_adjacency_field',forbidden)
        m.setattr(engine,'uneven_development_rate_field',forbidden)
        out=tmp_path/'new.ffprocess.npz';revise_work(cp,out,[override(2,'develop_color',time_min=.35)])
        again=tmp_path/'again.ffprocess.npz';revise_work(out,again,[override(2,'develop_color',time_min=.6)])
    after,_,changed=load_checkpoint(again);preserve_past(before,after,pools,changed)
    for name in ('surface_state','inhibitor_state'):
        for key,value in before[name].items():
            if key=='definition':continue
            if isinstance(value,np.ndarray):np.testing.assert_array_equal(value,after[name][key])
            elif key=='fields':
                for field,array in value.items():np.testing.assert_array_equal(array,after[name][key][field])
            else:assert value==after[name][key],(name,key)
    assert len(after['bath_revisions'])==2
    assert after['bath_revisions'][-1]['refreshed_preparation']==['dir']
    np.testing.assert_array_equal(before['spatial_preparation']['arrays']['adjacency'],after['spatial_preparation']['arrays']['adjacency'])
    assert before['spatial_preparation']['audits']['adjacency']==after['spatial_preparation']['audits']['adjacency']
    assert np.any(before['spatial_preparation']['arrays']['dir_0']!=after['spatial_preparation']['arrays']['dir_0'])
    assert_complete(again,tmp_path,monkeypatch)
    entered=tmp_path/'entered.ffprocess.npz';continue_work(again,entered,stop_after=stop+1)
    with pytest.raises(ValueError,match='locked'):
        revise_work(entered,tmp_path/'bad.ffprocess.npz',[override(2,'develop_color',time_min=.1)])


def test_reversal_intervening_bath_refreshes_unentered_colour_dependency(tmp_path,monkeypatch):
    from film_foundry.core import engine
    c=config('color_reversal');c.process_control_mode='expected'
    c.chemistry.development_adjacency_strength=.4;c.chemistry.dir_process_effectiveness=.8
    c.film.dir_material_response_matrix=prepared_config().film.dir_material_response_matrix
    c.chemistry.process_steps=(
        {'action':'develop_silver','time_min':.6},
        {'action':'bleach_silver','time_min':1.},
        {'action':'activate_remaining_halide'},
        {'action':'develop_color','time_min':2.},
        {'action':'bleach_silver'}, {'action':'fix_halide'})
    path=source(tmp_path,c);initial=tmp_path/'zero.ffprocess.npz';start_work(path,initial,c)
    info=describe_work(initial);assert info['baths'][1]['row']['action']=='bleach_silver'
    cp=tmp_path/'mid.ffprocess.npz';continue_work(initial,cp,stop_after=1)
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):raise AssertionError('Recomputed past adjacency')
        m.setattr(engine,'build_development_adjacency_field',forbidden)
        out=tmp_path/'changed.ffprocess.npz';revise_work(cp,out,[override(2,'bleach_silver',time_min=.15)])
    assert load_checkpoint(out)[0]['bath_revisions'][-1]['refreshed_preparation']==['dir']
    assert_complete(out,tmp_path,monkeypatch)


def test_enabled_empty_adjacency_is_recomputed_and_partial_coupled_bath_is_locked(tmp_path,monkeypatch):
    from film_foundry.core import engine
    c=config('bw_negative');c.process_control_mode='expected'
    c.chemistry.development_adjacency_strength=.4
    c.chemistry.process_steps=({'action':'develop_silver','solution':{
        'model':'bath_solution_v1','action':'develop_silver','activity_remaining':0.}}, {'action':'fix_halide'})
    path=source(tmp_path,c);cp=tmp_path/'empty.ffprocess.npz';start_work(path,cp,c)
    before=load_checkpoint(cp)[0]['spatial_preparation']
    assert before['audits']['adjacency']['enabled'] and before['fields']['adjacency'] is None
    out=tmp_path/'active.ffprocess.npz';revise_work(cp,out,[override(1,'develop_silver',time_min=1.)])
    after=load_checkpoint(out)[0]['spatial_preparation'];assert after['fields']['adjacency'] is not None
    assert_complete(out,tmp_path,monkeypatch)
    c=prepared_config();path=source(tmp_path,c);cp=tmp_path/'coupled.ffprocess.npz';start_work(path,cp,c)
    info=describe_work(cp)
    stop=next(i+1 for i,s in enumerate(info['steps']) if s.get('time_history') is None)
    mid=tmp_path/'partial.ffprocess.npz';continue_work(cp,mid,stop_after=stop)
    assert describe_work(mid)['baths'][0]['reason']=='started'
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):raise AssertionError('Locked bath reached numerical preparation')
        m.setattr(engine,'develop_negative_from_latent',forbidden)
        with pytest.raises(ValueError,match='locked'):
            revise_work(mid,tmp_path/'bad.ffprocess.npz',[override(1,'develop_silver',time_min=.1)])

