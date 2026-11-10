"""Dormant optical surfaces still own real wet history in v5 checkpoints."""
from copy import deepcopy
import json
import numpy as np
import pytest

from film_foundry.tests.test_film_process_work import config,source,same_medium,wait
from film_foundry.tests.test_film_process_revision import override,same_pools
from film_foundry.app.film_process_work import start_work,continue_work,describe_work,_run
from film_foundry.app.film_process_revision import revise_work
from film_foundry.core.film_process.checkpoint_io import load_checkpoint,save_checkpoint
from film_foundry.core.engine import develop_negative_from_latent
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.model.config import DarkroomConfig


def legacy_v4(path,output,c,stop_after=1):
    current=output.with_name('bootstrap.ffprocess.npz');start_work(path,current,c)
    meta,latent,_=load_checkpoint(current);meta['scope']='frozen_program_prepared_inventory_v4'
    return _run(path,output,latent,DarkroomConfig.from_dict(meta['config']),meta,None,stop_after,None)


def test_dormant_history_advances_without_fields_or_replaying_events(tmp_path,monkeypatch):
    from film_foundry.core.film_process.surface import SurfaceProcessSession
    from film_foundry.app import film_process_work
    c=config('bw_negative');path=source(tmp_path,c);cp=tmp_path/'zero.ffprocess.npz';start_work(path,cp,c)
    meta,latent,_=load_checkpoint(cp);wet=meta['surface_state']
    assert meta['kind']=='film_process_work_v5' and wet['model']=='film_surface_boundary_v2'
    assert not wet['active'] and 'fields' not in wet and wet['wet_seconds']==0 and wet['cursor']==0
    expected_rng=np.random.default_rng(19)
    expected=develop_negative_from_latent(latent.copy(),c,rng=expected_rng)
    original=SurfaceProcessSession.apply_event;events=[]
    def tracked(self,event):
        if self.track_wet_history:events.append(event['bath'])
        return original(self,event)
    monkeypatch.setattr(SurfaceProcessSession,'apply_event',tracked)
    previous=cp
    for step in range(1,len(meta['boundary']['binding']['program']['steps'])+1):
        out=tmp_path/f'{step}.ffprocess.npz';continue_work(previous,out,stop_after=step)
        saved=load_checkpoint(out)[0]['surface_state']
        assert not saved['active'] and saved['cursor']==step
        duration=meta['config']['chemistry']['time_min']*60
        transfer=meta['config']['chemistry']['bath_transfer_s']
        assert saved['wet_seconds']==duration*step+transfer*(step-1)
        assert saved['initial_wet_credit']==0 and events==list(range(1,step+1))
        with np.load(out,allow_pickle=False) as archive:assert not any(n.startswith('surface_') for n in archive.files)
        previous=out
    generators=[];original_rng=film_process_work._rng
    def rng(value):
        generator=original_rng(value);generators.append(generator);return generator
    monkeypatch.setattr(film_process_work,'_rng',rng)
    final=continue_work(previous,tmp_path/'finished.npz')
    same_medium(expected,load_developed_negative_npz(final.output_path))
    assert generators[-1].bit_generator.state==expected_rng.bit_generator.state
    # A program containing only activation has no wet-bath reference to credit.
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    empty=config('bw_negative');empty.chemistry.process_steps=({'action':'activate_remaining_halide'},)
    program=program_from_develop_recipe(empty.chemistry,mode='bw_negative',layer_count=1)
    dry=SurfaceProcessSession(empty,(23,37),program=program,track_wet_history=True)
    dry.bind(program);dry.before_step(0);dry.after_step(0);dry.finish()
    assert dry.snapshot()['wet_seconds']==0. and dry.fields is None


def test_dormant_to_hot_local_hardening_keeps_clock_and_matches_continuous(tmp_path,monkeypatch):
    from film_foundry.core.film_process.surface import SurfaceProcessSession
    c=config('bw_negative')
    c.chemistry.process_events={'model':'film_process_events_v11','events':[{'kind':'bath_contact','bath':2,'fill_s':45.,'immersion_fraction':.8}]}
    path=source(tmp_path,c);cp=tmp_path/'mid.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    before,latent,pools=load_checkpoint(cp);old=before['surface_state'];assert not old['active']
    update=override(2,'fix_halide',temperature_c=55.,time_min=.8)
    update['solution']['hardener_g_l']=.3
    out=tmp_path/'branch.ffprocess.npz'
    with monkeypatch.context() as m:
        m.setattr(SurfaceProcessSession,'advance',lambda self:pytest.fail('Replayed past wet history during revision'))
        revise_work(cp,out,[update])
    changed,_,current=load_checkpoint(out);wet=changed['surface_state'];same_pools(pools,current)
    assert wet['active'] and not any(np.any(v) for v in wet['fields'].values())
    for key in ('temperature','wet_seconds','hardening','cursor','trace','initial_wet_credit'):assert wet[key]==old[key]
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(changed['config']),rng=np.random.default_rng(19))
    path.unlink();cp.unlink();moved=tmp_path/'moved.ffprocess.npz';out.rename(moved)
    next_path=tmp_path/'fixed.ffprocess.npz';continue_work(moved,next_path,stop_after=2)
    fixed=load_checkpoint(next_path)[0]['surface_state']
    assert np.max(fixed['fields']['emulsion_damage'])>0 and np.ndim(fixed['hardening'])==2
    assert np.ptp(fixed['hardening'])>0
    result=continue_work(next_path,tmp_path/'finished.npz');same_medium(expected,load_developed_negative_npz(result.output_path))


