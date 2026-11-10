"""Only real reaction continuation, frozen preparation and affected entrypoints."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import json
import shutil

import numpy as np
import pytest

from film_foundry.app.film_process_work import start_work, continue_work, describe_work
from film_foundry.core.film_process.checkpoint_io import load_checkpoint, save_checkpoint
from film_foundry.core.engine import expose_image_to_latent, develop_negative_from_latent
from film_foundry.core.latent_io import save_exposed_latent, load_exposed_latent, latent_identity_from_film
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.model.config import DarkroomConfig
from film_foundry.tests.test_unified_workspace import panel,wait


def scene():
    y,x=np.mgrid[:23,:37]
    return np.stack((.05+.8*x/36,.08+.65*y/22,.12+.7*((x>12)&(x<25))),axis=-1).astype(np.float32)


def config(kind='color_negative'):
    c=DarkroomConfig(enable_halation=False,enable_mtf=False,seed_strategy='fixed',random_seed=19)
    c.process_control_mode='principle';c.chemistry.program_key=kind
    if kind.startswith('bw'):c.film.color_process='bw'
    c.output.save_scanner_raw=False;c.output.export_transparent_plate=False
    c.output.export_plate_set=False;c.output.export_layer_pack=False
    c.chemistry.uneven_development=.24
    return c


def source(tmp_path,c):
    path=tmp_path/'source.darkroom_latent.npz'
    latent=expose_image_to_latent(scene(),c,rng=np.random.default_rng(3))
    save_exposed_latent(path,latent,identity={**latent_identity_from_film(c.film),
        'frame_identity':{'roll_id':'test-roll','frame_index':2}})
    return path


def same_medium(a,b):
    for key in ('density_cmy','density_grain','optical_density_rgb','emulsion_coverage'):
        first=getattr(a,key,None);second=getattr(b,key,None)
        if first is None:assert second is None
        else:np.testing.assert_array_equal(first,second,err_msg=key)
    assert json.loads(json.dumps(a.metadata['film_process_model']))==b.metadata['film_process_model']
    assert a.process_surface.keys()==b.process_surface.keys() if a.process_surface is not None else b.process_surface is None
    for key in a.process_surface or {}:np.testing.assert_array_equal(a.process_surface[key],b.process_surface[key])
    if a.spectral_medium is not None:
        np.testing.assert_array_equal(a.spectral_medium.component_amounts,b.spectral_medium.component_amounts)


@pytest.mark.parametrize('kind',['bw_negative','bw_reversal','color_negative','color_reversal'])
def test_all_paths_roundtrip_move_resume_without_repeating_completed_steps(tmp_path,monkeypatch,kind):
    c=config(kind);path=source(tmp_path,c)
    original,_=load_exposed_latent(path)
    expected_rng=np.random.default_rng(19)
    expected=develop_negative_from_latent(original.copy(),c,rng=expected_rng)
    before=path.read_bytes();cp=tmp_path/'first.ffprocess.npz'
    paused=start_work(path,cp,c,stop_after=1)
    assert paused.status=='requested_boundary' and paused.next_step==1
    metadata,latent,state=load_checkpoint(cp)
    assert np.any(state.metallic_silver>0)
    np.testing.assert_array_equal(latent.halide,original.halide)
    assert state.developability.size>0 and state.formation_history is not None
    if not kind.startswith('bw'):assert state.coupler is not None
    assert path.read_bytes()==before
    moved=tmp_path/'moved.ffprocess.npz';shutil.move(cp,moved);path.unlink()
    from film_foundry.core.film_process import operators
    real=operators._apply_step;actions=[]
    def count(state,step,*args,**kwargs):
        actions.append(step.action.value);return real(state,step,*args,**kwargs)
    monkeypatch.setattr(operators,'_apply_step',count)
    result=continue_work(moved,tmp_path/'finished.npz')
    assert actions==[s['action'] for s in metadata['boundary']['binding']['program']['steps'][1:]]
    got=load_developed_negative_npz(result.output_path);same_medium(expected,got)
    assert got.metadata['frame_identity']=={'roll_id':'test-roll','frame_index':2}
    assert result.status=='completed'
    # Finishing must not compact the reusable checkpoint's arrays on disk.
    np.testing.assert_array_equal(load_checkpoint(moved)[2].developability,state.developability)


@pytest.mark.parametrize('spectral',[False,True])
def test_complex_accidents_history_and_final_grain_match_continuous(tmp_path,spectral):
    from film_foundry.tests.test_emulsion_shear import config as shear_config,SHEAR
    from film_foundry.tests.test_emulsion_integrity import WEAR
    from film_foundry.tests.test_film_time_history import history
    from film_foundry.contracts.film_time import validate_response
    from film_foundry.contracts.carbon_backing import validate_carbon_backing
    c=shear_config({'kind':'bath_contact','bath':2,'fill_s':80.}, {'kind':'contaminate','after_bath':1},
        {'kind':'silver_bath','after_bath':1}, {**WEAR,'after_bath':1},
        {**SHEAR,'after_bath':1},
        {'kind':'backing_wash','after_bath':2})
    c.enable_grain=True;c.random_seed=19;c.film.time_response=validate_response({'model':'film_time_response_v2'})
    # This assertion audits formation/continuation, not lossy archive budgets.
    c.output.material_storage_precision='full'
    c.film.carbon_backing=validate_carbon_backing({})
    c.chemistry.latent_wait=history('latent',30.)
    c.chemistry.post_process_light=history('light',100.)
    c.chemistry.process_steps=(*c.chemistry.process_steps[:2],{'action':'fix_halide','strength':.3})
    if spectral:
        from film_foundry.tests.test_spectral_grain import configuration
        c.film.spectral_material_profile=configuration().film.spectral_material_profile
    path=source(tmp_path,c);latent,_=load_exposed_latent(path)
    expected=develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19))
    zero=tmp_path/'zero.ffprocess.npz';start_work(path,zero,c,stop_after=0)
    info=describe_work(zero);steps=info['steps']
    stop=next(i+1 for i,s in enumerate(steps) if s['action']=='shear_emulsion')
    one=tmp_path/'one.ffprocess.npz';continue_work(zero,one,stop_after=stop)
    metadata,_,state=load_checkpoint(one)
    assert state.formation_history and state.emulsion_removed and state.emulsion_outside
    final=continue_work(one,tmp_path/'finished.npz');same_medium(expected,load_developed_negative_npz(final.output_path))
    # A snapshot at the last reaction is still a process state, not a medium.
    end=tmp_path/'end.ffprocess.npz';continue_work(one,end,stop_after=len(steps))
    again=continue_work(end,tmp_path/'again.npz');same_medium(expected,load_developed_negative_npz(again.output_path))


def test_cancellation_saves_last_boundary_and_failed_write_keeps_old(tmp_path,monkeypatch):
    from film_foundry.app.control import WorkflowControl,CancelFlag
    from film_foundry.core import atomic_io
    c=config();path=source(tmp_path,c);cp=tmp_path/'cancel.ffprocess.npz'
    flag=CancelFlag()
    def progress(event):
        if event.fraction>0:flag.cancel()
    paused=start_work(path,cp,c,stop_after=2,control=WorkflowControl(cancel_requested=flag.requested,on_progress=progress))
    assert paused.status=='cancelled' and paused.next_step==1
    before=cp.read_bytes()
    real=atomic_io.os.replace
    def fail(src,dst):
        if Path(dst)==cp:raise OSError('injected publish failure')
        return real(src,dst)
    monkeypatch.setattr(atomic_io.os,'replace',fail)
    with pytest.raises(OSError,match='injected'):start_work(path,cp,c,stop_after=2)
    assert cp.read_bytes()==before
    with pytest.raises(ValueError,match='preserve'):continue_work(cp,cp,stop_after=2)
    with pytest.raises(ValueError,match='backwards'):continue_work(cp,tmp_path/'earlier.ffprocess.npz',stop_after=0)
    altered,latent,state=load_checkpoint(cp);altered['config']['chemistry']['time_min']*=2
    changed=tmp_path/'changed.ffprocess.npz';save_checkpoint(changed,altered,latent,state)
    with pytest.raises(ValueError,match='differs'):continue_work(changed,tmp_path/'bad.npz')


def test_real_cli_prepare_show_continue_and_scanner_reads_only_final(tmp_path,capsys):
    from film_foundry.app.cli import main
    from film_foundry.model.session import save_session,session_payload
    from film_foundry.core.scan_runtime import scan_medium_output
    c=config();path=source(tmp_path,c);session=tmp_path/'session.json'
    save_session(session,session_payload(config=c,pipeline_mode='develop_latent',input_path=str(path),negative_path='',output_path='',presets={}))
    cp=tmp_path/'work.ffprocess.npz';out=tmp_path/'final.npz'
    assert main(['film-process','start',str(path),str(cp),'--session',str(session),'--stop-after','1'])==0
    assert main(['film-process','show',str(cp)])==0
    capsys.readouterr()
    assert main(['film-process','continue',str(cp),str(out)])==0
    assert 'completed' in capsys.readouterr().out
    with pytest.raises(ValueError):load_exposed_latent(cp)
    with pytest.raises(ValueError):load_developed_negative_npz(cp)
    medium=load_developed_negative_npz(out);before=medium.optical_density_rgb.copy()
    scan_medium_output(medium,c)
    np.testing.assert_array_equal(before,medium.optical_density_rgb)


def test_random_variation_and_preparation_keep_initial_rng_and_optional_state(tmp_path):
    c=config();c.seed_strategy='random';c.chemistry.process_variation=.3
    c.chemistry.development_adjacency_strength=.15
    path=source(tmp_path,c);latent,identity=load_exposed_latent(path)
    latent.original_developability=latent.developability.copy()
    save_exposed_latent(path,latent,identity=identity)
    cp=tmp_path/'random.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    metadata,frozen,state=load_checkpoint(cp)
    assert state.original_developability is not None
    from film_foundry.app.film_process_work import _rng
    expected=develop_negative_from_latent(frozen.copy(),DarkroomConfig.from_dict(metadata['config']),rng=_rng(metadata['rng_initial']))
    result=continue_work(cp,tmp_path/'finished.npz')
    same_medium(expected,load_developed_negative_npz(result.output_path))


def test_embedded_page_runs_restores_and_defers_close_until_saved(panel,tmp_path,monkeypatch):
    from threading import Event
    from film_foundry.ui.qt import QtCore
    c=config();path=source(tmp_path,c);monkeypatch.setattr(panel,'_config',lambda:c)
    page=panel.open_film_process_workspace()
    assert not page.isWindow()
    cp=tmp_path/'zero.ffprocess.npz';assert page.start_from(path,cp)
    wait(lambda:not page.busy);assert page.last_error is None and page.info['next_step']==0
    panel.show_workbench();panel.open_film_process_workspace();assert panel.workspace_host.currentWidget() is page
    first=tmp_path/'first.ffprocess.npz';assert page.advance_to(first,1)
    wait(lambda:not page.busy);assert page.path==first and page.steps.topLevelItem(0).text(3)=='已执行'
    records=[];page.result_ready.connect(records.append)
    assert page.advance_to(tmp_path/'final.npz');wait(lambda:not page.busy)
    assert page.last_error is None and len(records)==1 and page.view_button.isEnabled()
    gate=Event();original=page._submit
    assert original(lambda control: (gate.wait(4),(first,describe_work(first)))[1])
    assert not page.close() and page.busy
    gate.set();wait(lambda:not page.busy)
    assert panel.workspace_host.find_page('film-process') is None
