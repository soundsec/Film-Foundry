"""Lightweight recovery coordination; workers never access controls or images."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from film_foundry.app.last_work import LastWorkStore, LatestWorkWriter
from film_foundry.app.crash_journal import record_exception
from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui import material_workspace_state as drafts


class LastWork(QtCore.QObject):
    written = QtCore.Signal(object)

    def __init__(self, owner):
        super().__init__(owner.root.qt)
        self.owner = owner
        self.store = LastWorkStore()
        from film_foundry.ui.preferences import load_preferences
        generations = load_preferences().get("preview_options", {}).get("generations", 1)
        interval = load_preferences().get("preview_options", {}).get("recovery_minutes", 5)
        self.store.interval_minutes = interval if interval in (1, 5, 10, 30) else 5
        self.store.generations = generations if generations in (3,5) else 1
        self.last = None
        self.materials = {}
        self.restoring = False
        self.closing = False
        self.ready_to_close = False
        self.last_error = None
        self.baseline = owner.work_sessions.film_snapshot()
        self.lock = QtCore.QLockFile(str(self.store.directory / 'writer.lock'))
        self.lock.setStaleLockTime(0)
        self.marker = self.store.directory / 'running.json'
        self.enabled = False
        self.unclean = False
        self.writer = None
        try:
            self.store.directory.mkdir(parents=True, exist_ok=True)
            self.enabled = self.lock.tryLock(0)
            self.unclean = self.enabled and self.marker.exists() and self.store.path.is_file()
            if self.enabled:
                atomic_write_json(self.marker, {'running': True})
                self.writer = LatestWorkWriter(self.store, self.written.emit)
        except OSError as exc:
            self.enabled = False
            self.lock.unlock()
            self.last_error = str(exc)
            record_exception('Initialize automatic recovery', exc)
            owner.status.set(text('自动保险不可写：', 'Automatic recovery is not writable: ')+str(exc))
        self.written.connect(self._written)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.capture)
        if self.enabled:
            self.timer.start()
            owner.workspace_host.currentChanged.connect(lambda _: QtCore.QTimer.singleShot(0, self.capture) if self.unclean else None)
        self.attach_menu()
        if owner.library_view is not None:
            self.attach_library(owner.library_view)

    def attach_menu(self):
        menu = self.owner.file_menu
        menu.addSeparator()
        self.action = menu.addAction(text('恢复上一次工作…', 'Recover last work…'), self.restore)
        self.action.setToolTip(text('自动保险只含参数、设置和来源引用；不含图像或未保存的材料状态。',
                                   'Automatic settings and source references; no images or unsaved material states.'))
        menu.addAction(text('打开恢复与诊断记录目录', 'Open recovery & diagnostics folder'),
            lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.store.directory))))
        self.unrestored = menu.addMenu(text('未恢复的工作', 'Unrecovered work'))
        self.unrestored.aboutToShow.connect(self.populate_archives)
        self.older = menu.addMenu(text('较早的自动保险', 'Earlier recovery settings'))
        self.older.aboutToShow.connect(self.populate_older)
        menu.aboutToShow.connect(self.update_action)
        self.update_action()

    def populate_archives(self):
        from datetime import datetime
        self.unrestored.clear()
        for path in self.store.archives():
            try:
                stamp = int(path.stem.split('-')[-1]) / 1e9
                label = datetime.fromtimestamp(stamp).strftime('%Y-%m-%d %H:%M:%S')
            except (OSError, ValueError, OverflowError):
                label = path.stem
            self.unrestored.addAction(label, lambda checked=False, p=path: self.restore(archive=p))

    def populate_older(self):
        from datetime import datetime
        self.older.clear()
        try:
            records = self.store.records()[1:self.store.generations]
        except (ValueError, OSError, KeyError, TypeError):
            records = []
        for record in records:
            try:
                label = datetime.fromisoformat(record['saved_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            except (ValueError, OverflowError, OSError):
                label = text('时间未知', 'Unknown time')
            self.older.addAction(label, lambda checked=False, work=record['work']: self.restore(work=work))
        if not records:
            self.older.addAction(text('暂无较早记录', 'No earlier records')).setEnabled(False)

    def update_action(self):
        self.unrestored.menuAction().setVisible(bool(self.store.archives()))
        self.older.menuAction().setVisible(self.store.generations in (3, 5))
        self.older.setEnabled(not self.restoring and not self.closing)
        self.action.setEnabled(self.store.path.is_file() and not self.restoring and not self.closing)
        if self.unclean:
            self.action.setText(text('上次未正常退出 · 恢复工作…', 'Previous exit incomplete · Recover work…'))
            self.owner.file_button.setText(text('文件 · 恢复', 'File · Recover'))
        else:
            self.action.setText(text('恢复上一次工作…', 'Recover last work…'))
            self.owner.file_button.setText(text('文件', 'File'))
        if not self.enabled:
            self.action.setToolTip(self.last_error or text('另一个程序窗口正在维护自动保险槽；本窗口不会覆盖。',
                                                          'Another application window owns the automatic recovery slot.'))

    def recovery_decided(self):
        self.unclean = False
        library = self.owner.library_view
        if library is not None:
            library.recovery_banner.hide()
        self.update_action()
        if self.enabled and not self.closing:
            self.timer.start()

    def attach_library(self, library):
        library.recovery_banner.setVisible(self.unclean)
        library.recover.clicked.connect(self.restore)
        library.ignore_recovery.clicked.connect(self.recovery_decided)

    def snapshot(self):
        owner = self.owner
        # A transient half-entered number must not replace the last valid film recipe.
        try:
            film = owner.work_sessions.film_snapshot()
        except (ValueError, TypeError):
            film = deepcopy(self.last.get('film')) if self.last else None
        unchanged = film is not None and all(film.get(k) == self.baseline.get(k) for k in ('config', 'paths', 'ui_options'))
        unchanged = unchanged and all(film.get('desktop_work', {}).get(k) == self.baseline.get('desktop_work', {}).get(k)
                                      for k in ('editors', 'principle_chemistry'))
        if unchanged and not owner.work_sessions.has_content(owner.film_workspace):
            film = None
        pages = []
        live = set(owner._material_windows.values())
        self.materials = {p: v for p, v in self.materials.items() if p in live}
        for page in owner._material_windows.values():
            # Source ownership may change in its worker. Keep its last coherent draft.
            if not page.busy:
                session = page.session
                source_kind = 'optical' if session.optical_source is not None or session.optical_reference is not None else 'image' if session.image is not None else 'archive'
                source = dict(path=session.source_path, kind=source_kind,
                              body=session.optical_body_id,
                              relay=asdict(session.optical_relay) if session.optical_relay is not None else None)
                if all(getattr(session, key) is None for key in ('image', 'state', 'group')):
                    source = deepcopy(getattr(page, '_recovery_source', source))
                self.materials[page] = dict(kind=page.kind, title=page.windowTitle(),
                    source=source,
                    draft=drafts.capture(page), scan=deepcopy(session.scan_adjustments),
                    precision=session.storage_precision,
                    batch=list(page.paths) if getattr(page, '_planning_batch', False) else None,
                    previous_stage=getattr(session.state, 'stage', None))
            if page in self.materials:
                pages.append(deepcopy(self.materials[page]))
        active = owner.workspace_host.currentWidget()
        material_pages = [p for p in owner._material_windows.values() if p in self.materials]
        roll = getattr(getattr(owner, 'roll_view', None), 'session', None)
        roll_drafts = []
        processes = []
        for page in dict.fromkeys([*owner.workspace_host._pages, *owner.root.qt.findChildren(QtWidgets.QDialog)]):
            roll_path = getattr(page, '_recovery_roll_path', None)
            if roll_path and page.isVisible():
                try:
                    roll_drafts.append(dict(path=roll_path, config=asdict(page.read_config())))
                except (ValueError, TypeError):
                    roll_drafts.extend(r for r in (self.last or {}).get('roll_drafts', []) if r['path'] == roll_path)
            if page.__class__.__name__ == 'FilmProcessWorkspace' and page.path:
                if page.busy:
                    processes.extend(r for r in (self.last or {}).get('processes', []) if r['path'] == str(page.path))
                else:
                    processes.append(dict(path=str(page.path), target=page.target.currentData()))
        return dict(film=film, materials=pages,
                    roll_path=str(roll.path) if roll else None,
                    roll_drafts=roll_drafts, processes=processes,
                    active_material=material_pages.index(active) if active in material_pages else None)

    def capture(self):
        if not self.enabled or self.restoring or self.closing or self.owner._workspace_io_busy:
            return
        try:
            work = self.snapshot()
            if not any(work[k] for k in ('film', 'materials', 'roll_path', 'roll_drafts', 'processes')):
                return  # Launching or closing an empty shell preserves the previous slot.
            if self.unclean:
                self.store.archive_unrecovered()
                self.recovery_decided()
            if work != self.last:
                self.last = work
                self.writer.submit(work)
        except Exception as exc:
            record_exception('Capture last-work settings', exc)
            self._written(str(exc))

    @QtCore.Slot(object)
    def _written(self, error):
        if error:
            self.last = None  # Retry on the next timer; the previous file is intact.
            if error != self.last_error:
                self.owner.status.set(text('自动保险保存失败，原记录保留：', 'Automatic recovery save failed; previous record retained: ')+error)
        self.last_error = error
        self.update_action()

    def begin_close(self):
        if self.ready_to_close:
            return True
        if self.closing:
            return False
        self.capture()
        self.closing = True
        self.timer.stop()
        if self.writer:
            self.writer.close()
        def finish():
            if self.writer and self.writer.thread.is_alive():
                QtCore.QTimer.singleShot(30, finish)
                return
            if self.enabled:
                try:
                    if not self.unclean:
                        self.marker.unlink(missing_ok=True)
                except OSError as exc:
                    record_exception('Clear clean-exit marker', exc)
                self.lock.unlock()
            self.materials.clear()
            self.last = None
            self.ready_to_close = True
            previous = self.owner.work_sessions.bypass
            self.owner.work_sessions.bypass = True
            try:
                self.owner.root.qt.close()
            finally:
                self.owner.work_sessions.bypass = previous
        QtCore.QTimer.singleShot(0, finish)
        return False

    def restore(self, checked=False, *, work=None, archive=None):
        owner = self.owner
        if owner._processing or owner._preview_running or owner._workspace_io_busy or owner.workspace_host.busy_pages():
            owner.status.set(text('请等待当前任务完成后恢复。', 'Wait for active tasks before recovery.'))
            return False
        try:
            # Read before any save-before-replace guard can update the slot.
            if work is None:
                work = self.store.read(archive)['work']
        except Exception as exc:
            record_exception('Read last-work settings', exc)
            QtWidgets.QMessageBox.warning(owner.root.qt, text('无法恢复', 'Cannot recover'), str(exc))
            return False
        def apply():
            self.restoring = True
            self.timer.stop()
            warnings = []
            try:
                if self.unclean and (archive is not None or work != self.store.read()['work']):
                    self.store.archive_unrecovered()
                film = work.get('film')
                if film:
                    film = deepcopy(film)
                    roll = film.get('desktop_work', {}).get('roll_path')
                    if roll and not Path(roll).exists():
                        film['desktop_work']['roll_path'] = None
                        warnings.append(text('胶卷来源缺失', 'Roll source missing'))
                    owner.work_sessions.apply_film(film)
                loads = self._restore_materials(work, warnings)
                roll_path = work.get('roll_path')
                if roll_path and Path(roll_path).exists():
                    owner.open_roll_workspace()
                    index = owner.roll_view.rolls.findData(str(roll_path))
                    if index >= 0:
                        owner.roll_view.rolls.setCurrentIndex(index)
                from film_foundry.app.electronic_roll import RollSession
                for draft in work.get('roll_drafts', []):
                    if Path(draft['path']).exists():
                        owner.open_roll_workspace()
                        owner._configure_roll(RollSession(draft['path']), recovery_config=draft['config'])
                for process in work.get('processes', []):
                    if Path(process['path']).exists():
                        page = owner.open_film_process_workspace(process['path'])
                        if page is not None:
                            target = process['target']
                            def select_target(*args, page=page, target=target):
                                page.completed.disconnect(page._recovery_target)
                                page._recovery_target = None
                                index = page.target.findData(target)
                                if index >= 0:page.target.setCurrentIndex(index)
                            if page.busy:
                                page._recovery_target = select_target
                                page.completed.connect(select_target)
                            else:
                                index = page.target.findData(target)
                                if index >= 0:page.target.setCurrentIndex(index)
                def finished():
                    self.restoring = False
                    self.recovery_decided()
                    owner.status.set(text('已恢复参数；预览重新计算。未保存的材料中间态需从来源重新执行。',
                        'Settings recovered; previews are recomputed. Unsaved material states require rerunning from source.') + ('\n'+'; '.join(warnings) if warnings else ''))
                self._load_next(loads, finished)
            except Exception as exc:
                record_exception('Restore last-work settings', exc)
                QtWidgets.QMessageBox.warning(owner.root.qt, text('恢复未完成', 'Recovery incomplete'), str(exc))
                self.restoring = False
                if self.enabled:
                    self.timer.start()
        owner.work_sessions.protect(apply, pages=[owner.film_workspace])
        return True

    def _restore_materials(self, work, warnings):
        from film_foundry.ui.material_lab_window import create_material_window
        from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
        from film_foundry.ui.sheet_lab_window import SheetBatchWindow
        restored, loads = [], []
        for record in work.get('materials', []):
            kind, batch = record['kind'], record.get('batch')
            if batch is None:
                page = create_material_window(kind)
            else:
                factory = MaterialBatchDialog if kind in ('peel', 'integral', 'plate') else SheetBatchWindow
                page = factory(kind, batch, load_source=False)
            source = record['source']
            page._recovery_source = deepcopy(source)
            draft = deepcopy(record['draft'])
            # The referenced source can precede the previously viewed product group.
            draft['body_index'] = 0
            try:
                drafts.restore(page, draft)
                page.session.scan_adjustments = deepcopy(record['scan'])
                page.session.storage_precision = record['precision']
            except Exception:
                page.close(); page.deleteLater()
                raise
            self.owner._mount_material_workspace(page, 'recovery:'+str(uuid4()), record['title'])
            drafts.restore_presentation(page, draft)
            page.preview_timer.stop()
            restored.append(page)
            path = source['path']
            page.session.source_path = path
            if not path or not Path(path).is_file():
                note = text('来源缺失；参数已恢复，请重新选择来源。', 'Source missing; settings restored. Choose a source again.')
                page.status.setText(note)
                warnings.append(note)
                continue
            def loaded(image, error, label, page=page, draft=draft):
                page.signals.done.disconnect(page._recovery_loaded)
                page._recovery_loaded = None
                if error:
                    return
                try:
                    drafts.restore(page, draft)
                    page.status.setText(text('参数已恢复 · 从来源继续；未重放未保存工序。',
                                            'Settings restored · continue from source; unsaved steps were not replayed.'))
                    page.edit_tools.record_draft()
                    page.request_preview()
                except Exception as exc:
                    record_exception('Restore material controls', exc)
                    page.status.setText(str(exc))
            size = page.work_size.value()
            if source['kind'] == 'optical':
                action = lambda p=page, s=source, n=size: p.session.open_medium(s['path'], body_id=s['body'], relay=s['relay'], long_edge=n)
            elif source['kind'] == 'image':
                action = lambda p=page, path=path, n=size: p.session.open_image(path, n)
            else:
                action = lambda p=page, path=path: p.session.load(path)
            loads.append((page, action, loaded))
        active = work.get('active_material')
        if isinstance(active, int) and 0 <= active < len(restored):
            self.owner.workspace_host.setCurrentWidget(restored[active])
        return loads

    def _load_next(self, loads, finished):
        # Opening seven large sources must not fan out into seven simultaneous decodes.
        if not loads or self.owner._workspace_exit_pending:
            finished()
            return
        page, action, loaded = loads.pop(0)
        if page not in self.owner._material_windows.values():
            self._load_next(loads, finished)
            return
        def completed(image, error, label):
            loaded(image, error, label)
            QtCore.QTimer.singleShot(0, lambda: self._load_next(loads, finished))
        page._recovery_loaded = completed
        page.signals.done.connect(completed)
        page._submit(action, text('重新读取来源…', 'Reloading source…'), render=False)
