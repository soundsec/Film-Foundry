"""Desktop work preservation, using public session/material storage APIs."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from film_foundry.app.desktop_settings import load_settings, save_settings, remember_work, work_directory
from film_foundry.model.session import save_session, load_session
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text


class WorkspaceSessions:
    def __init__(self, owner):
        self.owner=owner;self.film_saved=self.film_snapshot();self.material_saved={};self.print_saved={}
        self.bypass=False;self.pending=False

    def has_content(self,page):
        """Cheap UI availability check, without serializing drafts or inspecting pixels."""
        owner=self.owner
        if page is owner.film_workspace:
            if owner._active_roll is not None:return True
            frozen=owner.pipeline_mode.get() in ('scan','develop_latent')
            if owner._selected_photos and not frozen:return True
            source=owner.negative_path.get() if frozen else owner.input_path.get()
            if not source.strip():return False
            path=Path(source)
            if path.is_file():return True
            from film_foundry.tools import run_darkroom_gui as legacy
            return path.is_dir() and path not in (legacy.DEFAULT_INPUT_DIR,legacy.DEFAULT_NEGATIVE_DIR)
        session=getattr(page,'session',None)
        if page in owner._material_windows.values():
            return session is not None and any(getattr(session,key,None) is not None for key in ('image','state','group'))
        if page is not None and page.__class__.__name__=='PrintmakingWorkspace':
            return session is not None and session.image is not None
        return False

    def film_snapshot(self):
        owner=self.owner
        payload=owner._session_payload();payload.pop('created_at',None)
        payload['desktop_work']={
            'selected_photos':list(owner._selected_photos),'layout':owner._workspace_layout,
            'roll_path':str(owner._active_roll.path) if owner._active_roll is not None else None,
            'expert':owner.expert_mode.get(),'expert_mode':owner._expert_operating_mode,
            'principle_chemistry':asdict(owner._principle_chemistry) if owner._principle_chemistry is not None else None,
            'editors':{bank:asdict(editor._config_from_controls()) for bank,editor in owner._editor_windows.items()
                       if bank!='runtime' and editor.root.winfo_exists() and hasattr(editor,'_config_from_controls')}}
        return payload

    def material_snapshot(self,page):
        from film_foundry.ui.material_workspace_state import capture
        session=page.session
        return (id(session.state),id(session.group),id(session.image),session.source_path,
                getattr(session.state,'process_json',None),getattr(session.group,'provenance_json',None),capture(page))

    def mark_saved(self,kind):
        if kind in ('film','session'):self.film_saved=self.film_snapshot()
        else:
            for page in self.owner._material_windows.values():self.material_saved[page]=self.material_snapshot(page)

    def dirty(self,pages=None):
        owner=self.owner
        film=pages is None or owner.film_workspace in pages or owner.editor_host in pages
        changed=[]
        if film and self.film_snapshot()!=self.film_saved:changed.append(owner.film_workspace)
        for page in owner._material_windows.values():
            if pages is not None and page not in pages:continue
            if self.material_snapshot(page)!=self.material_saved.get(page):changed.append(page)
        for printing in owner.workspace_host._pages:
            if printing.__class__.__name__=='PrintmakingWorkspace' and printing.session.image is not None and (pages is None or printing in pages):
                if self.print_snapshot(printing)!=self.print_saved.get(printing):changed.append(printing)
        return changed

    def print_snapshot(self,page):
        page.commit_layer()
        session=page.session
        return (id(session.image),tuple(id(p) for p in session.plates),[asdict(s) for s in session.styles],
                tuple(session.paper),session.dpi,page.registration.isChecked(),page.mode.currentIndex())

    def decision(self):
        values=load_settings();policy=values['leave_policy']
        if policy!='ask':return policy
        box=QtWidgets.QMessageBox(self.owner.root.qt)
        box.setWindowTitle(text('保存当前工作','Save current work'))
        box.setText(text('此操作会关闭或替换尚未保存的工作。','This action will close or replace unsaved work.'))
        box.setInformativeText(text('保存后可从图库的“最近会话与工作”重新打开。','Saved work can be reopened from Recent sessions & work in the library.'))
        save=box.addButton(text('保存工作','Save work'),QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard=box.addButton(text('不保存','Discard'),QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        cancel=box.addButton(text('取消','Cancel'),QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save);box.setEscapeButton(cancel)
        remember=QtWidgets.QCheckBox(text('记住选择，不再询问（可在全局设置修改）','Remember this choice (change in Settings)'));box.setCheckBox(remember)
        box.exec()
        choice='save' if box.clickedButton()==save else 'discard' if box.clickedButton()==discard else 'cancel'
        if remember.isChecked() and choice!='cancel':values['leave_policy']=choice;save_settings(values)
        return choice

    def protect(self,action,*,pages=None):
        if self.bypass:action();return True
        if self.pending:return False
        try:changed=self.dirty(pages)
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self.owner.root.qt,text('无法读取工作配置','Cannot read work'),str(exc));return False
        if not changed:action();return True
        decision=self.decision()
        if decision=='cancel':return False
        if decision=='discard':action();return True
        return self.save(changed,action)

    def save(self,pages=None,after=None):
        owner=self.owner
        pages=list(pages) if pages is not None else self.dirty()
        try:
            printing=next((page for page in pages if page.__class__.__name__=='PrintmakingWorkspace'),None)
            if printing is not None and printing in pages and printing.session.image is not None:
                remaining=[page for page in pages if page is not printing]
                path=work_directory()/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid4().hex[:6]+'-plates')
                self.pending=True
                def printed(ok):
                    self.pending=False
                    if ok:
                        self.print_saved[printing]=self.print_snapshot(printing)
                        self.save(remaining,after)
                if not printing.save_work(path,printed):self.pending=False;return False
                return True
            if owner.film_workspace in pages:
                payload=self.film_snapshot()
                path=work_directory()/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid4().hex[:6]+'.json')
                save_session(path,payload)
                source=payload['paths'].get('input') or payload['paths'].get('developed_medium')
                remember_work(path,'session',Path(source).name if source else text('胶片会话','Film session'))
                self.film_saved=payload
            materials=[page for page in pages if page in owner._material_windows.values()]
            if materials:
                self.pending=True
                def completed(ok):
                    self.pending=False
                    if ok:
                        self.mark_saved('material')
                        if after:after()
                owner._workspace_files.after_operation=completed
                path=work_directory()/(datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid4().hex[:6]+'.ffworkspace')
                if not owner._workspace_files.save_to(path):
                    owner._workspace_files.after_operation=None;self.pending=False;return False
                return True
            owner.status.set(text('工作已保存，可从图库重新打开。','Work saved; reopen it from the library.'))
            if owner.library_view:owner.library_view.sidebar.refresh()
            if after:after()
            return True
        except Exception as exc:
            QtWidgets.QMessageBox.warning(owner.root.qt,text('保存失败，工作保留','Save failed; work retained'),str(exc));return False

    def open_work(self,path,kind):
        owner=self.owner
        def open_now():
            try:
                if kind=='print':owner._open_print_workspace().open_project(path);return
                if kind=='film':owner._film_workspace_files.open_from(path);return
                if kind=='material':owner._workspace_files.open_from(path);return
                payload=load_session(path)
                from film_foundry.model.config import DarkroomConfig
                DarkroomConfig.from_dict(payload['config'])
                roll_path=payload.get('desktop_work',{}).get('roll_path')
                from film_foundry.app.electronic_roll import RollSession
                roll=RollSession(roll_path) if roll_path else None
                from film_foundry.ui.film_workspace_io import _capture_controls,_restore_controls
                previous=_capture_controls(owner);photos=list(owner._selected_photos)
                ready=owner._auto_preview_ready;owner._auto_preview_ready=False
                try:
                    owner._active_roll=None;owner._apply_session_payload(payload)
                    desktop=payload.get('desktop_work',{})
                    owner._selected_photos=list(desktop.get('selected_photos',[]));owner._sync_selected_photos()
                    owner.set_workspace_layout(desktop.get('layout',owner._workspace_layout),persist=False)
                    owner._expert_operating_mode=desktop.get('expert_mode',owner._expert_operating_mode)
                    owner.expert_mode.set(desktop.get('expert',owner.expert_mode.get()))
                    chemistry=desktop.get('principle_chemistry')
                    if chemistry is not None:owner._principle_chemistry=DarkroomConfig.from_dict({'chemistry':chemistry}).chemistry
                    owner.expert_operation.blockSignals(True);owner.expert_operation.setCurrentIndex(owner.expert_operation.findData(owner._expert_operating_mode));owner.expert_operation.blockSignals(False)
                    owner._update_expert_visibility()
                    for bank,config in desktop.get('editors',{}).items():
                        owner.open_embedded_editor(bank)
                        owner._editor_windows[bank]._set_controls_from_config(DarkroomConfig.from_dict(config))
                    owner._active_roll=roll
                    owner.mode_choice.qt.setEnabled(roll is None);owner.pages[owner.page_index['film']].qt.setEnabled(roll is None)
                except Exception:
                    _restore_controls(owner,previous);owner._selected_photos=photos;owner._sync_selected_photos();raise
                finally:owner._auto_preview_ready=ready
                owner._preview_session.clear();owner.show_workbench();owner._preview_edited()
                self.mark_saved('session');remember_work(path,'session')
            except Exception as exc:QtWidgets.QMessageBox.warning(owner.root.qt,text('无法打开工作','Cannot open work'),str(exc))
        if kind in ('material','film','print'):open_now()  # Each package owns its replacement guard.
        else:self.protect(open_now,pages=[owner.film_workspace])
