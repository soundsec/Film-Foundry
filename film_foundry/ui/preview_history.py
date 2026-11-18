"""Bounded display history. Never owns formation states or persistent archives."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from itertools import count
from weakref import WeakSet, ref

from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.comparison_view import ComparisonView, comparison_choice
from film_foundry.ui.curve_editor import text

_serial = count(1)
_open_dialog = None


@dataclass
class PreviewEntry:
    serial: int
    image: object
    payload: dict
    context: object
    guard: object
    label: str
    time: str
    difference: str = ""


class HistoryBudget:
    def __init__(self, max_bytes=32*1024*1024):
        self.max_bytes = max_bytes
        self.stores = WeakSet()
        self.limit = 6

    def configure(self):
        from film_foundry.ui.preferences import load_preferences
        values = load_preferences().get('preview_options', {})
        size = values.get('memory', 32)
        self.max_bytes = (size if size in (16, 32, 64, 128) else 32) * 1048576
        limit = values.get('history', 6)
        self.limit = limit if limit in (3, 6, 12, 24) else 6
        for store in list(self.stores):
            store.trim_count()
        self.trim()

    @property
    def bytes(self):
        return sum(e.image.sizeInBytes() for s in self.stores for e in s.entries)

    def trim(self):
        changed = set()
        while self.bytes > self.max_bytes:
            _, serial, store, entry = min((e.serial == s.pinned, e.serial, s, e)
                                          for s in self.stores for e in s.entries)
            store.entries.remove(entry)
            if serial == store.pinned:
                store.pinned = None
            changed.add(store)
        for store in changed:store.changed.emit()


history_budget = HistoryBudget()


def parameter_changes(before, after, path='', labels=None):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes = []
        for key in sorted(before.keys() | after.keys()):
            changes.extend(parameter_changes(before.get(key), after.get(key), f'{path}.{key}'.strip('.'),labels))
        return changes
    if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)) and len(before) == len(after):
        changes = []
        for index, (left, right) in enumerate(zip(before, after)):
            changes.extend(parameter_changes(left, right, f'{path}[{index}]',labels))
        return changes
    name = path.removeprefix('settings.config.').removeprefix('draft.values.')
    from film_foundry.ui.film_test_strip import _LABELS
    from film_foundry.ui.i18n import tr
    key=name.rsplit('.',1)[-1]
    label=text(*_LABELS[key]) if key in _LABELS else tr('label.'+key)
    control_name=(labels or {}).get(name.replace('[','.').replace(']',''))
    if control_name:name=control_name
    elif label!='label.'+key:name=label
    def display(value):
        if isinstance(value, (dict, tuple, list)):
            return f'[{len(value)}]'
        return f'{value:g}' if isinstance(value, float) else str(value)
    return [f'{name} {display(before)} → {display(after)}']


class PreviewHistory(QtCore.QObject):
    changed = QtCore.Signal()

    def __init__(self, parent, context, can_restore, restore, *, budget=None):
        super().__init__(parent)
        self.labels=dict
        self.entries = []
        self.pinned = None
        self.context = context
        self.can_restore = can_restore
        self.restore_parameters = restore
        self.budget = budget or history_budget
        if budget is None:
            self.budget.configure()
        self.budget.stores.add(self)
        weak_store = ref(self)
        stores = self.budget.stores
        self.destroyed.connect(lambda: stores.discard(weak_store()) if weak_store() is not None else None)
        self.dialog = None
        self._context = None

    def synchronize(self):
        context = self.context()
        if context != self._context:
            self._context = context
            self.clear()
        return context

    def clear(self):
        self.pinned = None
        self.entries.clear()
        self.changed.emit()

    def trim_count(self):
        unpinned = [entry for entry in self.entries if entry.serial != self.pinned]
        removed = unpinned[:-self.budget.limit]
        for entry in removed:
            self.entries.remove(entry)
        if removed:self.changed.emit()

    def add(self, image, payload, context, guard, label):
        if context != self.synchronize() or image is None or image.isNull():return
        # Only completed presentations enter history; equal parameters do not
        # duplicate an entry. No full-image comparison or hash is needed.
        if self.entries and (self.entries[-1].payload, self.entries[-1].guard) == (payload, guard):return
        image = image.scaled(900,900,QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                             QtCore.Qt.TransformationMode.SmoothTransformation) if max(image.width(),image.height()) > 900 else image
        image = image.convertToFormat(QtGui.QImage.Format.Format_RGB888).copy()
        changes = parameter_changes(self.entries[-1].payload, payload, labels=self.labels()) if self.entries else []
        self.entries.append(PreviewEntry(next(_serial),image,deepcopy(payload),context,guard,label,
                                         datetime.now().strftime('%H:%M:%S'), '\n'.join(changes)))
        self.trim_count()
        self.budget.trim()
        self.changed.emit()

    def button(self, layout):
        button = QtWidgets.QToolButton()
        button.setText(text('历史…','History…'))
        button.setToolTip(text('比较本次工作预览。历史容量可在全局设置调整。','Compare work previews. Adjust history limits in global settings.'))
        from film_foundry.ui.shortcut_settings import bind_hint
        bind_hint(button,'history')
        button.clicked.connect(self.open)
        layout.addWidget(button)
        return button

    def open(self):
        global _open_dialog
        self.synchronize()
        if self.dialog is not None:
            self.dialog.raise_();self.dialog.activateWindow();return self.dialog
        previous = _open_dialog() if _open_dialog else None
        if previous is not None:
            try:previous.close()
            except RuntimeError:pass  # The Qt window may already have been destroyed.
        dialog = HistoryDialog(self, self.parent())
        self.dialog = dialog;_open_dialog = ref(dialog)
        dialog.finished.connect(lambda *_:setattr(self,'dialog',None))
        dialog.show()
        return dialog

    def dispose(self):
        if self.dialog is not None:self.dialog.close()
        self.clear()
        self.budget.stores.discard(self)


class HistoryDialog(QtWidgets.QDialog):
    def __init__(self, history, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose,True)
        self.setWindowTitle(text('预览历史对照','Preview history comparison'))
        self.resize(980,650);self.setMinimumSize(620,440)
        self.history = history;self.selected = None;self._pair_key = None
        root = QtWidgets.QVBoxLayout(self)
        self.note = QtWidgets.QLabel(text('左：选中历史  ·  右：最近成功预览。选择只看图；恢复参数才修改工作。屏幕草稿不能代替正式尺寸检查。',
            'Left: selected history · Right: latest successful preview. Selecting only compares; Restore changes settings. Screen drafts do not replace full-resolution inspection.'))
        self.note.setWordWrap(True);root.addWidget(self.note)
        self.labels = QtWidgets.QLabel();self.labels.setWordWrap(True);root.addWidget(self.labels)
        self.viewer = ComparisonView();root.addWidget(self.viewer,1)
        row = QtWidgets.QHBoxLayout();root.addLayout(row)
        self.comparison = comparison_choice(self.viewer);row.addWidget(self.comparison)
        self.comparison.setCurrentIndex(self.comparison.findData('wipe'))
        self.comparison.setToolTip(text('左侧/按住时显示选中历史，右侧显示最近成功预览。','Selected history on the left/on hold; latest successful preview on the right.'))
        for label, action in ((text('适合窗口','Fit'),self.viewer.fit),('100%',self.viewer.actual_size)):
            button=QtWidgets.QPushButton(label);button.clicked.connect(action);row.addWidget(button)
        row.addStretch()
        self.list = QtWidgets.QListWidget()
        self.list.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.list.setFlow(QtWidgets.QListView.Flow.LeftToRight);self.list.setWrapping(False)
        self.list.setIconSize(QtCore.QSize(118,76));self.list.setGridSize(QtCore.QSize(148,113))
        self.list.setFixedHeight(132);self.list.setMovement(QtWidgets.QListView.Movement.Static)
        root.addWidget(self.list)
        self.list.currentItemChanged.connect(self.select)
        self.message = QtWidgets.QLabel();self.message.setWordWrap(True);root.addWidget(self.message)
        footer = QtWidgets.QHBoxLayout();root.addLayout(footer)
        clear = QtWidgets.QPushButton(text('清空历史','Clear history'));clear.clicked.connect(history.clear);footer.addWidget(clear)
        self.pin = QtWidgets.QPushButton(text('钉住 / 取消参照', 'Pin / unpin reference'))
        self.pin.clicked.connect(self.toggle_pin)
        footer.addWidget(self.pin)
        footer.addStretch()
        self.apply = QtWidgets.QPushButton(text('恢复选中参数','Restore selected settings'));self.apply.clicked.connect(self.adopt);footer.addWidget(self.apply)
        close = QtWidgets.QPushButton(text('关闭','Close'));close.clicked.connect(self.close);footer.addWidget(close)
        history.changed.connect(self.refresh)
        self.timer=QtCore.QTimer(self);self.timer.setInterval(250);self.timer.timeout.connect(self.sync);self.timer.start()
        self.refresh()

    def toggle_pin(self):
        self.history.pinned = None if self.history.pinned == self.selected else self.selected
        self.history.trim_count()
        self.history.changed.emit()

    def refresh(self):
        entries=self.history.entries
        selected=self.selected
        if not any(e.serial == selected for e in entries):
            selected = self.history.pinned or (entries[max(0,len(entries)-2)].serial if entries else None)
        blocker=QtCore.QSignalBlocker(self.list);self.list.clear()
        for index,e in enumerate(entries,1):
            icon=QtGui.QIcon(QtGui.QPixmap.fromImage(e.image.scaled(118,76,QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation)))
            delta = e.difference.splitlines()
            summary = delta[0].split(' → ')[0].rsplit(' ',1)[0].split('.')[-1] if delta else ''
            title = ('★ ' if self.history.pinned == e.serial else '') + e.time
            item=QtWidgets.QListWidgetItem(icon,title+'\n'+summary,self.list)
            item.setData(QtCore.Qt.ItemDataRole.UserRole,e.serial);item.setToolTip(e.label + ("\n"+e.difference if e.difference else ""))
            if e.serial==selected:self.list.setCurrentItem(item)
        del blocker
        self.selected=selected;self.select()

    def select(self,*_):
        item=self.list.currentItem()
        self.selected=item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        entry=next((e for e in self.history.entries if e.serial==self.selected),None)
        latest=self.history.entries[-1] if self.history.entries else None
        key=(entry.serial,latest.serial) if entry and latest else None
        if key!=self._pair_key:
            self._pair_key=key
            if entry:
                self.viewer.set_pixmap_pair(QtGui.QPixmap.fromImage(entry.image),QtGui.QPixmap.fromImage(latest.image))
                self.labels.setToolTip(entry.difference)
                self.labels.setText(text('历史 ','History ')+entry.time+' · '+entry.label+'  |  '+text('最近 ','Latest ')+latest.time+' · '+latest.label)
            else:
                self.viewer.clear_image(text('成功更新预览后，记录会显示在这里。','Successful previews will appear here.'));self.labels.clear()
        self.sync()

    def sync(self):
        self.history.synchronize()
        entry=next((e for e in self.history.entries if e.serial==self.selected),None)
        reason=self.history.can_restore(entry) if entry else text('暂无可恢复的记录。','No record to restore.')
        self.pin.setEnabled(entry is not None)
        self.apply.setEnabled(entry is not None and not reason)
        self.message.setText(reason or text('恢复后可撤回；不自动保存成片。','Restore supports Undo; no output is saved automatically.'))

    def adopt(self):
        entry=next((e for e in self.history.entries if e.serial==self.selected),None)
        if entry is None:return
        reason=self.history.can_restore(entry)
        if reason:self.message.setText(reason);return
        try:self.history.restore_parameters(entry)
        except Exception as exc:self.message.setText(str(exc));return
        self.accept()

    def done(self,result):
        self.timer.stop()
        try:self.history.changed.disconnect(self.refresh)
        except RuntimeError:pass
        self.viewer.clear_image();self.list.clear()
        super().done(result)
