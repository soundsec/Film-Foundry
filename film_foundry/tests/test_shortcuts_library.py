"""Custom key ownership, temporary comparison and non-destructive library import."""
from copy import deepcopy
from pathlib import Path
import numpy as np
from PySide6.QtTest import QTest
from film_foundry.tests.test_desktop_usability import desktop,photos,wait
from film_foundry.tests.test_electronic_roll import roll
from film_foundry.ui.qt import _application,QtCore,QtGui,QtWidgets
from film_foundry.ui.shortcut_settings import ShortcutSettings,defaults


def test_settings_apply_conflicts_persist_and_route_live(desktop,monkeypatch):
    from film_foundry.ui.preferences import load_preferences,save_preferences
    p=desktop;p.show_workbench();model=p.work_shortcuts.model
    p.open_global_settings('shortcuts');dialog=p._settings_dialog;panel=dialog.shortcut_settings
    assert dialog.tabs.currentWidget() is panel
    panel.editors['preview'][0].setKeySequence(QtGui.QKeySequence('Ctrl+S'))
    panel.apply();assert model.values==defaults() and 'Ctrl+S' in panel.status.text()
    panel.editors['preview'][0].setKeySequence(QtGui.QKeySequence('F6'))
    panel.editors['hold_source'][0].setKeySequence(QtGui.QKeySequence('B'))
    panel.editors['save'][0].clear();panel.apply()
    assert model.values['save']==[] and model.values['preview']==['F6']
    save_preferences(theme='ivory')
    assert load_preferences()['shortcuts']['hold_source']==['B']
    loaded=ShortcutSettings(model.path);assert loaded.values==model.values
    dialog.reject();_application().processEvents()
    calls=[];monkeypatch.setattr(p,'_start_preview',lambda:calls.append('preview'))
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_F5);assert not calls
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_F6);assert calls==['preview']
    assert 'F6' in p.preview_button.qt.toolTip()
    model.apply(defaults());assert 'F5' in p.preview_button.qt.toolTip()


def test_hold_source_restores_display_and_releases_on_context_changes(desktop,tmp_path):
    p=desktop;p._open_catalog_source(photos(tmp_path)[0],'film')
    view=p.result_view
    source=np.zeros((12,18,3),np.float32);source[...,0]=1
    result=np.zeros_like(source);result[...,1]=1
    view.set_pair(source,result);p.preview_tabs.setCurrentWidget(view);view.setFocus();_application().processEvents()
    before=p.edit_tools.capture()
    for mode in ('result','wipe','side_by_side'):
        view.set_comparison_mode(mode)
        QTest.keyPress(view,QtCore.Qt.Key.Key_Space)
        assert view._keyboard_source and view.source.toImage().pixelColor(0,0)==QtGui.QColor('red')
        assert view.clipping_region()[0].toImage().pixelColor(0,0)==QtGui.QColor('red')
        repeat=QtGui.QKeyEvent(QtCore.QEvent.Type.KeyRelease,QtCore.Qt.Key.Key_Space,QtCore.Qt.KeyboardModifier.NoModifier,' ',True)
        _application().sendEvent(view,repeat);assert view._keyboard_source
        QTest.keyRelease(view,QtCore.Qt.Key.Key_Space)
        assert not view._keyboard_source and view.mode==mode
    assert p.edit_tools.capture()==before and not p._preview_running
    QTest.keyPress(view,QtCore.Qt.Key.Key_Space)
    _application().sendEvent(p.root.qt,QtCore.QEvent(QtCore.QEvent.Type.WindowDeactivate))
    assert not view._keyboard_source
    QTest.keyPress(view,QtCore.Qt.Key.Key_Space);assert view._keyboard_source
    p.open_library();assert not view._keyboard_source
    p.show_workbench();QTest.keyPress(view,QtCore.Qt.Key.Key_Space)
    model=p.work_shortcuts.model;values=deepcopy(model.values);values['hold_source']=['B'];model.apply(values)
    assert not view._keyboard_source
    QTest.keyPress(view,QtCore.Qt.Key.Key_B);assert view._keyboard_source
    QTest.keyRelease(view,QtCore.Qt.Key.Key_B);assert not view._keyboard_source


def test_hold_does_not_take_text_or_modal_input_and_material_is_supported(desktop):
    p=desktop;w=p._show_material_workspace('paper')
    source=np.zeros((8,12,3),np.float32);result=np.ones_like(source)
    w.viewer.set_pair(source,result);w.viewer.setFocus();_application().processEvents()
    QTest.keyPress(w.viewer,QtCore.Qt.Key.Key_Space);assert w.viewer._keyboard_source
    QTest.keyRelease(w.viewer,QtCore.Qt.Key.Key_Space);assert not w.viewer._keyboard_source
    line=QtWidgets.QLineEdit(w);line.show();line.setFocus();QTest.keyClicks(line,'a b')
    assert line.text()=='a b' and not w.viewer._keyboard_source
    modal=QtWidgets.QDialog(p.root.qt);modal.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal);modal.show();_application().processEvents()
    QTest.keyPress(modal,QtCore.Qt.Key.Key_Space);assert not w.viewer._keyboard_source
    modal.close()
    model=p.work_shortcuts.model;values=deepcopy(model.values);values['preview']=['Ctrl+C'];model.apply(values)
    line.setText('copy');line.selectAll();QTest.keyClick(line,QtCore.Qt.Key.Key_C,QtCore.Qt.KeyboardModifier.ControlModifier)
    assert _application().clipboard().text()=='copy' and not w.busy


