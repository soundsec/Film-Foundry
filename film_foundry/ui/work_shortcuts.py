"""One context-aware route to existing work actions; text undo stays native."""
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.shortcut_settings import ShortcutSettings, SPECS, bind_hint


class WorkShortcuts(QtCore.QObject):
    def __init__(self, owner, *, desktop=False):
        parent = owner.root.qt if desktop else owner
        super().__init__(parent)
        self.owner = owner; self.desktop = desktop
        self.model=ShortcutSettings.current();self.held=None
        self.model.changed.connect(self.release_hold)
        if desktop:owner.workspace_host.currentChanged.connect(self.release_hold)
        QtWidgets.QApplication.instance().installEventFilter(self)

    def page(self, target):
        owner=self.owner
        root=owner.root.qt if self.desktop else owner
        if not isinstance(target,QtWidgets.QWidget) or target.window() is not root or not root.isEnabled():return None
        if QtWidgets.QApplication.activeModalWidget() is not None:return None
        if not self.desktop:
            host=getattr(owner,'_inline_editor_host',None)
            return owner if owner.isWindow() and not (host and host.frames) else None
        page=owner.workspace_host.currentWidget()
        host=getattr(page,'_inline_editor_host',None)
        if host and host.frames:return None
        if page is owner.film_workspace:return owner
        return page

    @staticmethod
    def editor(target):
        while target is not None:
            if isinstance(target,(QtWidgets.QLineEdit,QtWidgets.QTextEdit,QtWidgets.QPlainTextEdit,QtWidgets.QAbstractSpinBox,QtWidgets.QKeySequenceEdit)):return target
            target=target.parentWidget()
        return None

    def eventFilter(self,target,event):
        kind=event.type()
        if self.held is not None:
            if kind==QtCore.QEvent.Type.KeyRelease and event.key()==self.held[1]:
                if not event.isAutoRepeat():self.release_hold()
                event.accept();return True
            if kind in (QtCore.QEvent.Type.ApplicationDeactivate,QtCore.QEvent.Type.WindowDeactivate,QtCore.QEvent.Type.FocusOut):self.release_hold()
            elif kind==QtCore.QEvent.Type.Hide and isinstance(target,QtWidgets.QWidget):
                view=self.held[0]
                if target is view or target.isAncestorOf(view):self.release_hold()
        if kind not in (QtCore.QEvent.Type.ShortcutOverride,QtCore.QEvent.Type.KeyPress):return False
        command=self.model.command(event)
        if command is None:return False
        page=self.page(target)
        if page is None:return False
        spec=SPECS[command]
        film=self.desktop and page is self.owner
        material=not self.desktop or page in self.owner._material_windows.values()
        if spec.scope=='work' and not (film or material):return False
        if spec.scope=='film' and not film:return False
        if (event.key() == QtCore.Qt.Key.Key_Space and not event.modifiers()
                and isinstance(QtWidgets.QApplication.focusWidget(), QtWidgets.QAbstractButton)):
            return False
        editor=self.editor(target)
        if editor is not None:
            if command in ('undo','redo') or isinstance(editor,QtWidgets.QKeySequenceEdit):return False
            if spec.hold:return False
            if any(event.matches(key) for key in (QtGui.QKeySequence.StandardKey.Copy,QtGui.QKeySequence.StandardKey.Cut,QtGui.QKeySequence.StandardKey.Paste,QtGui.QKeySequence.StandardKey.SelectAll,QtGui.QKeySequence.StandardKey.Undo,QtGui.QKeySequence.StandardKey.Redo)):return False
            if event.key() in (QtCore.Qt.Key.Key_Left,QtCore.Qt.Key.Key_Right,QtCore.Qt.Key.Key_Up,QtCore.Qt.Key.Key_Down,QtCore.Qt.Key.Key_Home,QtCore.Qt.Key.Key_End,QtCore.Qt.Key.Key_Backspace,QtCore.Qt.Key.Key_Delete):return False
            if not event.modifiers() & (QtCore.Qt.KeyboardModifier.ControlModifier|QtCore.Qt.KeyboardModifier.AltModifier|QtCore.Qt.KeyboardModifier.MetaModifier) and not QtCore.Qt.Key.Key_F1<=event.key()<=QtCore.Qt.Key.Key_F35:return False
        if kind==QtCore.QEvent.Type.ShortcutOverride:
            event.accept();return True
        if not event.isAutoRepeat():
            if command in ('save','preview') and editor is not None:
                spin=editor if isinstance(editor,QtWidgets.QAbstractSpinBox) else editor.parentWidget()
                if isinstance(spin,QtWidgets.QAbstractSpinBox):spin.interpretText()
            if spec.hold:
                view=page.result_view if film else page.viewer
                if command == 'hold_previous':
                    history = page.preview_history
                    history.synchronize()
                    if len(history.entries) < 2:
                        return False
                    self.release_hold()
                    if film:page.preview_tabs.setCurrentWidget(view)
                    view.held_preview = QtGui.QPixmap.fromImage(history.entries[-2].image)
                    self.held = (view, event.key())
                    view._present()
                elif view.original is not None:
                    if film:page.preview_tabs.setCurrentWidget(view)
                    self.release_hold();self.held=(view,event.key());view.set_source_held(True)
            else:self.invoke(page,command)
        event.accept();return True

    @QtCore.Slot()
    def release_hold(self,*_):
        held=self.held;self.held=None
        if held is not None:
            try:
                if held[0].held_preview is not None:
                    held[0].held_preview = None
                    held[0]._present()
                held[0].set_source_held(False)
            except RuntimeError:pass  # Page destroyed while the key was down.

    def invoke(self,page,command):
        if command=='guide':
            return self.owner._open_context_guide() if self.desktop else self.owner.guide_button.click()
        if command=='advanced':return self.owner._toggle_page_advanced()
        film=self.desktop and page is self.owner
        tools=page.edit_tools
        if command in ('undo','redo'):
            if not film and not page.busy:tools.record_draft()
            return tools.actions.invoke('edit.'+command)
        if command in ('previous_stage','next_stage'):
            stages = list(page.pipeline_bar.buttons)
            selected = next((i for i,k in enumerate(stages) if page.pipeline_bar.buttons[k].isChecked()), 0)
            index = max(0, min(len(stages)-1, selected + (1 if command=='next_stage' else -1)))
            page.pipeline_bar.buttons[stages[index]].click()
        elif command == 'execute':
            if page.process_button.qt.isEnabled() and not page._processing:page.process_button.qt.click()
        elif command in ('fit','actual'):
            view = page.preview_tabs.currentWidget() if film else page.viewer
            (view.fit if command == 'fit' else view.actual_size)()
        elif command == 'strip':
            if film:page.test_strip_button.qt.click()
            else:
                from film_foundry.ui.material_test_strip import open_material_strip
                if not page.busy:open_material_strip(page)
        elif command=='compare':
            if tools.comparison.isEnabled():tools.comparison.setCurrentIndex((tools.comparison.currentIndex()+1)%tools.comparison.count())
        elif command=='history':page.preview_history.open()
        elif command=='preview':
            if film:
                if page.preview_button.qt.isEnabled():page._start_preview()
            else:page.request_preview()
        elif command=='save':
            if self.desktop:
                o=self.owner
                if not (o._processing or o._workspace_io_busy or o.work_sessions.pending or getattr(page,'busy',False)):
                    actual=o.film_workspace if film else page
                    if o.work_sessions.has_content(actual):o.work_sessions.save([actual])
            elif not page.busy and page.session.state is not None:page.save_dialog('state')


def annotate(tools):
    for key in ('undo','redo'):bind_hint(tools.buttons['edit.'+key],key)
    bind_hint(tools.comparison,'compare')
    view=getattr(tools.owner,'viewer',None)
    if view is not None:bind_hint(view,'hold_source')
