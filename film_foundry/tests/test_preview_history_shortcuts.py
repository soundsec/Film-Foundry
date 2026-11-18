"""Display history must match published pixels and respect work/focus ownership."""
from copy import deepcopy
from threading import Event
import numpy as np
import pytest
from PySide6.QtTest import QTest

from film_foundry.tests.test_desktop_usability import desktop, wait, photos
from film_foundry.tests.test_electronic_roll import roll
from film_foundry.ui.qt import _application, QtCore, QtGui, QtWidgets


def test_history_memory_budget_count_and_pixel_ownership():
    from film_foundry.ui.preview_history import PreviewHistory,HistoryBudget
    _application();parent=QtWidgets.QWidget();budget=HistoryBudget(5*120*80*3)
    a=PreviewHistory(parent,lambda:'a',lambda e:'',lambda e:None,budget=budget)
    b=PreviewHistory(parent,lambda:'b',lambda e:'',lambda e:None,budget=budget)
    image=QtGui.QImage(120,80,QtGui.QImage.Format.Format_RGB888);image.fill(QtGui.QColor('red'))
    for i in range(10):a.add(image,{'value':i},'a',0,str(i))
    assert len(a.entries)==5 and budget.bytes<=budget.max_bytes
    b.add(image,{'value':1},'b',0,'one')
    assert len(a.entries)==4 and len(b.entries)==1
    image.fill(QtGui.QColor('blue'))
    assert b.entries[0].image.pixelColor(0,0)==QtGui.QColor('red')
    budget.max_bytes=32*1024*1024
    for i in range(12):a.add(image,{'value':i},'a',0,str(i))
    assert len(a.entries)==6
    a.pinned = a.entries[0].serial
    for i in range(20, 28):
        a.add(image,{'value':i},'a',0,str(i))
    assert len(a.entries)==7 and a.entries[0].serial==a.pinned
    assert a.entries[-1].difference=='value 26 → 27'
    dialog=a.open()
    assert dialog.selected==a.pinned
    dialog.close()
    a.dispose();b.dispose();assert budget.bytes==0;parent.close()


def test_real_film_history_compare_restore_and_undo(desktop,tmp_path):
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film')
    p._start_preview();wait(lambda:not p._preview_running)
    assert len(p.preview_history.entries)==1
    first=p.preview_history.entries[0];before=deepcopy(first.payload['settings'])
    p.exposure_ev.set(.7);p._start_preview();wait(lambda:not p._preview_running)
    assert len(p.preview_history.entries)==2
    second=p.preview_history.entries[-1]
    assert first.image!=second.image and second.payload['settings']['config']['look']['exposure_ev']==.7
    dialog=p.preview_history.open();dialog.list.setCurrentRow(0)
    assert p.exposure_ev.get()==.7 and dialog.viewer.mode=='wipe' and dialog.apply.isEnabled()
    dialog.adopt()
    assert p.edit_tools.capture()==before and not p._processing
    p.edit_tools.travel(False);assert p.exposure_ev.get()==.7
    p._open_catalog_source(photos(tmp_path)[1],'film')
    assert not p.preview_history.entries


def test_stale_film_result_is_not_history_and_snapshot_is_from_request(desktop,tmp_path,monkeypatch):
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film');entered=Event();release=Event()
    def render(request):
        entered.set();assert release.wait(5)
        image=np.full((8,12,3),.2+request.config.look.exposure_ev/10,np.float32)
        p._post_preview(request.token,lambda:p._show_preview('source',image,image,'source','result'))
        p.root.after(0,lambda:p._finish_preview(request.token))
    monkeypatch.setattr(p,'_render_preview',render)
    try:
        p._start_preview();wait(entered.is_set);p.exposure_ev.set(1)
        release.set();wait(lambda:not p._preview_running)
        assert not p.preview_history.entries
        p._start_preview();wait(lambda:not p._preview_running)
        entry=p.preview_history.entries[0]
        assert entry.payload['settings']['config']['look']['exposure_ev']==1
        assert entry.image.pixelColor(0,0).red()==76
    finally:release.set()


@pytest.mark.parametrize('kind',['peel','integral','plate','paper','wet','cyanotype','platinum'])
def test_material_history_restores_settings_without_retaining_or_rewinding_media(kind):
    from film_foundry.tests.test_material_recipes import formed
    from film_foundry.ui.material_lab_window import create_material_window
    from film_foundry.app.material_lab_session import MaterialLabSession
    w=create_material_window(kind);w.session=formed(kind);w._restore_controls();w._sync();w.show();w.preview_timer.stop()
    try:
        state=w.session.state;w.refresh_preview();wait(lambda:not w.busy)
        assert len(w.preview_history.entries)==1
        first=w.preview_history.entries[0]
        w.view_ev.setValue(.6);w.preview_timer.stop();w.refresh_preview();wait(lambda:not w.busy)
        assert len(w.preview_history.entries)==2 and w.session.state is state
        d=w.preview_history.open();d.list.setCurrentRow(0);d.adopt()
        assert w.view_ev.value()==0 and w.session.state is state
        w.preview_timer.stop();w.edit_tools.travel(False);wait(lambda:not w.busy and w.view_ev.value()==.6)
        w.preview_timer.stop()
        assert w.session.state is state
        # A different physical session cannot be rewound by a display record.
        other=MaterialLabSession(kind);other.image=w.session.image;other.state=state;other.source_path=w.session.source_path;w.session=other
        assert w.preview_history.can_restore(first)
        assert set(first.payload)=={'draft','scan'}
    finally:
        wait(lambda:not w.busy);w.preview_timer.stop();w.close();_application().processEvents()


