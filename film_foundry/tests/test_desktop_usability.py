"""Behavioral checks for destructive navigation and the gallery/work split."""
import os
from pathlib import Path
from time import monotonic

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PIL import Image
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets,_application


def wait(predicate):
    end=monotonic()+10
    while not predicate() and monotonic()<end:_application().processEvents()
    assert predicate()


@pytest.fixture
def desktop(tmp_path,monkeypatch):
    for key in ('UI_SETTINGS','CATALOG','CAMERA_SETTINGS','DESKTOP_SETTINGS'):
        monkeypatch.setenv('FILM_FOUNDRY_'+key,str(tmp_path/(key+'.json')))
    from film_foundry.ui.preferences import _read
    from film_foundry.app.desktop_settings import load_settings,save_settings
    _read.cache_clear()
    values=load_settings();values.update(workspace=str(tmp_path/'workspace'),leave_policy='discard');save_settings(values)
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    yield panel
    if panel.root.winfo_exists():
        wait(lambda:not panel.workspace_host.busy_pages() and not panel._workspace_io_busy)
        panel.work_sessions.bypass=True
        panel.root.qt.close()
    _application().sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)


def photos(tmp_path):
    result=[]
    for i in range(3):
        path=tmp_path/f'image-{i}.png';Image.new('RGB',(24,16),(50+i*30,90,120)).save(path);result.append(str(path))
    return result


def test_remove_is_index_only_and_refresh_does_not_resurrect(tmp_path):
    from film_foundry.app.desktop_catalog import DesktopCatalog
    paths=photos(tmp_path);catalog=DesktopCatalog(tmp_path/'catalog.json');catalog.add(paths)
    item=catalog.entries[0];catalog.annotate(item['id'],rating=4,favorite=True)
    before=[Path(p).stat().st_mtime_ns for p in paths]
    catalog.remove_items([item]);reopened=DesktopCatalog(catalog.path)
    assert len(reopened.query())==2
    assert not reopened.index_candidates([tmp_path])
    assert all(Path(p).is_file() for p in paths)
    assert before==[Path(p).stat().st_mtime_ns for p in paths]
    assert not reopened.import_paths([paths[0]])
    restored=next(e for e in reopened.query() if e['id']==item['id'])
    assert restored['rating']==4 and restored['favorite']


def test_gallery_double_click_browses_and_batch_creation_is_explicit(desktop,tmp_path):
    panel=desktop;paths=photos(tmp_path);panel.open_library();library=panel.library_view
    library.catalog.add(paths);library.refresh();library.grid.setCurrentRow(0)
    before=panel.input_path.get()
    library.grid.itemDoubleClicked.emit(library.grid.item(0))
    browser=panel.workspace_host.currentWidget()
    assert browser.__class__.__name__=='LibraryBrowser' and panel.input_path.get()==before
    browser.move(1);browser.annotate(rating=5);browser.annotate(favorite=True)
    assert browser.target_family.isHidden() and browser.open_button.isHidden()
    asset=browser.selected_asset();assert asset['rating']==5 and asset['favorite']
    panel.open_library();library.select_all();library.open_selected()
    assert set(panel._selected_photos)==set(paths) and len(panel._selected_photos)==3
    assert panel.workspace_host.currentWidget() is panel.film_workspace


def test_save_before_replace_cancel_and_defaults(desktop,tmp_path,monkeypatch):
    from film_foundry.app.desktop_settings import load_settings,save_settings
    panel=desktop;paths=photos(tmp_path)
    panel._open_catalog_batch(paths[:2],'film');panel.fast_mode.set(True);panel.quality_mode.set('high')
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'cancel')
    panel._open_catalog_source(paths[2],'film')
    assert panel._selected_photos==paths[:2]
    values=load_settings();values.update(fast_mode=False,quality='draft',resize=True,long_edge=1200);save_settings(values)
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'save')
    panel._open_catalog_source(paths[2],'film')
    assert panel.input_path.get()==paths[2] and panel.quality_mode.get()=='draft'
    assert panel.run_as_preview.get() and int(panel.preview_long_edge.get())==1200
    saved=load_settings()['recent_work'][0]
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'discard')
    panel.work_sessions.open_work(saved['path'],'session')
    assert panel._selected_photos==paths[:2] and panel.fast_mode.get() and panel.quality_mode.get()=='high'
    assert list((tmp_path/'workspace'/'sessions').iterdir())==[Path(saved['path'])]


def test_small_dialog_retains_parent_and_editor_page_is_reachable(desktop,tmp_path):
    from film_foundry.ui.inline_editor import present_inline_editor
    panel=desktop;original=panel.workspace_host.currentWidget();calls=[]
    dialog=QtWidgets.QDialog(panel.root.qt)
    present_inline_editor(panel.root.qt,dialog,lambda:calls.append(True))
    assert dialog.isWindow() and panel.workspace_host.currentWidget() is original
    dialog.reject();assert not calls
    panel.open_embedded_editor('film');editor=panel._editor_windows['film'];page=panel.workspace_host.currentWidget()
    assert page is panel.editor_host and not editor.root.qt.isWindow()
    panel.open_library();panel.open_embedded_editor('film')
    assert panel._editor_windows['film'] is editor and panel.workspace_host.currentWidget() is page


