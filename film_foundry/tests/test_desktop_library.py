import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import asdict
from pathlib import Path
from time import monotonic

import numpy as np
from PIL import Image
import pytest

from film_foundry.app.desktop_catalog import DesktopCatalog
from film_foundry.ui.qt import QtCore, QtWidgets, _application


@pytest.fixture(autouse=True)
def local_catalog(tmp_path,monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_CATALOG',str(tmp_path/'catalog.json'))
    monkeypatch.setenv('FILM_FOUNDRY_CAMERA_SETTINGS',str(tmp_path/'camera.json'))
    monkeypatch.setenv('FILM_FOUNDRY_UI_SETTINGS',str(tmp_path/'ui.json'))
    from film_foundry.ui.preferences import _read
    from film_foundry.ui.i18n import set_language
    _read.cache_clear();set_language('zh_CN')
    yield
    set_language('zh_CN')


def source(tmp_path):
    path=tmp_path/'frame.png'
    Image.fromarray(np.full((24,36,3),125,np.uint8)).save(path)
    return path


def wait(predicate):
    deadline=monotonic()+6
    while not predicate() and monotonic()<deadline:_application().processEvents()
    assert predicate()


def test_catalog_annotations_persist_and_do_not_touch_source(tmp_path,monkeypatch):
    path=source(tmp_path);stamp=path.stat().st_mtime_ns
    catalog=DesktopCatalog();added=catalog.add([path,path]);assert len(added)==1
    identity=added[0]['id'];catalog.annotate(identity,rating=4,favorite=True)
    restored=DesktopCatalog()
    assert restored.query(favorites=True,relation='ge',rating=4)[0]['id']==identity
    assert restored.query(relation='lt',rating=4)==[]
    assert restored.query(relation='gt',rating=4)==[]
    assert restored.query(relation='le',rating=4)[0]['rating']==4
    assert path.stat().st_mtime_ns==stamp
    def fail():raise OSError('read only')
    monkeypatch.setattr(restored,'save',fail)
    with pytest.raises(OSError):restored.annotate(identity,rating=1)
    assert restored.entries[0]['rating']==4


def test_library_routes_source_and_preserves_layout_editor_drafts(tmp_path):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.ui.i18n import language_label
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    try:
        path=source(tmp_path);panel.open_library();library=panel.library_view
        library.catalog.add([path]);library.refresh();library.grid.setCurrentRow(0)
        library.rate(4);library.favor()
        assert library.selected()['rating']==4 and library.selected()['favorite']
        wait(lambda:not library.grid.item(0).icon().isNull())
        library.open_selected()
        assert panel.input_path.get()==str(path.resolve())
        before=asdict(panel._config());points=panel.curve_editors['tone_curve'].points
        for mode in ('studio','compact','studio'):
            panel.set_workspace_layout(mode)
            assert asdict(panel._config())==before
            assert panel.curve_editors['tone_curve'].points is points
        assert panel.workspace_splitter.widget(1) is panel.preview_surface
        panel.open_embedded_editor('film');editor=panel._editor_windows['film']
        editor.preset_name.set('unsaved library draft')
        assert not editor.root.qt.isWindow()
        panel.language.set(language_label('en_US'));panel._change_language()
        assert editor.root.winfo_exists() and editor.preset_name.get()=='unsaved library draft'
        assert panel.workspace_host.currentWidget() is panel.editor_host
        assert panel._workspace_layout=='studio'
        panel.open_library();assert panel.library_view.catalog.entries[0]['rating']==4
    finally:panel.root.destroy()


def test_library_material_windows_independent_and_reopen_after_close(tmp_path):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel=DesktopPanel();panel.auto_preview.set(False)
    try:
        path=source(tmp_path)
        for family in ('peel','integral','plate'):
            panel._open_catalog_source(str(path),family)
            window=panel._material_windows[family]
            wait(lambda:not window.busy)
            assert window.last_error is None and window.session.image is not None
            window.set_workspace_layout('studio')
            assert window.workspace_splitter.widget(1) is window.preview_surface
        assert len({id(window.session) for window in panel._material_windows.values()})==3
        old=panel._material_windows['peel'];old.close()
        assert 'peel' not in panel._material_windows
        panel._open_catalog_source(str(path),'peel')
        new=panel._material_windows['peel'];wait(lambda:not new.busy)
        _application().sendPostedEvents(None,QtCore.QEvent.Type.DeferredDelete)
        assert panel._material_windows['peel'] is new
    finally:
        for window in list(panel._material_windows.values()):
            wait(lambda:not window.busy);window.close()
        panel.root.destroy()


def test_selected_batch_and_strict_roll_buttons_use_application_jobs(tmp_path):
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    from film_foundry.app.api import WorkflowRequest,run_request
    from film_foundry.app.electronic_roll import import_roll
    panel=DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    try:
        first=source(tmp_path);second=tmp_path/'second.png';Image.open(first).save(second)
        cfg=panel._config();cfg.enable_grain=False;cfg.enable_mtf=False;cfg.enable_halation=False
        cfg.output.save_scanner_raw=False;cfg.output.export_transparent_plate=False;panel._apply_loaded_config(cfg)
        panel.output_path.set(str(tmp_path/'results'));panel.run_as_preview.set(False)
        panel.open_library();panel._open_catalog_batch([str(first),str(second)],'film')
        panel._start_process()
        destination=panel.film_workspace._inline_editor_host.frames[-1][1]
        destination.path.setText(str(tmp_path/'results'));destination.apply()
        wait(lambda:not panel._processing)
        assert len(panel.library_view.catalog.results)==2
        assert all(r['runs'][0].get('cover') for r in panel.library_view.catalog.results)
        latent=tmp_path/'latent.darkroom_latent.npz'
        run_request(WorkflowRequest('expose',first,latent,cfg,frame_identity={'roll_id':'ui','frame_index':1}))
        panel.open_roll_workspace();session=import_roll(latent,panel.roll_view.storage,paths=[latent],capacity=8,confirmed_complete=True)
        panel.roll_view.session=session;panel.roll_view.refresh()
        assert panel.roll_view.develop.isEnabled() and not panel.roll_view.scan.isEnabled()
        panel.output_path.set('')  # The chosen destination must not depend on an old hidden field.
        panel._configure_roll(panel.roll_view.session,'develop')
        destination=panel.roll_view._inline_editor_host.frames[-1][1]
        destination.path.setText(str(tmp_path/'roll-results'));destination.apply()
        wait(lambda:not panel._processing)
        assert not panel.roll_view.develop.isEnabled() and panel.roll_view.scan.isEnabled()
        assert len(panel.library_view.catalog.results)==10
        panel._configure_roll(panel.roll_view.session,'scan')
        destination=panel.roll_view._inline_editor_host.frames[-1][1]
        destination.path.setText(str(tmp_path/'roll-scans'));destination.apply()
        wait(lambda:not panel._processing)
        assert panel.roll_view.session.data['phase']=='scanned'
    finally:panel.root.destroy()
