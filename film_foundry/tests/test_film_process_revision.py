"""A future-bath branch must preserve real past material and preparation."""
from copy import deepcopy
from dataclasses import fields
import json
import numpy as np
import pytest

from film_foundry.tests.test_film_process_work import source,config,same_medium,panel,wait
from film_foundry.tests.test_film_preparation_checkpoint import prepared_config
from film_foundry.app.film_process_work import start_work,continue_work,describe_work
from film_foundry.app.film_process_revision import revise_work
from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.core.engine import develop_negative_from_latent
from film_foundry.model.config import DarkroomConfig


def override(bath,action,**kw):
    return dict(bath=bath,action=action,solution=dict(model='bath_solution_v1',action=action),**kw)


def same_pools(a,b):
    for f in fields(a):
        x=getattr(a,f.name);y=getattr(b,f.name)
        if isinstance(x,np.ndarray):np.testing.assert_array_equal(x,y,err_msg=f.name)
        elif f.name=='formation_history' and x is not None:
            assert x.keys()==y.keys()
            for key in x:np.testing.assert_array_equal(x[key],y[key])
        else:assert x==y,f.name


@pytest.mark.parametrize('spectral',[False,True])
def test_future_coupled_bath_preserves_past_and_matches_complete_new_program(tmp_path,monkeypatch,spectral):
    from film_foundry.core import engine
    from film_foundry.core.film_process import operators
    c=prepared_config()
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        c.film.spectral_material_profile=configuration().film.spectral_material_profile
    path=source(tmp_path,c);initial=tmp_path/'initial.ffprocess.npz';start_work(path,initial,c)
    steps=describe_work(initial)['steps'];stop=next(i for i,s in enumerate(steps) if s['bath_index']==2)
    cp=tmp_path/'mid.ffprocess.npz';continue_work(initial,cp,stop_after=stop)
    before,latent,pools=load_checkpoint(cp)
    update=override(3,'bleach_silver',time_min=.2,temperature_c=32.,agitation=.2)
    update['solution'].update(model='bath_solution_v2',hardener_g_l=.4,
        secondary=dict(action='fix_halide',rate_per_minute=.18))
    real=operators._apply_step;calls=[]
    def track(state,*a,**kw):
        if state.halide.shape==pools.halide.shape:calls.append(1)
        return real(state,*a,**kw)
    monkeypatch.setattr(operators,'_apply_step',track)
    out=tmp_path/'branch.ffprocess.npz'
    with monkeypatch.context() as m:
        def forbidden(*a,**kw):raise AssertionError('A revision rebuilt saved spatial preparation')
        for name in ('build_development_adjacency_field','build_dir_material_response_field','uneven_development_rate_field'):
            m.setattr(engine,name,forbidden)
        result=revise_work(cp,out,[update])
    assert not calls and result.next_step==stop and result.status=='revised'
    after,_,changed=load_checkpoint(out);same_pools(pools,changed)
    assert after['boundary']['trace']==before['boundary']['trace']
    for key in ('surface_state','inhibitor_state'):
        for name,value in before[key].items():
            if name=='definition':continue
            got=after[key][name]
            if isinstance(value,np.ndarray):np.testing.assert_array_equal(value,got)
            elif name=='fields':
                for n,array in value.items():np.testing.assert_array_equal(array,got[n])
            else:assert value==got,(key,name)
    for key,a in before['spatial_preparation']['arrays'].items():
        np.testing.assert_array_equal(a,after['spatial_preparation']['arrays'][key])
    assert after['spatial_preparation']['rng_after']==before['spatial_preparation']['rng_after']
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(after['config']),rng=np.random.default_rng(19))
    path.unlink();cp.unlink();moved=tmp_path/'moved.ffprocess.npz';out.rename(moved)
    final=continue_work(moved,tmp_path/'finished.npz');got=load_developed_negative_npz(final.output_path)
    same_medium(expected,got)
    assert got.metadata['film_process_continuation']['bath_revisions']==after['bath_revisions']
    # Another revision also branches without losing the previous record.
    again=tmp_path/'again.ffprocess.npz';revise_work(moved,again,[override(4,'fix_halide',time_min=.25)])
    assert len(load_checkpoint(again)[0]['bath_revisions'])==2


def test_first_bath_before_entry_and_saved_wet_history(tmp_path):
    c=config('bw_negative');path=source(tmp_path,c);cp=tmp_path/'zero.ffprocess.npz';start_work(path,cp,c)
    out=tmp_path/'revised.ffprocess.npz';revise_work(cp,out,[override(1,'develop_silver',time_min=.7,temperature_c=27.)])
    meta,latent,_=load_checkpoint(out)
    assert meta['surface_state']['active'] and meta['surface_state']['wet_seconds']==0.
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(meta['config']),rng=np.random.default_rng(19))
    got=continue_work(out,tmp_path/'finished.npz');same_medium(expected,load_developed_negative_npz(got.output_path))
    mid=tmp_path/'original-mid.ffprocess.npz';continue_work(cp,mid,stop_after=1)
    revised=tmp_path/'wet-history.ffprocess.npz'
    revise_work(mid,revised,[override(2,'fix_halide',temperature_c=31.)])
    assert load_checkpoint(revised)[0]['surface_state']['wet_seconds']==load_checkpoint(mid)[0]['surface_state']['wet_seconds']