def test_key_recorder_accepts_space_and_guide_advanced_rebind(desktop,monkeypatch):
    p=desktop;p.show_workbench();p.open_global_settings('shortcuts')
    editor=p._settings_dialog.shortcut_settings.editors['hold_source'][0];editor.clear();editor.setFocus()
    QTest.keyClick(editor,QtCore.Qt.Key.Key_Space)
    assert editor.keySequence().toString()=='Space'
    p._settings_dialog.reject();_application().processEvents()
    model=p.work_shortcuts.model;values=deepcopy(model.values);values['guide']=['F2'];values['advanced']=['F3'];model.apply(values)
    calls=[];monkeypatch.setattr(p,'_open_context_guide',lambda:calls.append('guide'));monkeypatch.setattr(p,'_toggle_page_advanced',lambda:calls.append('advanced'))
    for key in (QtCore.Qt.Key.Key_F1,QtCore.Qt.Key.Key_F8):QTest.keyClick(p.result_view,key)
    assert not calls
    QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_F2);QTest.keyClick(p.result_view,QtCore.Qt.Key.Key_F3)
    assert calls==['guide','advanced']
    p.open_library();QTest.keyClick(p.library_view.grid,QtCore.Qt.Key.Key_F2)
    assert calls[-1]=='guide'


def send_drop(target,paths):
    mime=QtCore.QMimeData();mime.setUrls([QtCore.QUrl.fromLocalFile(str(p)) for p in paths])
    enter=QtGui.QDragEnterEvent(QtCore.QPoint(20,20),QtCore.Qt.DropAction.CopyAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
    _application().sendEvent(target,enter)
    drop=QtGui.QDropEvent(QtCore.QPointF(20,20),QtCore.Qt.DropAction.CopyAction,mime,QtCore.Qt.MouseButton.LeftButton,QtCore.Qt.KeyboardModifier.NoModifier)
    _application().sendEvent(target,drop)
    return enter.isAccepted(),drop.isAccepted()


def test_empty_library_drop_files_folder_and_filter_reset(desktop,tmp_path):
    p=desktop;p.open_library();library=p.library_view
    assert library.grid_pages.currentWidget() is library.welcome
    assert set(library.welcome.buttons)=={'photos','materials','roll'}
    assert library.filters_panel.isHidden() and library.manage_panel.isHidden()
    paths=photos(tmp_path);stamp=Path(paths[0]).stat().st_mtime_ns
    assert send_drop(library.welcome,paths[:1])==(True,True)
    assert library.grid.count()==1 and library.grid_pages.currentWidget() is library.grid
    assert Path(paths[0]).stat().st_mtime_ns==stamp and not p._processing
    assert send_drop(library.grid.viewport(),paths[:1])==(True,True)
    assert len(library.catalog.entries)==1
    library.search.setText('no matching filename')
    assert library.grid_pages.currentWidget() is library.welcome and library.welcome.clear.isVisible()
    library.welcome.clear.click();assert library.grid.count()==1
    folder=tmp_path/'source_folder';folder.mkdir();photos(folder)
    assert send_drop(library.grid.viewport(),[folder])==(True,True)
    wait(lambda:not library.sidebar.indexing)
    assert library.grid.count()==4
    from film_foundry.app.desktop_settings import load_settings
    assert str(folder) in load_settings()['source_folders']
    bad=tmp_path/'notes.txt';bad.write_text('not an image')
    assert send_drop(library.grid.viewport(),[bad])==(False,False)
    assert bad.read_text()=='not an image'


def test_library_drop_latent_uses_existing_material_import(desktop,roll):
    p=desktop;_,_,latent,_=roll;stamp=latent.stat().st_mtime_ns
    p.open_library();library=p.library_view
    assert send_drop(library.welcome,[latent])==(True,True)
    assert library.collection.currentIndex()==1 and library.grid.count()==1
    assert any(a['kind']=='latent' for a in library.catalog.assets())
    assert latent.stat().st_mtime_ns==stamp and not p._processing


def test_folder_drop_during_indexing_queues_latest_sources(desktop,tmp_path,monkeypatch):
    from threading import Event
    p=desktop;p.open_library();library=p.library_view
    folders=[tmp_path/'folder_a',tmp_path/'folder_b']
    for folder in folders:folder.mkdir();photos(folder)
    started=Event();release=Event();original=library.catalog.index_candidates;calls=[]
    def delayed(paths):
        calls.append(paths)
        if len(calls)==1:started.set();assert release.wait(5)
        return original(paths)
    monkeypatch.setattr(library.catalog,'index_candidates',delayed)
    try:
        library.drop_target.import_paths([str(folders[0])]);wait(started.is_set)
        library.drop_target.import_paths([str(folders[1])])
        seen=[];QtCore.QTimer.singleShot(0,lambda:seen.append(True));wait(lambda:seen)
        release.set();wait(lambda:len(calls)==2 and not library.sidebar.indexing and library.grid.count()==6)
    finally:release.set()