def test_removing_future_surface_trigger_preserves_existing_history(tmp_path):
    c=config('bw_negative');c.chemistry.bath_overrides=(override(2,'fix_halide',temperature_c=55.),)
    path=source(tmp_path,c);cp=tmp_path/'mid.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    old,latent,pools=load_checkpoint(cp);assert old['surface_state']['active']
    out=tmp_path/'uniform.ffprocess.npz';revise_work(cp,out,[override(2,'fix_halide',temperature_c=c.chemistry.temperature_c)])
    meta,_,current=load_checkpoint(out);same_pools(pools,current)
    assert meta['surface_state']['active'] and meta['surface_state']['wet_seconds']==old['surface_state']['wet_seconds']
    result=continue_work(out,tmp_path/'finished.npz');got=load_developed_negative_npz(result.output_path)
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(meta['config']),rng=np.random.default_rng(19))
    for name in ('density_cmy','density_grain','optical_density_rgb'):np.testing.assert_array_equal(getattr(got,name),getattr(expected,name))
    assert not any(np.any(a) for a in got.process_surface.values())


def test_old_inactive_v4_cannot_invent_history_and_corrupt_v5_is_rejected(tmp_path):
    c=config('bw_negative');path=source(tmp_path,c);old=tmp_path/'legacy.ffprocess.npz';legacy_v4(path,old,c)
    meta,latent,_=load_checkpoint(old)
    assert meta['kind']=='film_process_work_v4' and 'wet_seconds' not in meta['surface_state']
    with pytest.raises(ValueError,match='wet history absent'):
        revise_work(old,tmp_path/'invalid.ffprocess.npz',[override(2,'fix_halide',temperature_c=55.)])
    result=continue_work(old,tmp_path/'old-finish.npz')
    same_medium(develop_negative_from_latent(latent.copy(),c,rng=np.random.default_rng(19)),load_developed_negative_npz(result.output_path))
    cp=tmp_path/'new.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    meta,latent,state=load_checkpoint(cp)
    for field,value in (('wet_seconds',-1.),('cursor',0),('light_done',True)):
        bad=deepcopy(meta);bad['surface_state'][field]=value
        out=tmp_path/f'{field}.ffprocess.npz';save_checkpoint(out,bad,latent,state)
        with pytest.raises(ValueError):continue_work(out,tmp_path/f'{field}.npz')


def test_embedded_future_temperature_uses_saved_dormant_history(tmp_path,monkeypatch):
    from film_foundry.ui.film_process_workspace import FilmProcessWorkspace
    from film_foundry.ui.qt import QtWidgets
    app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    c=config('bw_negative');path=source(tmp_path,c);cp=tmp_path/'mid.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    page=FilmProcessWorkspace(lambda:c);page.show();page.open_from(cp);wait(lambda:not page.busy)
    page.edit_bath_button.click();editor=page.bath_editor
    assert editor.bath==2
    editor.controls['temperature_c'].setValue(55.)
    out=tmp_path/'branch.ffprocess.npz'
    monkeypatch.setattr(QtWidgets.QFileDialog,'getSaveFileName',lambda *a,**k:(str(out),''))
    editor.apply();wait(lambda:not page.busy)
    assert page.last_error is None and page.info['next_step']==1
    assert load_checkpoint(out)[0]['surface_state']['wet_seconds']==load_checkpoint(cp)[0]['surface_state']['wet_seconds']
    finish=tmp_path/'finished.npz';page.advance_to(finish);wait(lambda:not page.busy)
    assert page.last_error is None and page.final_path==finish
    page.close()


def test_principle_bypass_bath_numbering_and_fixed_compatibility_stage(tmp_path):
    c=config('color_negative_bleach_bypass');path=source(tmp_path,c)
    cp=tmp_path/'mid.ffprocess.npz';start_work(path,cp,c,stop_after=1)
    info=describe_work(cp);rows=info['baths']
    assert [r['row']['action'] for r in rows]==['develop_color','fix_halide','remove_auxiliary','destroy_dye']
    assert rows[1]['editable'] and not rows[-1]['editable']
    out=tmp_path/'branch.ffprocess.npz';revise_work(cp,out,[override(2,'fix_halide',time_min=.2)])
    meta,latent,_=load_checkpoint(out)
    expected=develop_negative_from_latent(latent.copy(),DarkroomConfig.from_dict(meta['config']),rng=np.random.default_rng(19))
    result=continue_work(out,tmp_path/'finished.npz');same_medium(expected,load_developed_negative_npz(result.output_path))
