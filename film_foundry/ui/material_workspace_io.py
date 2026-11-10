"""Background material checkpoint I/O and atomic publication of restored pages."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from film_foundry.app.material_workspace import snapshot_document,save_material_workspace,load_material_workspace
from film_foundry.ui import material_workspace_state as drafts
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore,QtWidgets


class MaterialWorkspaceFiles(QtCore.QObject):
    completed=QtCore.Signal(object,object,str,str)

    def __init__(self,owner):
        super().__init__(owner.root.qt)
        self.owner=owner;self.path=None;self.busy=False;self.last_error=None;self._timers=[]
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='material-workspace')
        self.completed.connect(self._completed)
        pool=self.pool
        self.destroyed.connect(lambda *_:pool.shutdown(wait=False))

    def _ready(self):
        owner=self.owner
        if self.busy or owner._workspace_io_busy or owner._workspace_exit_pending or owner._processing or owner._preview_running or owner.workspace_host.busy_pages():
            self._error(text('请等当前操作完成后再保存或打开工作区。','Wait for active operations before saving or opening a workspace.'))
            return False
        return True

    def _error(self,message):
        self.last_error=message
        QtWidgets.QMessageBox.warning(self.owner.root.qt,self.title(),message)

    def title(self):
        return text('材料工作区','Material workspace')

    def restored_path(self,path):
        return None if self.owner._material_windows else Path(path).absolute()

    def save(self,*,save_as=False):
        if not self._ready():return False
        path=self.path
        if path is None or save_as:
            path,_=QtWidgets.QFileDialog.getSaveFileName(self.owner.root.qt,text('保存材料工作区目录包','Save material workspace package'),str(path or 'materials.ffworkspace'),'Material workspace (*.ffworkspace)')
            if not path:return False
            if not str(path).lower().endswith('.ffworkspace'):path=str(path)+'.ffworkspace'
        return self.save_to(path)

    def open(self):
        if not self._ready():return False
        path=QtWidgets.QFileDialog.getExistingDirectory(self.owner.root.qt,text('选择 .ffworkspace 工作区目录','Choose a .ffworkspace directory'),str(self.path.parent if self.path else ''))
        return self.open_from(path) if path else False

    def save_to(self,path):
        if not self._ready():return False
        host=self.owner.workspace_host
        pages=[host.widget(i) for i in range(host.count()) if host.widget(i) in self.owner._material_windows.values()]
        if not pages:
            self._error(text('先打开需要保存的材料页。此存档包含七类材料工作页及批量草稿。','Open material pages first. This archive covers the seven material families and batch drafts.'))
            return False
        try:
            documents=[snapshot_document(page.session,drafts.capture(page),title=host._pages[page][0],
                batch_paths=page.paths if getattr(page,'_planning_batch',False) else None) for page in pages]
            active=pages.index(host.currentWidget()) if host.currentWidget() in pages else 0
        except Exception as exc:self._error(str(exc));return False
        return self._start('save',path,lambda:save_material_workspace(path,documents,active=active))

    def open_from(self,path):
        if not self._ready():return False
        return self._start('open',path,lambda:load_material_workspace(path))

    def _start(self,operation,path,action):
        self.busy=True;self.last_error=None
        owner=self.owner;owner._workspace_io_busy=True
        self._timers=[timer for timer in [owner._preview_timer,*[p.preview_timer for p in owner._material_windows.values()]] if timer.isActive()]
        for timer in self._timers:timer.stop()
        owner.workspace_host.setEnabled(False)
        owner.root.qt.setEnabled(False)
        owner.workspace_menu.setText(text('正在保存…','Saving…') if operation=='save' else text('正在打开…','Opening…'))
        def done(task):
            try:result=task.result();error=None
            except Exception as exc:result=None;error=str(exc)
            self.completed.emit(result,error,operation,str(path))
        self.pool.submit(action).add_done_callback(done)
        return True

    def _publish(self,workspace):
        from film_foundry.ui.material_lab_window import create_material_window
        from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
        from film_foundry.ui.sheet_lab_window import SheetBatchWindow
        candidates=[]
        try:
            for document in workspace.documents:
                kind=document.session.kind
                if document.batch_paths is not None:
                    factory=MaterialBatchDialog if kind in ('peel','integral','plate') else SheetBatchWindow
                    page=factory(kind,document.batch_paths,load_source=False)
                else:page=create_material_window(kind)
                candidates.append((page,document))
                page.session=document.session
                page._restore_controls();page._sync();drafts.restore(page,document.draft)
                page.preview_timer.stop()
        except Exception:
            for page,_ in candidates:page.close();page.deleteLater()
            raise
        # No existing page is replaced, even if a family already has a tab.
        # Publish only after every draft has been accepted by its material UI.
        for page,document in candidates:
            key=page.kind if page.kind not in self.owner._material_windows and document.batch_paths is None else 'restored:'+str(uuid4())
            self.owner._mount_material_workspace(page,key,document.title)
            drafts.restore_presentation(page,document.draft)
            page._workspace_restore_draft=document.draft
            if hasattr(self.owner,'work_sessions'):
                self.owner.work_sessions.material_saved[page]=self.owner.work_sessions.material_snapshot(page)
            page._submit(lambda:None,text('恢复观看…','Restoring view…'),body_index=document.draft['body_index'])
            if document.batch_paths is not None:
                page.batch_button.setToolTip(text('新工作区携带全部批量来源；旧版存档仍使用原文件路径。','New workspaces carry every batch source; legacy archives still use original file paths.'))
        self.owner.workspace_host.setCurrentWidget(candidates[workspace.active][0])

    @QtCore.Slot(object,object,str,str)
    def _completed(self,result,error,operation,path):
        owner=self.owner
        try:
            if error is None:
                if operation=='open':
                    restored_path=self.restored_path(path)
                    # An accepted exit drains I/O without creating new work.
                    if not owner._workspace_exit_pending:self._publish(result)
                    self.path=restored_path
                    if self.path is not None and self.path.name=='workspace.json':self.path=self.path.parent
                else:self.path=Path(path).absolute()
        except Exception as exc:error=str(exc)
        finally:
            self.busy=False;owner._workspace_io_busy=False
            owner.workspace_menu.setText(text('快捷导航','Navigate'))
            owner.workspace_host.setEnabled(not owner._workspace_exit_pending)
            owner.root.qt.setEnabled(True)
            if not owner._workspace_exit_pending:
                for timer in self._timers:timer.start()
            self._timers=[]
        self.last_error=error
        if error:
            if not owner._workspace_exit_pending:self._error(error)
        else:
            message=self.title()+(' '+text('已保存：','saved: ') if operation=='save' else ' '+text('已打开：','opened: '))
            owner.workspace_menu.setToolTip(message+str(path));owner.status.set(message+str(path))
            page=owner.workspace_host.currentWidget()
            if operation=='save' and page in owner._material_windows.values():page.status.setText(message+str(path))
            from film_foundry.app.desktop_settings import remember_work
            kind='film' if self.__class__.__name__=='FilmWorkspaceFiles' else 'material'
            try:
                remember_work(path,kind)
                if hasattr(owner,'work_sessions') and (operation=='save' or kind=='film'):owner.work_sessions.mark_saved(kind)
                if owner.library_view:owner.library_view.sidebar.refresh()
            except (OSError,ValueError) as exc:owner.status.set(str(exc))
        callback=getattr(self,'after_operation',None)
        self.after_operation=None
        if callback:callback(error is None)

    def shutdown(self):
        self.pool.shutdown(wait=False)