def test_locked_dependencies_started_baths_old_archives_and_cancel(tmp_path,monkeypatch):
    c=prepared_config();path=source(tmp_path,c);cp=tmp_path/'zero.ffprocess.npz';start_work(path,cp,c)
    info=describe_work(cp)
    assert [r['editable'] for r in info['baths']]==[True,True,True,True]
    started=next(i+1 for i,s in enumerate(info['steps']) if s.get('time_history') is None)
    mid=tmp_path/'mid.ffprocess.npz';continue_work(cp,mid,stop_after=started)
    assert describe_work(mid)['baths'][0]['reason']=='started'
    with pytest.raises(ValueError,match='locked'):
        revise_work(mid,tmp_path/'invalid.ffprocess.npz',[override(1,'develop_silver',time_min=.1)])
    with pytest.raises(ValueError,match='source bath action'):
        revise_work(mid,tmp_path/'invalid.ffprocess.npz',[override(3,'fix_halide')])
    from film_foundry.app.control import WorkflowControl,WorkflowCancelled
    flag={'cancel':False}
    def progress(event):flag['cancel']=True
    # Cancel as soon as revision preparation is reported, before publishing.
    control=WorkflowControl(cancel_requested=lambda:flag['cancel'],on_progress=progress)
    with pytest.raises(WorkflowCancelled):
        revise_work(cp,tmp_path/'cancel.ffprocess.npz',[override(3,'bleach_silver')],control=control)
    assert not (tmp_path/'cancel.ffprocess.npz').exists()
    from film_foundry.app.film_process_work import _run
    meta,latent,_=load_checkpoint(cp);meta['scope']='frozen_program_wet_inventory_v3'
    old=tmp_path/'v3.ffprocess.npz';_run(path,old,latent,DarkroomConfig.from_dict(meta['config']),meta,None,0,None)
    with pytest.raises(ValueError,match='v4'):
        revise_work(old,tmp_path/'invalid.ffprocess.npz',[override(3,'bleach_silver')])


def test_actual_cli_and_embedded_bath_editor(tmp_path,monkeypatch,capsys):
    from film_foundry.app.cli import main
    from film_foundry.ui.film_process_workspace import FilmProcessWorkspace
    from film_foundry.ui.qt import QtWidgets,QtCore
    c=prepared_config();path=source(tmp_path,c);cp=tmp_path/'start.ffprocess.npz';start_work(path,cp,c)
    changes=tmp_path/'baths.json';changes.write_text(json.dumps([override(3,'bleach_silver',time_min=.2)]),encoding='utf-8')
    cli=tmp_path/'cli.ffprocess.npz'
    assert main(['film-process','revise',str(cp),str(cli),'--baths',str(changes)])==0
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    page=FilmProcessWorkspace(lambda:c);page.show()
    assert page.open_from(cli);wait(lambda:not page.busy)
    assert page.last_error is None and page.edit_bath_button.isEnabled()
    page.edit_bath_button.click();editor=page.bath_editor
    assert editor is not None and editor.windowType()==QtCore.Qt.WindowType.Widget
    editor.controls['time_min'].setValue(.35)
    assert editor.controls['agitation_time_s'].value()==21.
    target=tmp_path/'ui.ffprocess.npz'
    monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(target),''))
    editor.apply();assert not editor.status.text();wait(lambda:not page.busy)
    assert page.last_error is None and page.path==target and page.info['next_step']==0
    assert len(page.info['bath_revisions'])==2
    saved=load_checkpoint(target)[0]
    assert saved['bath_revisions'][-1]['after'][0]['agitation_time_s'] is None
    assert saved['bath_revisions'][-1]['refreshed_preparation']==['adjacency','dir']
    page.close()


def test_latent_wait_has_not_entered_first_bath(tmp_path):
    from film_foundry.tests.test_film_time_history import history
    from film_foundry.contracts.film_time import validate_response
    c=config('bw_negative');c.film.time_response=validate_response({'model':'film_time_response_v1'})
    c.chemistry.latent_wait=history('latent',2.)
    path=source(tmp_path,c);cp=tmp_path/'wait.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    assert describe_work(cp)['baths'][0]['editable']
    out=tmp_path/'revised.ffprocess.npz';revise_work(cp,out,[override(1,'develop_silver',time_min=.7,temperature_c=27.)])
    meta,latent,pools=load_checkpoint(out)
    same_pools(load_checkpoint(cp)[2],pools)
    assert meta['surface_state']['next_step']==1 and meta['surface_state']['wet_seconds']==0.
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(meta['config']),rng=np.random.default_rng(19))
    result=continue_work(out,tmp_path/'finished.npz');same_medium(expected,load_developed_negative_npz(result.output_path))
