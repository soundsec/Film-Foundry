"""Catalog preview ownership, selection transactions and unscaled unit input."""
from copy import deepcopy
from threading import Event
import numpy as np
import pytest
from film_foundry.tests.test_desktop_usability import desktop,photos,wait
from film_foundry.ui.qt import QtCore,QtWidgets,_application
from film_foundry.app.control import WorkflowControl
from PySide6.QtTest import QTest


def choose(dialog,key):
    for row in range(dialog.choices.count()):
        if dialog.choices.item(row).data(QtCore.Qt.ItemDataRole.UserRole)['key']==key:
            dialog.choices.setCurrentRow(row);return
    raise AssertionError(key)


def test_current_material_cancel_apply_pairing_and_frozen_guard(desktop,tmp_path):
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film')
    p.exposure_ev.set(.75);before=deepcopy(p._config())
    assert not p.material_selection_model.qt.isVisible() and p.change_material_button.qt.isVisible()
    p.change_material_button.qt.click();d=p._material_chooser
    choose(d,'monochrome_reversal_transparency');d.reject()
    assert p._config()==before
    p.change_material_button.qt.click();d=p._material_chooser
    choose(d,'monochrome_reversal_transparency');d.confirm()
    assert p.film_preset.get()=='monochrome_reversal_transparency'
    assert p.develop_preset.get()=='standard_bw_reversal'
    assert p.current_material_name.qt.text()==p.film_preset_display.get()
    p.pipeline_mode.set('develop_latent');p._update_mode_visibility()
    assert not p.change_material_button.qt.isEnabled()
    p._choose_work_material();assert p._material_chooser is d


def test_thumbnail_queue_rejects_old_source_and_releases_without_blocking(desktop,monkeypatch):
    from film_foundry.ui.material_chooser import MaterialChooser
    from film_foundry.app.selection_preview import SelectionPreviewSession
    started=Event();release=Event();cleared=Event();calls=[]
    original=SelectionPreviewSession.clear
    def render(self,record,use_source,control):
        calls.append(use_source)
        if len(calls)==1:started.set();release.wait(5)
        control.check_cancel()
        return np.full((24,36,3),190 if use_source else 35,np.uint8)
    def clear(self):original(self);cleared.set()
    monkeypatch.setattr(SelectionPreviewSession,'render',render);monkeypatch.setattr(SelectionPreviewSession,'clear',clear)
    d=MaterialChooser(desktop.root.qt,paths=['test.png'],family='plate',fixed_family=True);d.show();v=d.preview
    try:
        wait(started.is_set)
        v.target.setCurrentIndex(1);events=[];QtCore.QTimer.singleShot(0,lambda:events.append(True));wait(lambda:bool(events))
        release.set();wait(lambda:bool(v.cache))
        assert calls==[False,True] and v.cache[0].toImage().pixelColor(0,0).red()==190
        d.reject();assert not v.cache and v.closed
        wait(cleared.is_set);assert v.session.film._medium is None
    finally:release.set();d.close();d.deleteLater()


def test_thumbnail_cache_and_failures_are_bounded(desktop,monkeypatch):
    from film_foundry.ui.preset_picker import PresetPicker
    from film_foundry.app.selection_preview import SelectionPreviewSession
    calls=[]
    def render(self,record,*_):
        calls.append(record['key'])
        if record['key']=='bad':raise ValueError('Unavailable source')
        return np.full((24,36,3),60,np.uint8)
    monkeypatch.setattr(SelectionPreviewSession,'render',render)
    records=[{'key':str(i)} for i in range(27)]+[{'key':'bad'}]
    d=PresetPicker(desktop.root.qt,'Catalog',tuple(r['key'] for r in records),0,records,preview_bank='scanner');d.show()
    try:
        for row in range(27):
            d.results.setCurrentRow(row);wait(lambda:row in d.preview.cache)
        assert len(d.preview.cache)<=24
        assert len({d.results.item(i).sizeHint().height() for i in range(28)})==1
        assert sum(not d.results.item(i).icon().isNull() for i in range(28))<=24
        d.results.setCurrentRow(27);wait(lambda:27 in d.preview.failed)
        for _ in range(5):d.preview.tick()
        assert calls.count('bad')==1
        d.search.setText('absent');assert not d.apply.isEnabled() and d.preview.selected_row()==-1
    finally:d.reject();d.deleteLater()


