"""Only the changed document ownership and asynchronous close paths."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from threading import Event
from time import monotonic

import numpy as np
from PIL import Image
import pytest

from film_foundry.ui.qt import QtCore, QtWidgets, _application


def wait(predicate):
    deadline = monotonic() + 6
    while not predicate() and monotonic() < deadline:
        _application().processEvents()
    assert predicate()


@pytest.fixture
def panel(tmp_path, monkeypatch):
    for key in ('CATALOG', 'CAMERA_SETTINGS', 'UI_SETTINGS'):
        monkeypatch.setenv('FILM_FOUNDRY_' + key, str(tmp_path / (key + '.json')))
    from film_foundry.ui.preferences import _read
    from film_foundry.ui.i18n import set_language
    _read.cache_clear();set_language('zh_CN')
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    panel = DesktopPanel();panel.auto_preview.set(False);panel.root.qt.show()
    yield panel
    if panel.root.winfo_exists():
        wait(lambda: not panel.workspace_host.busy_pages())
        panel.root.qt.close()
    _application().sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    set_language('zh_CN')


def source(tmp_path):
    path = tmp_path / 'source.png'
    Image.fromarray(np.full((24, 36, 3), 128, np.uint8)).save(path)
    return str(path)


def test_seven_materials_and_busy_draft_survive_navigation_and_language(panel, tmp_path):
    path = source(tmp_path)
    for kind in ('peel', 'integral', 'plate', 'paper', 'wet', 'cyanotype', 'platinum'):
        panel._open_catalog_source(path, kind)
        page = panel._material_windows[kind]
        wait(lambda: not page.busy)
        assert page.last_error is None and not page.isWindow()
        assert panel.workspace_host.currentWidget() is page
    page = panel._material_windows['cyanotype'];session = page.session
    page.work_size.setValue(1200)
    gate = Event()
    try:
        page._submit(lambda: gate.wait(5), 'held operation', render=False)
        panel.open_library();library = panel.library_view
        panel.open_embedded_editor('runtime')
        assert not panel._editor_windows['runtime'].root.qt.isWindow()
        panel._editor_windows['runtime'].root.qt.window().reject()
        panel.workspace_host.setCurrentWidget(page)
        from film_foundry.ui.i18n import language_label
        panel.language.set(language_label('en_US'));panel._change_language()
        _application().sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        assert panel.workspace_host.currentWidget() is page and page.busy
        assert panel.library_view is library
        assert page.session is session and page.work_size.value() == 1200
        assert all(not window.isWindow() for window in panel._material_windows.values())
        assert [w for w in _application().topLevelWidgets() if w.isVisible()] == [panel.root.qt]
    finally:
        gate.set();wait(lambda: not page.busy)


def test_close_busy_tab_waits_for_worker_then_can_reopen(panel):
    page = panel._show_material_workspace('paper');gate = Event()
    try:
        page._submit(lambda: gate.wait(5), 'held operation', render=False)
        panel.workspace_host.close_page(panel.workspace_host.indexOf(page))
        assert page.busy and panel.workspace_host.indexOf(page) >= 0
    finally:
        gate.set();wait(lambda: 'paper' not in panel._material_windows)
    assert panel.workspace_host.indexOf(page) == -1
    assert panel._show_material_workspace('paper') is not page


def test_main_close_drains_embedded_workers(panel, monkeypatch):
    page = panel._show_material_workspace('integral');gate = Event()
    monkeypatch.setattr(QtWidgets.QMessageBox, 'question', lambda *_: QtWidgets.QMessageBox.StandardButton.Yes)
    try:
        page._submit(lambda: gate.wait(5), 'held operation', render=False)
        assert not panel.root.qt.close()
        assert panel.root.winfo_exists() and page.busy
        assert panel._workspace_exit_pending
    finally:
        gate.set()
    wait(lambda: not panel.root.winfo_exists())
    assert page.pool._shutdown


def test_result_stack_and_archive_stay_in_host(panel, tmp_path):
    from film_foundry.app.material_lab_session import MaterialLabSession
    path = source(tmp_path);session = MaterialLabSession('cyanotype')
    session.open_image(path, 0);session.run_standard()
    archive = tmp_path / 'sheet.ffmaterial.npz';session.save_products(archive)
    entry = {'id':'one', 'label':'Test result', 'runs':[{'source':path, 'medium':str(archive),
             'family':'cyanotype', 'artifacts':{'cover':path}}]}
    panel._open_result_stack(entry);stack = panel.workspace_host.currentWidget()
    wait(lambda: stack.preview.source is not None)
    assert stack.preview.source is not None and not stack.isWindow()
    panel._open_result_artifact(str(archive), 'cyanotype');page = panel.workspace_host.currentWidget()
    wait(lambda: not page.busy)
    assert page.last_error is None and page.session.group is not None and not page.isWindow()
    count = panel.workspace_host.count()
    panel._open_result_stack(entry);assert panel.workspace_host.currentWidget() is stack
    panel._open_result_artifact(str(archive), 'cyanotype')
    assert panel.workspace_host.currentWidget() is page and panel.workspace_host.count() == count
    # The final medium, not a rendered cover, enters the next exposure workflow.
    stack.tree.setCurrentItem(stack.tree.topLevelItem(0).child(1))
    assert stack.reexpose_button.isEnabled()
    for kind in ('peel','integral','plate'):
        stack.target_family.setCurrentIndex(stack.target_family.findData(kind));stack.reexpose()
        receiver=panel.workspace_host.currentWidget()
        assert receiver.kind==kind and receiver.optical_panel.isVisible()
        receiver.optical_panel.ev.setValue(1.25);receiver.optical_panel.applied.emit()
        wait(lambda:not receiver.busy)
        assert receiver.last_error is None and receiver.session.optical_relay.exposure_ev==1.25
        assert receiver.session.optical_source.group_id==session.group.group_id


def test_light_table_close_waits_for_export(panel, tmp_path, monkeypatch):
    from film_foundry.ui.roll_strips import RollLightTableDialog, RollLightTable
    from film_foundry.app import film_observation
    monkeypatch.setattr(RollLightTable, 'observe', lambda *_args, **_kwargs: np.zeros((8, 12, 3), np.float32))
    page = RollLightTableDialog(medium_path=tmp_path/'medium.npz')
    panel._present_workspace_dialog(page)
    wait(lambda: page.view_valid)
    gate = Event();target = tmp_path/'export.png'
    monkeypatch.setattr(QtWidgets.QFileDialog, 'getSaveFileName', lambda *_: (str(target), 'PNG'))
    def export(*_):
        gate.wait(5);target.write_bytes(b'completed')
    monkeypatch.setattr(film_observation, 'export_observation', export)
    try:
        page.export_view();panel.workspace_host.close_page(panel.workspace_host.indexOf(page))
        assert page.exporting and panel.workspace_host.indexOf(page) >= 0
        panel.open_library();assert page.exporting
    finally:gate.set()
    wait(lambda: panel.workspace_host.indexOf(page) == -1)
    assert target.read_bytes() == b'completed'


def test_roll_recipe_draft_cannot_thaw_started_roll(panel, tmp_path, monkeypatch):
    from copy import deepcopy
    from film_foundry.app import electronic_roll
    class Session:
        path = tmp_path/'roll.json'
        data = {'phase':'sealed'}
        saved = []
        def process_config(self, config):return deepcopy(config)
        def save(self):self.saved.append(deepcopy(self.data))
    session = Session()
    monkeypatch.setattr(electronic_roll, 'RollSession', lambda _: session)
    panel.open_roll_workspace()
    panel._configure_roll(session)
    draft = panel.workspace_host.currentWidget()
    assert not draft.isWindow()
    draft.controls['temperature_c'].setValue(27)
    panel.open_library();panel._configure_roll(session)
    assert panel.workspace_host.currentWidget() is draft
    session.data['phase'] = 'developing'
    draft.apply()
    assert not session.saved and 'recipe_draft' not in session.data
    assert panel.workspace_host.indexOf(draft) == -1
