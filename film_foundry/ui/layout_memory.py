"""Window and pane preferences only; no recipe, image or processing state."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import weakref

from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.preferences import preferences_path
from film_foundry.ui.curve_editor import text


def layout_path():
    path = preferences_path()
    return path.with_name(path.stem + '_layout.json')


def _read(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and value.get('version') == 1:
            return {key: value[key] if isinstance(value.get(key), dict) else {}
                    for key in ('windows', 'panels')}
    except (OSError, ValueError):
        pass
    return {'windows': {}, 'panels': {}}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.layout_', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(dict(version=1, **value), stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class LayoutMemory(QtCore.QObject):
    """Remember actual drags, not sizes caused by hiding or rearranging panes."""
    instances = weakref.WeakSet()

    def __init__(self, window, key, default_size):
        super().__init__(window)
        self.window = window
        self.key = key
        self.default_size = default_size
        self.path = layout_path()
        self.panels = {}
        self.responsive = set()
        self.pending = {}
        self.geometry = None
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.flush)
        self.restore_timer = QtCore.QTimer(self)
        self.restore_timer.setSingleShot(True)
        self.restore_timer.timeout.connect(self._apply_all)
        self.resize_timer=QtCore.QTimer(self);self.resize_timer.setSingleShot(True)
        self.resize_timer.timeout.connect(self._apply_responsive)
        saved = _read(self.path)['windows'].get(key)
        if isinstance(saved, str) and len(saved) <= 1024:
            try:
                window.restoreGeometry(QtCore.QByteArray(bytes.fromhex(saved)))
            except ValueError:
                pass
        window.installEventFilter(self)
        self.instances.add(self)

    def bind(self, name, splitter, variant, defaults, *, responsive=False):
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(9)
        self.panels[name] = (splitter, variant, defaults, None)
        if responsive:self.responsive.add(name)
        splitter.installEventFilter(self)
        splitter.splitterMoved.connect(lambda *_: self.remember(name))
        for index in range(1, splitter.count()):
            splitter.handle(index).setToolTip(text('拖动调整面板比例；自动记忆，可在全局设置中重置。',
                'Drag to resize panels. Saved automatically; reset in Global settings.'))
        self.apply(name)

    def apply(self, name, *, force=False):
        splitter, variant, defaults, active = self.panels[name]
        key = self.key + '.' + name + '.' + variant()
        if active == key and not force:
            return
        saved = self.pending.get(key, _read(self.path)['panels'].get(key))
        valid = (isinstance(saved, list) and len(saved) == splitter.count()
                 and all(type(x) is int and 0 <= x <= 100000 for x in saved) and sum(saved) > 0)
        sizes = saved if valid else defaults()
        horizontal = splitter.orientation() == QtCore.Qt.Orientation.Horizontal
        extent = splitter.contentsRect().width() if horizontal else splitter.contentsRect().height()
        extent -= sum((splitter.handle(i).width() if horizontal else splitter.handle(i).height())
                      for i in range(1,splitter.count()) if not splitter.handle(i).isHidden())
        splitter.setSizes([round(x*max(1,extent)/sum(sizes)) for x in sizes])
        self.panels[name] = (splitter, variant, defaults, key)
        if not force:self.restore_timer.start(0)

    def remember(self, name):
        splitter, _, _, active = self.panels[name]
        sizes = splitter.sizes()
        if active and sum(sizes) > 0:
            self.pending[active] = sizes
            self.timer.start()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QtCore.QEvent.Type.Show:
            self.restore_timer.start(0)
        if kind == QtCore.QEvent.Type.Resize and any(watched is self.panels[name][0] for name in self.responsive):
            self.resize_timer.start(0)
        if watched is self.window and self.window.isWindow():
            if kind in (QtCore.QEvent.Type.Resize, QtCore.QEvent.Type.Move,
                        QtCore.QEvent.Type.WindowStateChange) and self.window.isVisible():
                if not self.window.isMinimized():
                    self.geometry = bytes(self.window.saveGeometry()).hex()
                    self.timer.start()
        if watched is self.window and kind == QtCore.QEvent.Type.Close:
            if self.window.isWindow():self.geometry = bytes(self.window.saveGeometry()).hex()
            # Embedded pages may be destroyed without their own close event.
            # Flush their last drag before a containing window can delete them.
            for memory in self.window.findChildren(LayoutMemory):memory.flush()
        return False

    def flush(self):
        self.timer.stop()
        if not self.pending and self.geometry is None:
            return
        value = _read(self.path)
        value['panels'].update(self.pending)
        if self.geometry is not None and self.window.isWindow():
            value['windows'][self.key] = self.geometry
        try:
            _write(self.path, value)
        except OSError as exc:
            logging.getLogger(__name__).warning('Could not save window layout: %s', exc)
            return
        self.pending.clear()
        self.geometry = None

    def dispose(self):
        self.flush()
        self.restore_timer.stop()
        self.resize_timer.stop()
        self.window.removeEventFilter(self)
        self.instances.discard(self)
        self.deleteLater()

    def reset(self):
        self.timer.stop()
        self.pending.clear()
        self.geometry = None
        if self.window.isWindow():
            self.window.setWindowState(QtCore.Qt.WindowState.WindowNoState)
            available = self.window.screen().availableGeometry()
            self.window.resize(min(self.default_size[0], available.width()),
                               min(self.default_size[1], available.height()))
            self.window.move(available.center() - self.window.rect().center())
        self.restore_timer.start(0)

    def _apply_all(self):
        for name in self.panels:self.apply(name, force=True)

    def _apply_responsive(self):
        saved=_read(self.path)['panels']
        for name in self.responsive:
            splitter,_,_,key=self.panels[name]
            value=self.pending.get(key,saved.get(key))
            if isinstance(value,list) and len(value)==splitter.count() and all(type(x) is int and 0<=x<=100000 for x in value) and sum(value)>0:continue
            self.apply(name,force=True)


def reset_layouts():
    """Reset saved and open layouts, retaining the chosen compact/studio mode."""
    path = layout_path()
    _write(path, {'windows': {}, 'panels': {}})
    for memory in list(LayoutMemory.instances):
        if memory.path != path:
            continue
        try:
            memory.reset()
        except RuntimeError:  # A Qt parent may already have deleted its children.
            LayoutMemory.instances.discard(memory)


def install_material_layout(window, default_size):
    # Tools stay with the preview; charts grow directly with the splitter.
    # This only reparents existing controls and callbacks.
    preview = window.preview_surface.layout()
    preview.removeWidget(window.save_footer)
    preview.removeWidget(window.view_group)
    window.view_group.layout().setDirection(QtWidgets.QBoxLayout.Direction.TopToBottom)
    index = preview.indexOf(window.viewer)
    preview.removeWidget(window.viewer)
    window.viewer.setMinimumHeight(140)
    details = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(details);layout.setContentsMargins(0,0,0,0)
    layout.addWidget(window.viewer,1)
    while preview.count() > index:
        item = preview.takeAt(index)
        if item.widget() is not None:layout.addWidget(item.widget())
        elif item.layout() is not None:
            child = item.layout();child.setParent(None);layout.addLayout(child)
        else:layout.addItem(item)
    window.inspection_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
    window.inspection_splitter.addWidget(details)
    window.inspection_splitter.addWidget(window.inspector.panels)
    window.inspection_splitter.setStretchFactor(0,1);window.inspection_splitter.setStretchFactor(1,0)
    preview.addWidget(window.inspection_splitter,1)
    preview.addWidget(window.save_footer)
    from film_foundry.ui.curve_editor import text
    window.config_tabs.insertTab(2, window.view_group, text('观看', 'View'))
    window.view_group.setTitle('')
    window.histogram_sidebar_host=QtWidgets.QWidget()
    side=QtWidgets.QVBoxLayout(window.histogram_sidebar_host);side.setContentsMargins(0,0,0,0)
    window.histogram_sidebar_host.setMinimumHeight(112)
    window.sidebar_inspector_splitter=QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
    window.sidebar_inspector_splitter.addWidget(window.histogram_sidebar_host)
    window.sidebar_inspector_splitter.addWidget(window.controls_scroll)
    window.sidebar_inspector_splitter.setStretchFactor(0,0);window.sidebar_inspector_splitter.setStretchFactor(1,1)
    window.workspace_splitter.insertWidget(0,window.sidebar_inspector_splitter)
    window.inspector.sync_placement()
    from film_foundry.ui.parameter_pins import CommonControls
    window.common_controls=CommonControls(window,False,window.left)
    window.layout_memory = LayoutMemory(window, 'material.' + window.kind, default_size)
    window.layout_memory.bind('workspace', window.workspace_splitter,
        lambda: window._workspace_layout,
        lambda: [440, 720, 0] if window._workspace_layout == 'compact' else [350, 500, 350])
    window.layout_memory.bind('inspection',window.inspection_splitter,
        lambda:'vertical' if window.inspector.histogram.settings.values['layout']=='sidebar' else 'below',
        lambda:[max(180,window.inspection_splitter.height()-min(154,max(112,window.inspection_splitter.height()-340))-9),
                min(154,max(112,window.inspection_splitter.height()-340))],responsive=True)
    window.layout_memory.bind('histogram_sidebar',window.sidebar_inspector_splitter,
        lambda:window.inspector.histogram.settings.values['layout'],
        lambda:[154,max(220,window.sidebar_inspector_splitter.height()-163)]
        if window.inspector.histogram.settings.values['layout']=='sidebar' else [0,1])
