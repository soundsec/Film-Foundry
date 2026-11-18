"""Settings-only recovery, bounded ownership, failure and close boundaries."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from film_foundry.app.last_work import LastWorkStore, LatestWorkWriter
from film_foundry.tests.test_desktop_usability import desktop, wait, photos
from film_foundry.tests.test_electronic_roll import roll
from film_foundry.ui.qt import _application, QtCore, QtGui


def test_slot_is_single_atomic_and_rejects_arrays(tmp_path, monkeypatch):
    tmp_path = tmp_path/'slot'
    store = LastWorkStore(tmp_path)
    store.write({'value': 1})
    store.write({'value': 2})
    assert list(tmp_path.iterdir()) == [store.path]
    import film_foundry.core.atomic_io as atomic
    def fail(*args):
        raise OSError('Interrupted replacement')
    monkeypatch.setattr(atomic.os, 'replace', fail)
    with pytest.raises(OSError):
        store.write({'value': 3})
    with pytest.raises(TypeError):
        store.write({'pixels': np.zeros((4, 4))})
    assert store.read()['work'] == {'value': 2}
    assert list(tmp_path.iterdir()) == [store.path]


def test_writer_coalesces_without_blocking_ui_or_retaining_pending(tmp_path):
    _application()
    entered, release = Event(), Event()
    calls = []
    class Store:
        def write(self, value):
            calls.append(value['value'])
            if len(calls) == 1:
                entered.set()
                assert release.wait(5)
    writer = LatestWorkWriter(Store(), lambda error: None)
    try:
        writer.submit({'value': 0})
        wait(entered.is_set)
        for i in range(1, 50):
            writer.submit({'value': i})
        tick = []
        QtCore.QTimer.singleShot(0, lambda: tick.append(True))
        wait(lambda: bool(tick))
        assert calls == [0] and writer.pending == {'value': 49}
    finally:
        release.set(); writer.close(); writer.thread.join(5)
    assert calls == [0, 49] and writer.pending is None and not writer.thread.is_alive()


def test_manual_save_does_not_disable_insurance_and_restore_uses_parameters(desktop, tmp_path):
    p = desktop
    p._open_catalog_source(photos(tmp_path)[0], 'film')
    p.exposure_ev.set(.5)
    p.work_sessions.save([p.film_workspace])
    assert not p.work_sessions.dirty([p.film_workspace])
    p.last_work.capture()
    wait(p.last_work.store.path.is_file)
    assert p.last_work.store.read()['work']['film']['config']['look']['exposure_ev'] == .5
    p.exposure_ev.set(1.25)
    p.work_sessions.save([p.film_workspace])
    p.last_work.capture()
    wait(lambda: p.last_work.store.read()['work']['film']['config']['look']['exposure_ev'] == 1.25)
    p.exposure_ev.set(-1)
    assert p.last_work.restore()
    assert p.exposure_ev.get() == 1.25 and not p._processing
    assert p._preview_session._image is None


def test_all_material_drafts_restore_from_sources_without_state_or_cache(desktop, tmp_path):
    from film_foundry.ui.material_lab_window import create_material_window
    p = desktop
    source = photos(tmp_path)[0]
    original = []
    for kind in ('peel', 'integral', 'plate', 'paper', 'wet', 'cyanotype', 'platinum'):
        page = create_material_window(kind)
        page.session.open_image(source, 32)
        page.view_ev.setValue(.75)
        p._mount_material_workspace(page, kind)
        page.preview_timer.stop()
        original.append(page)
    # An unsaved state must never be serialized or presented as restored.
    original[3].session.process('expose')
    original[3]._sync(); original[3].preview_timer.stop()
    p.last_work.capture()
    wait(p.last_work.store.path.is_file)
    saved = p.last_work.store.read()['work']
    assert len(saved['materials']) == 7
    assert p.last_work.store.path.stat().st_size < 180_000
    assert all(set(r) == {'kind','title','source','draft','scan','precision','batch','previous_stage'} for r in saved['materials'])
    assert p.last_work.restore()
    wait(lambda: not p.last_work.restoring and not p.workspace_host.busy_pages())
    restored = [w for w in p._material_windows.values() if w not in original]
    assert len(restored) == 7
    for page in restored:
        assert page.session.image is not None and page.session.state is None and page.session.group is None
        assert page.view_ev.value() == .75 and page.session.source_path == source
        page.preview_timer.stop()
    assert original[3].session.state is not None
    # Closing a page removes roots which otherwise retain its full image/session.
    page = restored[0]
    p.work_sessions.bypass = True
    page.close()
    assert page not in p.work_sessions.material_saved and page not in p.last_work.materials
    p.work_sessions.bypass = False


def test_blank_start_preserves_previous_and_exit_drains_latest(desktop, tmp_path):
    p = desktop
    p.last_work.store.write({'sentinel': 'previous'})
    p.open_library(); p.last_work.capture()
    assert p.last_work.store.read()['work'] == {'sentinel': 'previous'}
    p._open_catalog_source(photos(tmp_path)[0], 'film')
    p.exposure_ev.set(1.75)
    p.work_sessions.bypass = True
    p.root.qt.close()
    wait(lambda: p.last_work.ready_to_close)
    assert p.last_work.store.read()['work']['film']['config']['look']['exposure_ev'] == 1.75
    assert not p.last_work.marker.exists() and not p.last_work.writer.thread.is_alive()


def test_clear_cache_during_decode_does_not_repopulate(tmp_path):
    from film_foundry.ui.image_cache import ImagePreviewCache
    _application()
    source = Path(photos(tmp_path)[0])
    entered, release = Event(), Event()
    cache = ImagePreviewCache()
    def decode(*args):
        entered.set(); assert release.wait(5)
        return QtGui.QImage(12, 8, QtGui.QImage.Format.Format_RGB32)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(cache.read, source, 12, 8, decode)
        assert entered.wait(5)
        cache.clear(); release.set()
        assert not task.result().isNull()
    assert cache.statistics()['bytes'] == 0 and not cache._pending


def test_crash_journal_records_tracebacks_and_restores_hooks(tmp_path, monkeypatch):
    from film_foundry.app.crash_journal import CrashJournal, record_exception
    hook = lambda *args: None
    monkeypatch.setattr(sys, 'excepthook', hook)
    journal = CrashJournal(tmp_path).install()
    try:
        try:
            raise RuntimeError('recovery diagnostic marker')
        except RuntimeError as exc:
            record_exception('Caught worker', exc)
            sys.excepthook(type(exc), exc, exc.__traceback__)
    finally:
        journal.close()
    log = (tmp_path/'desktop.log').read_text(encoding='utf-8')
    assert 'Caught worker' in log and 'Unhandled main/UI exception' in log
    assert 'Traceback' in log and 'recovery diagnostic marker' in log
    assert sys.excepthook is hook


def test_relaunch_reports_incomplete_exit_without_overwriting_slot(desktop, tmp_path):
    p = desktop
    p.last_work.store.write({'sentinel': 'keep'})
    p.work_sessions.bypass = True; p.root.qt.close()
    wait(lambda: p.last_work.ready_to_close)
    p.last_work.marker.write_text('{}', encoding='utf-8')
    from film_foundry.tools.run_desktop_gui import DesktopPanel
    reopened = DesktopPanel()
    try:
        assert reopened.last_work.unclean and reopened.last_work.action.isEnabled()
        reopened._open_catalog_source(photos(tmp_path)[0], 'film')
        reopened.open_library()
        assert reopened.last_work.timer.isActive()
        reopened.last_work.capture()
        store=reopened.last_work.store
        wait(lambda:'film' in store.read()['work'])
        assert store.read(store.archives()[0])['work']=={'sentinel':'keep'}
    finally:
        reopened.work_sessions.bypass = True; reopened.root.qt.close()
        wait(lambda: reopened.last_work.ready_to_close)


def test_roll_uncommitted_draft_survives_without_saving_roll(desktop, roll):
    from copy import deepcopy
    p = desktop; session, config, _, _ = roll
    before = deepcopy(session.data)
    p.open_roll_workspace(); p._configure_roll(session)
    key = 'roll-recipe:'+str(session.path)
    dialog = p.find_configuration_dialog(key)
    dialog.controls['temperature_c'].setValue(24)
    p.last_work.capture(); wait(p.last_work.store.path.is_file)
    data = p.last_work.store.read()['work']
    assert data['roll_drafts'][0]['config']['chemistry']['temperature_c'] == 24
    dialog.reject(); _application().processEvents()
    p.last_work.restore()
    restored = p.find_configuration_dialog(key)
    assert restored.controls['temperature_c'].value() == 24
    assert session.data == before


def test_missing_material_source_preserves_settings_and_source_kind(desktop, tmp_path):
    from film_foundry.ui.material_lab_window import create_material_window
    p = desktop; source = photos(tmp_path)[0]
    page = create_material_window('paper'); page.session.open_image(source, 32)
    p._mount_material_workspace(page, 'paper'); page.view_ev.setValue(1.5); page.preview_timer.stop()
    p.last_work.capture(); wait(p.last_work.store.path.is_file)
    Path(source).unlink()
    p.last_work.restore()
    restored = [w for w in p._material_windows.values() if w is not page][0]
    assert restored.view_ev.value() == 1.5 and restored.session.image is None
    record = p.last_work.snapshot()['materials'][-1]
    assert record['source']['kind'] == 'image' and record['source']['path'] == source


def test_only_one_window_owns_the_automatic_slot(desktop):
    from film_foundry.ui.last_work import LastWork
    p = desktop; p.last_work.store.write({'sentinel': 'owner'})
    second = LastWork(p)
    try:
        assert p.last_work.enabled and not second.enabled and second.writer is None
        second.capture()
        assert p.last_work.store.read()['work'] == {'sentinel': 'owner'}
    finally:
        second.deleteLater()
