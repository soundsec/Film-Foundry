from copy import deepcopy
from dataclasses import asdict
from threading import Event
import numpy as np
import pytest
from PIL import Image

from film_foundry.model.config import DarkroomConfig
from film_foundry.app.film_test_strip import available_axes,build_variants,FilmStripSession,bath_rows
from film_foundry.app.control import CancelFlag,WorkflowCancelled
from film_foundry.tests.test_desktop_usability import desktop,photos,wait


def axis_for(config,mode,path):
    return next(a for a in available_axes(config,mode) if a.path==path)


def test_strip_uses_real_pipeline_same_identity_and_isolates_recipe(tmp_path):
    path=tmp_path/'source.png'
    pixels=np.tile(np.arange(24,dtype=np.uint8)[None,:,None]*9,(16,1,3));Image.fromarray(pixels).save(path)
    base=DarkroomConfig(enable_halation=False,enable_mtf=False);before=asdict(base)
    axis=axis_for(base,'full',('look','exposure_ev'))
    variants=build_variants(base,'full',axis,-1,1,3)
    session=FilmStripSession(path,'full',base,long_edge=32)
    first=session.render(variants[0][1]);middle=session.render(variants[1][1]);again=session.render(variants[0][1])
    np.testing.assert_array_equal(first,again)
    assert not np.allclose(first,middle) and asdict(base)==before
    assert variants[0][1].seed_strategy=='random' and variants[0][1].random_seed is None
    result=[];cancel=CancelFlag()
    def received(i,value,pixels):result.append(i);cancel.cancel()
    with pytest.raises(WorkflowCancelled):session.run(variants,cancel,received)
    assert result==[0]
    Image.new('RGB',(25,16),(10,20,30)).save(path)
    with pytest.raises(ValueError,match='Source changed'):session.check_source()


def test_single_bath_keeps_sequence_and_frozen_axes_and_validates_ranges():
    base=DarkroomConfig();before=asdict(base);rows=bath_rows(base)
    axis=next(a for a in available_axes(base,'develop_latent') if a.path[0]=='bath' and a.path[-1]=='temperature_c')
    variants=build_variants(base,'develop_latent',axis,19,23,3)
    for value,config in variants:
        changed=bath_rows(config)
        assert [r['action'] for r in changed]==[r['action'] for r in rows]
        assert changed[axis.path[1]]['temperature_c']==value
        for i,row in enumerate(rows):
            if i!=axis.path[1]:assert changed[i]==row
    assert asdict(base)==before
    assert all(a.stage=='develop' for a in available_axes(base,'develop_latent'))
    assert all(a.stage=='scan' for a in available_axes(base,'scan'))
    frozen=axis_for(base,'full',('look','exposure_ev'))
    with pytest.raises(ValueError,match='frozen'):build_variants(base,'scan',frozen,-1,1,3)
    with pytest.raises(ValueError):build_variants(base,'develop_latent',axis,-2,80,3)


def test_latent_and_medium_trials_observe_and_preserve_sources(tmp_path):
    from film_foundry.tests.test_film_task_navigation import formed
    _,latent,developed,base=formed(tmp_path)
    files=(latent.output_path,developed.medium_path);stamps={p:p.stat().st_mtime_ns for p in files}
    for mode,path,field,start,stop in (
        ('develop_latent',latent.output_path,('chemistry','temperature_c'),18,25),
        ('scan',developed.medium_path,('look','print_exposure_ev'),-1,1)):
        variants=build_variants(base,mode,axis_for(base,mode,field),start,stop,2)
        session=FilmStripSession(path,mode,base,long_edge=32);results=[]
        session.run(variants,CancelFlag(),lambda i,v,p:results.append(p.copy()))
        assert len(results)==2 and not np.allclose(results[0],results[1])
    assert stamps=={p:p.stat().st_mtime_ns for p in files}