def test_shortcuts_follow_page_and_leave_text_and_dialogs_alone(desktop,tmp_path,monkeypatch):
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film')
    ctrl=QtCore.Qt.KeyboardModifier.ControlModifier
    p.exposure_ev.set(.8);p.edit_tools.record()
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_Z,ctrl)
    assert p.exposure_ev.get()==0
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_Y,ctrl)
    assert p.exposure_ev.get()==.8
    line=QtWidgets.QLineEdit(p.film_workspace);line.show();line.setFocus();line.setText('a');line.selectAll();QTest.keyClicks(line,'b')
    QTest.keyClick(line,QtCore.Qt.Key.Key_Z,ctrl)
    assert line.text()=='a' and p.exposure_ev.get()==.8
    calls=[];monkeypatch.setattr(p.work_sessions,'save',lambda pages:calls.append(pages))
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_S,ctrl)
    assert calls==[[p.film_workspace]]
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_Backslash,ctrl)
    assert p.result_view.mode=='side_by_side'
    modal=QtWidgets.QDialog(p.root.qt);modal.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal);modal.show();_application().processEvents()
    QTest.keyClick(modal,QtCore.Qt.Key.Key_S,ctrl);assert len(calls)==1;modal.close()
    page=p._show_material_workspace('paper');page.session.image=np.zeros((8,12,3),np.float32);page._sync()
    QTest.keyClick(page.viewer,QtCore.Qt.Key.Key_S,ctrl)
    assert calls[-1]==[page] and len(calls)==2
    QTest.keyClick(page.viewer,QtCore.Qt.Key.Key_H,ctrl)
    assert page.preview_history.dialog is not None
    page.preview_history.dialog.close()


def test_material_history_records_post_action_scan_rotation():
    from film_foundry.tests.test_material_recipes import formed
    from film_foundry.ui.material_lab_window import create_material_window
    w=create_material_window('paper');w.session=formed('paper');w._restore_controls();w._sync();w.show();w.preview_timer.stop()
    try:
        w.edit_tools.rotate(1);wait(lambda:not w.busy)
        e=w.preview_history.entries[-1]
        assert e.payload['scan']['rotation_quarters']==1
        assert e.image.height()>e.image.width()
    finally:w.close();_application().processEvents()


def test_history_survives_language_rebuild_and_rejects_changed_source(desktop,tmp_path):
    from PIL import Image
    from film_foundry.ui.i18n import language_label,set_language
    p=desktop;path=photos(tmp_path)[0];p._open_catalog_source(path,'film')
    p._start_preview();wait(lambda:not p._preview_running)
    history=p.preview_history;entry=history.entries[0]
    try:
        p.language.set(language_label('en_US'));p._change_language()
        assert p.preview_history is history and history.entries[0] is entry
        before=p.edit_tools.capture()
        Image.new('RGB',(24,16),'white').save(path)
        with pytest.raises(ValueError):history.restore_parameters(entry)
        assert p.edit_tools.capture()==before
        p._start_preview();wait(lambda:not p._preview_running)
        assert len(history.entries)==1 and history.entries[0] is not entry
    finally:set_language('zh_CN')


def test_source_removed_after_selection_does_not_start_preview(desktop,tmp_path,monkeypatch):
    from pathlib import Path
    p=desktop;path=Path(photos(tmp_path)[0]);p._open_catalog_source(str(path),'film')
    select=p._first_input_image
    def disappear():
        selected=select();path.unlink();return selected
    monkeypatch.setattr(p,'_first_input_image',disappear)
    p._start_preview()
    assert not p._preview_running and p._preview_state_kind=='failed'
    assert not p.preview_history.entries and p._history_preview_request is None


def test_frozen_latent_history_keeps_source_and_mode(desktop,roll):
    p=desktop;_,_,latent,_=roll;stamp=latent.stat().st_mtime_ns
    p.pipeline_mode.set('develop_latent');p.negative_path.set(str(latent));p._update_mode_visibility()
    p.edit_tools.initialize();p._start_preview();wait(lambda:not p._preview_running)
    first=p.preview_history.entries[0]
    p.develop_temperature.set(28);p._start_preview();wait(lambda:not p._preview_running)
    p.preview_history.restore_parameters(first)
    assert p.pipeline_mode.get()=='develop_latent' and p.negative_path.get()==str(latent)
    assert p.pipeline_bar.states['film']=='frozen' and latent.stat().st_mtime_ns==stamp