def test_close_during_render_cancels_and_releases_on_worker(desktop,monkeypatch):
    from film_foundry.ui.material_chooser import MaterialChooser
    from film_foundry.app.selection_preview import SelectionPreviewSession
    from film_foundry.app.control import WorkflowCancelled
    started=Event();release=Event();cleared=Event()
    def render(self,record,use_source,control):
        started.set();release.wait(5);control.check_cancel()
        raise AssertionError('Cancelled result must not be published')
    monkeypatch.setattr(SelectionPreviewSession,'render',render)
    monkeypatch.setattr(SelectionPreviewSession,'clear',lambda _:cleared.set())
    d=MaterialChooser(desktop.root.qt,family='plate',fixed_family=True);d.show()
    try:
        wait(started.is_set);future=d.preview.future;d.reject()
        assert d.preview.closed and not cleared.is_set()
        events=[];QtCore.QTimer.singleShot(0,lambda:events.append(1));wait(lambda:bool(events))
        release.set();wait(cleared.is_set)
        with pytest.raises(WorkflowCancelled):future.result()
    finally:release.set();d.close();d.deleteLater()


def test_reference_render_all_families_keeps_source_and_recipe(tmp_path,monkeypatch):
    from film_foundry.app.selection_preview import SelectionPreviewSession
    from film_foundry.app.material_selection import material_choices
    from film_foundry.model.config import DarkroomConfig
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    path=photos(tmp_path)[0];stamp=__import__('pathlib').Path(path).stat().st_mtime_ns
    config=DarkroomConfig();before=deepcopy(config)
    s=SelectionPreviewSession(base=config,source=path,long_edge=96)
    try:
        for family in ('film','peel','integral','plate','paper','wet','cyanotype','platinum'):
            record=material_choices(family)[0];original=deepcopy(record)
            result=s.render(record,False,WorkflowControl())
            assert result.dtype==np.uint8 and max(result.shape[:2])<=96 and result.std()>0,family
            assert record==original
        photo=s.render(material_choices('film')[0],True,WorkflowControl())
        assert photo.shape==(16,24,3)
        assert config==before and __import__('pathlib').Path(path).stat().st_mtime_ns==stamp
        from PIL import Image
        Image.new('RGB',(37,25),'red').save(path)
        with pytest.raises(ValueError,match='Source changed'):s.render(material_choices('film')[0],True,WorkflowControl())
    finally:s.clear()
    assert s.film._medium is None and s._linear is None


def test_preset_preview_snapshot_and_units_do_not_scale_values(desktop,tmp_path):
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film');before=deepcopy(p._config())
    p._browse_presets(p.scanner_preset_combo,'Scanner')
    d=p.root.qt._inline_editor_host.frames[-1][1]
    assert d.preview.session.base==before and d.preview.target.count()==2
    d.results.setCurrentRow(min(1,d.results.count()-1));d.reject();assert p._config()==before
    for name,suffix,value in (('develop_time',' min',12.5),('develop_temperature',' °C',21),('developer_concentration',' ×',1.25),('exposure_ev',' EV',.5)):
        spin=p.fields[name][1].qt
        assert spin.suffix()==suffix
        spin.setValue(value);assert getattr(p,name).get()==value
    assert p._config().chemistry.time_min==12.5 and p._config().chemistry.concentration==1.25
    from film_foundry.ui.material_lab_window import create_material_window
    for family in ('plate','wet'):
        page=create_material_window(family)
        try:
            assert page.view_ev.spin.suffix()==' EV'
            page.view_ev.setValue(1.25);assert page.conditions().exposure_ev==1.25
            assert page.view_angle.spin.suffix()=='°'
        finally:page.close();page.deleteLater()