def test_desktop_strip_adoption_and_undo_do_not_adopt_preview_seed(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film');panel.auto_preview.set(False)
    wait(lambda:not panel._preview_running)
    before=panel.edit_tools.capture();dialog=panel._open_test_strip()
    assert dialog is not None,panel.status.get()
    index=next(i for i in range(dialog.axis.count()) if dialog.axis.itemData(i).path==('look','exposure_ev'))
    dialog.axis.setCurrentIndex(index);dialog.count.setValue(3);dialog.start.setValue(-.5);dialog.stop.setValue(.5)
    dialog.generate();wait(lambda:not dialog.busy)
    assert len(dialog.frames)==3,dialog.status.text()
    assert panel.edit_tools.capture()==before
    dialog.tiles.setCurrentRow(2);dialog.adopt()
    assert panel._config().look.exposure_ev==.5
    assert panel._config().seed_strategy==before['config']['seed_strategy']
    assert panel._config().random_seed==before['config']['random_seed']
    assert panel._test_strip_dialog is None
    panel.edit_tools.travel(False);assert panel.edit_tools.capture()==before


def test_strip_close_waits_for_worker_and_discards_without_applying(tmp_path):
    from film_foundry.ui.film_test_strip import FilmTestStripDialog
    from film_foundry.ui.qt import _application
    _application();entered=Event();release=Event();applied=[]
    class Slow(FilmStripSession):
        def render(self,config):
            entered.set();release.wait(5);return np.zeros((16,24,3),np.float32)
    path=tmp_path/'source.png';Image.new('RGB',(24,16)).save(path);base=DarkroomConfig()
    dialog=FilmTestStripDialog(path,'full',base,base,lambda *args:applied.append(args),renderer=Slow)
    dialog.show();dialog.generate();wait(entered.is_set)
    dialog.reject();assert dialog.busy and dialog.isVisible() and dialog.cancel.requested()
    release.set();wait(lambda:not dialog.busy)
    assert not dialog.isVisible() and not applied
    dialog.close()


@pytest.mark.parametrize('kind',['material','bath'])
def test_strip_adopts_advanced_material_and_principle_bath_without_losing_fields(desktop,tmp_path,kind):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film');wait(lambda:not panel._preview_running)
    if kind=='bath':panel.mode_pickers['develop'].buttons['principle'].click()
    before=panel.edit_tools.capture();base=DarkroomConfig.from_dict(before['config'])
    if kind=='material':axis=axis_for(base,'full',('film','halation_threshold'));start,stop=.5,.7
    else:axis=next(a for a in available_axes(base,'full') if a.path[0]=='bath' and a.path[-1]=='temperature_c');start,stop=19,23
    variants=build_variants(base,'full',axis,start,stop,2);dialog=panel._open_test_strip()
    assert dialog is not None,panel.status.get()
    dialog.apply_result(variants[1][1],axis);dialog.accept()
    assert asdict(panel._config())==asdict(variants[1][1])
    panel.edit_tools.travel(False);assert panel.edit_tools.capture()==before


def test_main_window_exit_cancels_strip_and_waits_for_the_worker(desktop,tmp_path,monkeypatch):
    from film_foundry.ui.qt import QtWidgets
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film');wait(lambda:not panel._preview_running)
    dialog=panel._open_test_strip();entered=Event();release=Event()
    class Slow(FilmStripSession):
        def render(self,config):
            entered.set();release.wait(5);return np.zeros((16,24,3),np.float32)
    dialog.renderer=Slow;dialog.generate();wait(entered.is_set)
    monkeypatch.setattr(QtWidgets.QMessageBox,'question',lambda *args,**kwargs:QtWidgets.QMessageBox.StandardButton.Yes)
    panel.work_sessions.bypass=True
    assert not panel._allow_window_close() and dialog.cancel.requested() and dialog.closing
    assert panel.root.winfo_exists()
    release.set();wait(lambda:not panel.root.winfo_exists())


def test_native_bath_current_trial_matches_its_reference(desktop,tmp_path):
    panel=desktop;panel._open_catalog_batch(photos(tmp_path),'film');wait(lambda:not panel._preview_running)
    before=panel.edit_tools.capture();dialog=panel._open_test_strip()
    index=next(i for i in range(dialog.axis.count()) if dialog.axis.itemData(i).path[0]=='bath' and dialog.axis.itemData(i).path[-1]=='temperature_c')
    dialog.axis.setCurrentIndex(index);axis=dialog.axis.currentData()
    dialog.start.setValue(axis.current-1);dialog.stop.setValue(axis.current+1);dialog.count.setValue(3)
    dialog.generate();wait(lambda:not dialog.busy)
    assert len(dialog.frames)==3,dialog.status.text()
    np.testing.assert_array_equal(dialog.reference,dialog.frames[1])
    assert not np.allclose(dialog.frames[0],dialog.frames[2])
    assert panel.edit_tools.capture()==before
    dialog.reject()