def test_material_save_waits_before_replace_and_old_dirty_page_survives_open(desktop,tmp_path,monkeypatch):
    from film_foundry.app.desktop_settings import load_settings
    panel=desktop;paths=photos(tmp_path);panel._open_catalog_source(paths[0],'paper')
    page=panel.workspace_host.currentWidget();wait(lambda:not page.busy)
    assert page in panel.work_sessions.dirty()
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'save')
    called=[]
    assert page.replace_work(lambda:called.append(True))
    assert not called
    wait(lambda:not panel._workspace_io_busy)
    assert called and page not in panel.work_sessions.dirty()
    saved=load_settings()['recent_work'][0];assert saved['kind']=='material'
    page.seed.setValue(page.seed.value()+1)
    panel.work_sessions.open_work(saved['path'],'material')
    wait(lambda:not panel._workspace_io_busy and not panel.workspace_host.busy_pages())
    assert page in panel.work_sessions.dirty()  # Loading adds work; doesn't mark old work as saved.
    loaded=panel.workspace_host.currentWidget();assert loaded is not page and loaded.session.image is not None
    assert loaded not in panel.work_sessions.dirty()


def test_save_failure_cannot_run_destructive_action(desktop,tmp_path,monkeypatch):
    import film_foundry.ui.workspace_sessions as sessions
    panel=desktop;panel._open_catalog_source(photos(tmp_path)[0],'film');called=[];errors=[]
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'save')
    monkeypatch.setattr(sessions,'save_session',lambda *_:(_ for _ in ()).throw(OSError('disk full')))
    monkeypatch.setattr(QtWidgets.QMessageBox,'warning',lambda *a:errors.append(a[2]))
    assert not panel.work_sessions.protect(lambda:called.append(True))
    assert not called and errors==['disk full']


def test_layout_roundtrip_and_close_cancel(desktop,tmp_path,monkeypatch):
    panel=desktop;panel._open_catalog_source(photos(tmp_path)[0],'film')
    panel.root.qt.resize(1100,780);_application().processEvents()
    panel.navigation.setCurrentRow(panel.page_index['develop'])
    layouts={}
    for mode in ('studio','compact','studio','compact'):
        panel.set_workspace_layout(mode);_application().processEvents()
        sizes=panel.workspace_splitter.sizes()
        if mode in layouts:assert sizes==layouts[mode]
        layouts[mode]=sizes
        assert panel.root.qt.width()==1100
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'cancel')
    panel.root.qt.close();assert panel.root.qt.isVisible()


def test_native_close_saves_before_window_is_destroyed(desktop,tmp_path,monkeypatch):
    from film_foundry.app.desktop_settings import load_settings
    panel=desktop;panel._open_catalog_source(photos(tmp_path)[0],'film')
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'save')
    panel.root.qt.close()
    wait(lambda:not panel.root.winfo_exists())
    recent=load_settings()['recent_work'];assert len(recent)==1 and Path(recent[0]['path']).is_file()


def test_print_page_uses_existing_project_storage_before_close(desktop,tmp_path,monkeypatch):
    import numpy as np
    from film_foundry.app.desktop_settings import load_settings
    panel=desktop;page=panel._receive_print_source({'image':np.full((16,24,3),.4,np.float32),'provenance':{'name':'test'}})
    page.numbers['opacity'].setValue(.7);page.mode.setCurrentIndex(1)
    assert page.busy
    monkeypatch.setattr(panel.work_sessions,'decision',lambda:'save')
    panel.workspace_host.close_page(panel.workspace_host.indexOf(page))
    wait(lambda:page not in panel.workspace_host._pages)
    recent=load_settings()['recent_work'][0];assert recent['kind']=='print'
    from film_foundry.app.printmaking import PrintmakingSession
    saved=PrintmakingSession();saved.load(recent['path'])
    assert saved.styles[0].opacity==.7


def test_save_choice_can_be_remembered_and_cancel_is_not_remembered(desktop):
    from film_foundry.app.desktop_settings import load_settings,save_settings
    values=load_settings();values['leave_policy']='ask';save_settings(values)
    def choose():
        box=next(w for w in _application().topLevelWidgets() if isinstance(w,QtWidgets.QMessageBox) and w.isVisible())
        box.checkBox().setChecked(True)
        next(b for b in box.buttons() if box.buttonRole(b)==QtWidgets.QMessageBox.ButtonRole.DestructiveRole).click()
    QtCore.QTimer.singleShot(0,choose)
    assert desktop.work_sessions.decision()=='discard'
    assert load_settings()['leave_policy']=='discard'
