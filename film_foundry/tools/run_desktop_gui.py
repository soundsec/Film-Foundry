"""桌面工作台的页面。

导航、各组控件和预览显示在这里。配方状态、批处理和任务线程在
`run_darkroom_gui.DarkroomPanel`。两边都不另写一套形成公式。
旧纵向布局不再由这个入口构造。材料、药水和扫描编辑器仍是独立窗口。
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys
from threading import Thread

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import numpy as np
from film_foundry.tools import run_darkroom_gui as legacy
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets, ui
from film_foundry.ui.curve_editor import CurveEditor, text
from film_foundry.ui.i18n import tr, current_language, language_options, language_label
from film_foundry.ui.preferences import load_preferences, save_preferences
from film_foundry.ui.theme import current_theme, apply_theme
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.workflow_text import (
    material_polarity_label, seed_strategy_labels, seed_strategy_label,
    light_leak_template_labels, light_leak_template_label,
    imprint_style_labels, imprint_style_label, imprint_side_labels, imprint_side_label,
)


class DesktopPanel(legacy.DarkroomPanel):
    """New presentation over the same state, configuration and task lifecycle."""
    PREVIEW_EDGE = 900
    PREVIEW_DEBOUNCE_MS = 350

    def __init__(self):
        self._section_states={}
        self._task_serial=0
        self._close_after_process=False
        self._latest_output=None
        self._task_stage=None
        self._preview_state_kind="empty"
        self._auto_preview_ready = False
        self._preview_revision = 0
        self._active_preview_revision = -1
        self._preview_pending = False
        self.auto_preview = ui.BoolValue(value=load_preferences().get("auto_preview", True))
        from film_foundry.app.camera_profile import CameraProfileStore
        self._camera_profile = CameraProfileStore()
        super().__init__()
        self._preview_timer = QtCore.QTimer(self.root.qt)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._run_auto_preview)
        self._auto_preview_ready = True
        from film_foundry.ui.close_guard import CloseGuard
        self._close_guard=CloseGuard(self.root.qt,self._allow_window_close)
        self._sync_activity_ui()

    def _build(self):
        self.root.title("Film Foundry · "+text("工作台", "Workbench"))
        self.root.geometry("1380x900");self.root.minsize(1024, 680)
        self.expert_groups=[];self.pages=[];self.fields={};self.sections={}
        shell=ui.Frame(self.root, padding=12);shell.pack(fill="both", expand=True)
        shell.qt.setObjectName("workbench")
        top=ui.Frame(shell);top.pack(fill="x", pady=(0,8))
        ui.Label(top, text="FILM FOUNDRY", font=("Segoe UI",16,"bold")).pack(side="left", padx=(0,18))
        ui.Label(top, text=text("本次任务", "Operation")).pack(side="left", padx=6)
        self.mode_display=ui.TextValue(value=tr("mode."+self.pipeline_mode.get()))
        self.mode_keys={tr("mode."+key):key for key in ("full","develop","scan","expose","develop_latent")}
        mode=ui.Choice(top, textvariable=self.mode_display, values=tuple(self.mode_keys), state="readonly")
        self.mode_choice=mode
        mode.qt.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        mode.qt.setMinimumContentsLength(10)
        mode.pack(side="left", padx=6)
        mode.bind("<<ComboboxSelected>>", lambda _: self._select_mode())
        ui.Toggle(top, text=text("专家细调", "Expert controls"), variable=self.expert_mode, command=self._update_expert_visibility).pack(side="left", padx=12)
        self.language.set(language_label(current_language()))
        language=ui.Choice(top, textvariable=self.language, values=language_options(), state="readonly", width=12)
        language.qt.setMaximumWidth(110)
        language.set_tooltip(tr("help.ui_language"))
        language.pack(side="right");language.bind("<<ComboboxSelected>>",self._change_language)
        self.theme_display=ui.TextValue(value=tr("theme."+current_theme()))
        self.theme_keys={tr("theme."+key):key for key in ("dark","ivory")}
        self.theme_combo=ui.Choice(top,textvariable=self.theme_display,values=tuple(self.theme_keys),state="readonly")
        self.theme_combo.qt.setMinimumWidth(90)
        self.theme_combo.qt.setMaximumWidth(110)
        self.theme_combo.qt.setAccessibleName(tr("label.theme"))
        self.theme_combo.pack(side="right",padx=6)
        self.theme_combo.bind("<<ComboboxSelected>>",self._change_theme)
        self.source_summary=ui.Label(shell, text="",wraplength=1200);self.source_summary.pack(fill="x", pady=(0,8))
        for variable in (self.input_path,self.negative_path,self.output_path,self.scan_output_near_source):
            variable._bind_view(lambda _: self._refresh_source_summary(),self.source_summary)

        body=ui.Frame(shell);body.pack(fill="both", expand=True)
        layout=body._ensure_pack_layout()
        split=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, body.qt);layout.addWidget(split)
        self.workspace_splitter=split
        self.navigation=QtWidgets.QListWidget()
        self.navigation.setObjectName("workbenchNavigation")
        self.navigation.setMinimumWidth(172)
        self.navigation.setMaximumWidth(200)
        self.navigation.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
        split.addWidget(self.navigation)
        self.stack=QtWidgets.QStackedWidget();self.stack.setMinimumWidth(420);split.addWidget(self.stack)
        self.page_index={};self.page_notes={}
        for key,zh,en in (
            ("output","任务与输出","Task & output"),
            ("film","胶卷与拍摄","Film & capture"),
            ("develop","冲洗","Development"),
            ("scan","观察与调色","Observe & color"),
            ("compute","计算设置","Computation"),
            ("resources","独立配置","Resource editors"),
        ):
            self.page_index[key]=self.navigation.count()
            self.navigation.addItem(text(zh,en))
            page=ui.Frame(body, padding=8);page.qt.show()
            title=ui.Label(page,text=text(zh,en),font=("Segoe UI",15,"bold"));title.qt.setObjectName("pageTitle");title.pack(fill="x",pady=(0,3))
            note=ui.Label(page,text="",wraplength=390);note.qt.setObjectName("sectionNote");note.pack(fill="x",pady=(0,8));self.page_notes[key]=note
            scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page.qt)
            self.stack.addWidget(scroll);self.pages.append(page)
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        preview=QtWidgets.QWidget();preview_layout=QtWidgets.QVBoxLayout(preview);preview_layout.setContentsMargins(12,0,0,0)
        self.preview_caption=QtWidgets.QLabel(text("预览 · 正式处理前可检查结果", "Preview · inspect before processing"));self.preview_caption.setWordWrap(True);preview_layout.addWidget(self.preview_caption)
        self.preview_tabs=QtWidgets.QTabWidget();self.input_view=ImageView();self.result_view=ImageView()
        self.preview_tabs.addTab(self.result_view,text("结果", "Result"));self.preview_tabs.addTab(self.input_view,text("来源 / 介质", "Source / medium"));preview_layout.addWidget(self.preview_tabs,1)
        self.inspector_view=ImageView();self.preview_tabs.addTab(self.inspector_view,text("阶段检查", "Stages"));self.preview_tabs.setTabVisible(2,False)
        tools=QtWidgets.QHBoxLayout();preview_layout.addLayout(tools)
        self.preview_zoom_label=QtWidgets.QLabel("—");self.preview_zoom_label.setMinimumWidth(48)
        for label,zh,en,callback in (
            ("−","缩小","Zoom out",lambda view:view.zoom(1/1.2)),
            (text("适合窗口","Fit"),"适合窗口","Fit to window",lambda view:view.fit()),
            ("100%","预览图实际像素","Actual preview pixels",lambda view:view.actual_size()),
            ("+","放大","Zoom in",lambda view:view.zoom(1.2)),
        ):
            button=QtWidgets.QPushButton(label);button.setToolTip(text(zh,en));button.setAccessibleName(text(zh,en))
            button.clicked.connect(lambda _=False,action=callback:action(self.preview_tabs.currentWidget()))
            tools.addWidget(button)
        tools.addWidget(self.preview_zoom_label);tools.addStretch(1)
        for view in (self.input_view,self.result_view,self.inspector_view):view.zoom_changed.connect(self._preview_zoom_changed)
        self.preview_tabs.currentChanged.connect(lambda _:self._preview_zoom_changed())
        note=QtWidgets.QLabel(text("滚轮缩放 · 拖动平移 · 双击适合窗口。预览尺寸小于正式输出。", "Scroll to zoom · drag to pan · double-click to fit. Preview dimensions are smaller than the final output."));note.setObjectName("sectionNote");note.setWordWrap(True);preview_layout.addWidget(note)
        self.preview_state_label=QtWidgets.QLabel(text("尚未生成预览","No preview yet"));self.preview_state_label.setObjectName("sectionNote");self.preview_state_label.setWordWrap(True);preview_layout.addWidget(self.preview_state_label)
        self._set_preview_state(self._preview_state_kind)
        self.preview_actions=ui.Frame(shell)
        preview_layout.addWidget(self.preview_actions.qt);self.preview_actions.qt.show()
        auto=ui.Toggle(self.preview_actions,text=text("自动刷新", "Auto-refresh"),variable=self.auto_preview,command=self._change_auto_preview)
        auto.pack(side="left")
        split.addWidget(preview);split.setChildrenCollapsible(False)
        split.setStretchFactor(0,0);split.setStretchFactor(1,0);split.setStretchFactor(2,1)
        split.setSizes([180,470,710])
        self._build_output(self.pages[self.page_index["output"]]);self._build_film(self.pages[self.page_index["film"]])
        self._build_develop(self.pages[self.page_index["develop"]]);self._build_scan(self.pages[self.page_index["scan"]])
        self._build_compute(self.pages[self.page_index["compute"]]);self._build_resources(self.pages[self.page_index["resources"]])
        self._update_material_filter_summary()
        for key,names in {
            "film.ageing":("material_degradation",),
            "film.gate":("exposure_occlusion_dust","exposure_occlusion_hair","exposure_occlusion_insect","exposure_occlusion_debris"),
            "film.leak":("light_leak",),
            "develop.accidents":("chemical_stain","uneven_development","reticulation_strength","dye_damage_strength"),
            "output.exports":("save_sidecar","debug_output","comparison_grid","save_scanner_raw","include_clear_base_border","export_transparent_plate","export_plate_set","export_layer_pack"),
        }.items():self._bind_section_count(key,names)
        for page in self.pages:
            page._ensure_pack_layout().addStretch(1)
        activity=ui.Frame(shell);activity.pack(fill="x",pady=(6,0))
        self.activity_label=ui.Label(activity,text=text("就绪","Ready"),wraplength=900);self.activity_label.qt.setObjectName("sectionNote");self.activity_label.pack(side="left",fill="x",expand=True)
        self.activity_progress=QtWidgets.QProgressBar();self.activity_progress.setFixedWidth(128);self.activity_progress.setFixedHeight(14);self.activity_progress.setTextVisible(False)
        activity._ensure_pack_layout().addWidget(self.activity_progress)
        self.open_output_button=ui.Button(activity,text=text("最近结果…","Recent output…"),command=self._open_latest_output);self.open_output_button.pack(side="right")
        bottom=ui.Frame(shell);bottom.pack(fill="x", pady=(8,0))
        self.preview_button=ui.Button(self.preview_actions,text=text("更新预览","Update"),command=self._start_preview);self.preview_button.pack(side="left",padx=6)
        self.preview_button.set_tooltip(tr("button.preview"))
        self.clear_preview_cache_button=ui.Button(self.preview_actions,text=text("清缓存","Clear cache"),command=self._clear_preview_cache);self.clear_preview_cache_button.pack(side="left")
        self.clear_preview_cache_button.set_tooltip(tr("button.clear_preview_cache"))
        resume=ui.Button(bottom,text=text("恢复批次…","Resume batch…"),command=self._start_resume);resume.pack(side="left",padx=6);resume.set_tooltip(tr("button.resume_batch"))
        self.resume_button=resume
        for button in (self.preview_button,self.clear_preview_cache_button,resume):button.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,QtWidgets.QSizePolicy.Policy.Fixed)
        bottom._ensure_pack_layout().addStretch(1)
        self.process_button=ui.Button(bottom,text=tr("button.process"),command=self._start_process)
        self.process_button.qt.setObjectName("primaryAction")
        self.process_button.pack(side="right")
        self.process_button.qt.setMinimumWidth(160);self.process_button.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,QtWidgets.QSizePolicy.Policy.Fixed)
        footer=ui.Frame(shell);footer.pack(fill="x",pady=(6,0))
        ui.Label(footer,textvariable=self.status,wraplength=1180).pack(side="left",fill="x",expand=True)
        from film_foundry.ui.about import show_about
        self.about_button=ui.Button(footer,text=text("关于","About"),command=lambda:show_about(self.root.qt))
        self.about_button.qt.setObjectName("aboutLink")
        self.about_button.pack(side="right")
        self.about_button.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,QtWidgets.QSizePolicy.Policy.Fixed)
        if self._preview_running:
            self.preview_button.state(["disabled"]);self.clear_preview_cache_button.state(["disabled"])
        if self._processing:
            self._set_process_button_mode("cancelling" if self._cancel_flag and self._cancel_flag.requested() else "cancel")
        self._update_expert_visibility()
        self._bind_auto_preview(shell)
        self._sync_activity_ui()

    def _bind_auto_preview(self, owner):
        names = set(self.fields) | {
            "input_path", "negative_path", "pipeline_mode", "film_preset",
            "develop_preset", "scanner_preset", "frame_size", "developer_type",
            "fixer_type", "scan_remove_base", "scan_invert", "seed_strategy",
            "random_seed", "light_leak_template_display", "imprint_style_display",
            "imprint_side_display", "imprint_text", "imprint_time",
            "spatial_reference_mm", "frame_long_edge_mm",
        }
        # Export-only controls have no bearing on an interactive sample.
        names -= {"run_as_preview", "preview_long_edge", "fast_mode", "debug_output",
                  "comparison_grid", "save_sidecar", "save_scanner_raw",
                  "export_transparent_plate", "export_plate_set", "export_layer_pack"}
        for name in names:
            variable=getattr(self,name)
            last=[variable.get()]
            def changed(value, last=last, name=name):
                if value != last[0]:
                    last[0]=value
                    frozen_source=self.pipeline_mode.get() in ("scan","develop_latent")
                    if name == "input_path" and frozen_source or name == "negative_path" and not frozen_source:
                        return
                    self._preview_edited()
            variable._bind_view(changed,owner)
        for editor in self.curve_editors.values():
            editor.changed.connect(self._preview_edited)

    def _preview_edited(self):
        if not self._auto_preview_ready or not self.root.winfo_exists():
            return
        # Even in manual mode an edited recipe invalidates an in-flight frame.
        self._preview_revision += 1
        if self.auto_preview.get():
            self._preview_pending = True
            self._preview_timer.start(self.PREVIEW_DEBOUNCE_MS)
        self._set_preview_state("stale")

    def _change_auto_preview(self):
        if self.auto_preview.get():
            self._preview_edited()
        else:
            self._preview_timer.stop()
            self._preview_pending=False
        self._save_display_preferences(auto_preview=bool(self.auto_preview.get()))
        self._set_preview_state(self._preview_state_kind)

    def _run_auto_preview(self):
        if self._preview_pending and self.auto_preview.get() and not self._processing and not self._preview_running:
            self._start_preview(automatic=True)

    def _fast_preview_config(self):
        config=self._freeze_incident_config(self._config())
        config.debug_output=False;config.comparison_grid=False;config.save_sidecar=False
        config.fast_mode=True
        config.processing.execution_mode="reduced_fast"
        config.processing.quality_mode="draft"
        config.output.render_long_edge=self.PREVIEW_EDGE
        config.output.preview_long_edge=self.PREVIEW_EDGE
        # Only this private preview recipe stabilizes random grain. The
        # selected formal random/fixed/path strategy remains untouched.
        if config.seed_strategy == "random":
            config.seed_strategy="path"
        return config

    def _start_preview(self, automatic=False):
        if self._processing or self._preview_running:
            if self.auto_preview.get():self._preview_pending=True
            return
        if not self._required_paths(preview=True,quiet=automatic):return
        self._preview_timer.stop();self._preview_pending=False
        try:
            mode=self.pipeline_mode.get();config=self._fast_preview_config()
            if mode in ("full","develop","expose"):
                path=self._first_input_image()
            elif mode == "develop_latent":
                paths=legacy.iter_latent_files(Path(self.negative_path.get()))
                path=paths[0] if paths else None
            else:
                path=self._first_negative_file()
            if path is None:
                self._set_preview_state("unavailable")
                if not automatic:self.status.set(tr("status.no_input" if mode in ("full","develop","expose") else "status.no_negative"))
                return
        except Exception as exc:
            self._set_preview_state("failed")
            self.status.set(text("预览失败：", "Preview failed: ")+str(exc));return
        self._preview_generation+=1
        self._active_preview_revision=self._preview_revision
        request=legacy.PreviewRequest(self._preview_generation,mode,config,path,self.PREVIEW_EDGE,True)
        self._preview_running=True
        self.preview_button.state(["disabled"]);self.clear_preview_cache_button.state(["disabled"])
        self.status.set(tr("status.previewing"))
        self._set_preview_state("running")
        Thread(target=self._render_preview,args=(request,),daemon=True).start()

    def _post_preview(self, token, callback):
        def apply():
            if (self.root.winfo_exists() and token == self._preview_generation
                    and self._active_preview_revision == self._preview_revision and not self._processing):
                callback()
        self.root.after(0,apply)

    def _finish_preview(self, token):
        if not self.root.winfo_exists() or token!=self._preview_generation:return
        super()._finish_preview(token)
        self._sync_activity_ui()
        if self._active_preview_revision!=self._preview_revision:
            self._set_preview_state("stale")
            if not self._processing and not self.auto_preview.get():
                self.status.set(text("参数已修改，请更新预览。","Settings changed; update the preview."))
        elif self._preview_state_kind=="running":
            self._set_preview_state("failed")
        if not self._preview_timer.isActive():self._run_auto_preview()

    def _start_process(self):
        if self._processing:
            super()._start_process();return
        if not self._required_paths():return
        self._task_serial+=1;self._task_stage=None
        self._preview_timer.stop()
        self._preview_pending=False
        self._preview_revision+=1
        super()._start_process()
        self._sync_activity_ui()

    def _finish_process(self):
        super()._finish_process()
        self._sync_activity_ui()
        if self._close_after_process:
            self._preview_pending=False;self._preview_timer.stop();self.root.after(0,self.root.destroy);return
        if self._preview_pending and self.auto_preview.get():
            self._preview_timer.start(self.PREVIEW_DEBOUNCE_MS)

    def _set_generated_medium_root(self,value):
        # A successful job points the medium picker at its new output folder.
        # This bookkeeping is not a user edit and must not re-preview a
        # consumed latent or overwrite the completion status.
        ready=self._auto_preview_ready;self._auto_preview_ready=False
        try:super()._set_generated_medium_root(value)
        finally:self._auto_preview_ready=ready

    def _start_resume(self):
        was_processing=self._processing
        if not was_processing:self._task_serial+=1;self._task_stage=None
        super()._start_resume()
        if self._processing and not was_processing:
            self._preview_timer.stop();self._preview_pending=False;self._preview_revision+=1
        self._sync_activity_ui()

    def _required_paths(self, *, preview=False, quiet=False):
        mode=self.pipeline_mode.get()
        source=self.negative_path if mode in ("scan","develop_latent") else self.input_path
        missing=not str(source.get()).strip()
        needs_output=not preview and not (mode=="scan" and self.scan_output_near_source.get())
        if not missing and (not needs_output or str(self.output_path.get()).strip()):return True
        if preview:self._preview_pending=False
        message=text("请先选择本次任务的来源。","Choose a source for this task first.") if missing else text("请选择输出文件夹。","Choose an output folder.")
        if not quiet:self.status.set(message)
        if preview:self._set_preview_state("unavailable")
        return False

    def _set_preview_state(self, kind):
        self._preview_state_kind=kind
        waiting=self.auto_preview.get() and self._preview_pending
        messages={
            "empty":text("尚未生成预览","No preview yet"),
            "stale":text("参数已修改 · 等待自动预览" if waiting else "参数已修改 · 请更新预览", "Settings changed · waiting for preview" if waiting else "Settings changed · update the preview"),
            "running":text("正在生成预览…","Updating preview…"),
            "ready":text("预览与当前参数一致","Preview matches the current settings"),
            "unavailable":text("请选择可预览的来源","Choose a source with previewable content"),
            "failed":text("预览未更新 · 请查看错误信息","Preview did not update · check the error message"),
        }
        self.preview_state_label.setText(messages[kind])

    def _sync_activity_ui(self):
        if not hasattr(self,"activity_label") or not self.root.winfo_exists():return
        busy=self._processing
        self.mode_choice.qt.setEnabled(not busy)
        self.resume_button.qt.setEnabled(not busy)
        self.image_source_row[0].qt.setEnabled(not busy);self.medium_source_row[0].qt.setEnabled(not busy)
        self.output_location_row[0].qt.setEnabled(not busy and not (self.pipeline_mode.get()=="scan" and self.scan_output_near_source.get()))
        self.source_relative_toggle.qt.setEnabled(not busy)
        for button in (self.preview_button,self.clear_preview_cache_button):button.qt.setEnabled(not busy and not self._preview_running)
        self.activity_progress.setVisible(busy)
        if busy:
            cancelling=self._cancel_flag is not None and self._cancel_flag.requested()
            label=text("正在取消，等待任务安全结束…","Cancelling; waiting for the task to finish safely…") if cancelling else (
                tr("mode."+self._task_stage.command)+" · "+tr("progress."+self._task_stage.stage) if self._task_stage is not None else text("任务进行中 · 使用启动时的配置","Task running · using the starting configuration"))
            self.activity_label.configure(text=label)
            self.activity_label.set_tooltip(text("调整参数将用于下一次任务。","Parameter edits apply to the next task."))
            fraction=self._task_stage.fraction if self._task_stage is not None else None
            self.activity_progress.setRange(0,0 if fraction is None else 1000)
            if fraction is not None:self.activity_progress.setValue(round(fraction*1000))
            self.activity_progress.setToolTip(text("当前阶段进度，不是整批总进度","Current stage progress, not the whole batch"))
        else:
            self.activity_label.configure(text=text("就绪","Ready"));self.activity_label.set_tooltip("")
        self.open_output_button.qt.setEnabled(self._latest_output is not None)
        self.open_output_button.set_tooltip(str(self._latest_output) if self._latest_output is not None else text("完成任务后可打开实际结果目录","Open the actual result folder after a task"))

    def _workflow_control(self, cancel_flag):
        if cancel_flag is None:return None
        from film_foundry.app.control import WorkflowControl
        serial=self._task_serial
        def report(progress):
            self.root.after(0,lambda:self._present_progress(serial,progress))
        return WorkflowControl(cancel_requested=cancel_flag.requested,on_progress=report)

    def _present_progress(self, serial, progress):
        if serial!=self._task_serial or not self._processing:return
        self._task_stage=progress;self._sync_activity_ui()
        if self._cancel_flag is None or not self._cancel_flag.requested():
            self.status.set(tr("mode."+progress.command)+" · "+tr("progress."+progress.stage))

    def _remember_output(self, path, serial):
        if serial==self._task_serial and Path(path).is_dir():
            self._latest_output=Path(path);self._sync_activity_ui()

    def _batch_status(self, operation, completed, failures, output_root):
        serial=self._task_serial
        self.root.after(0,lambda:self._remember_output(output_root,serial))
        return super()._batch_status(operation,completed,failures,output_root)

    def _resume_batch(self, plan, cancel_flag):
        serial=self._task_serial
        super()._resume_batch(plan,cancel_flag)
        self.root.after(0,lambda:self._remember_output(plan.output_root,serial))

    def _open_latest_output(self):
        if self._latest_output is None:return
        if not self._latest_output.is_dir():
            self.status.set(text("结果目录已移动或不存在。","The result folder has moved or is missing."));return
        if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self._latest_output.resolve()))):
            self.status.set(text("无法打开结果目录：","Unable to open the result folder: ")+str(self._latest_output))

    def _confirm_cancel_close(self):
        dialog=QtWidgets.QMessageBox(self.root.qt);dialog.setWindowTitle(text("任务正在运行","Task in progress"))
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setText(text("取消当前任务后退出？","Cancel the current task and exit?"))
        dialog.setInformativeText(text("已完成的输出会保留。窗口将在任务结束后关闭。","Completed outputs are retained. The window closes after the task has finished."))
        stop=dialog.addButton(text("取消任务后退出","Cancel task and exit"),QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        keep=dialog.addButton(text("继续处理","Keep processing"),QtWidgets.QMessageBox.ButtonRole.RejectRole);dialog.setDefaultButton(keep)
        dialog.exec();return dialog.clickedButton()==stop

    def _allow_window_close(self):
        if not self._processing:return True
        if self._close_after_process:return False
        if self._confirm_cancel_close():
            if not self._processing:return True
            self._close_after_process=True;self._request_cancel()
        return False

    def group(self, page, zh, en, expert=False, *, key=None, collapsed=False):
        if key is None:
            group=ui.Group(page,text=text(zh,en).replace("&","&&"),padding=10)
        else:
            from film_foundry.ui.disclosure import Disclosure
            group=Disclosure(page,text(zh,en),expanded=self._section_states.get(key,not collapsed),
                on_toggle=lambda value:self._section_states.__setitem__(key,value))
            self.sections[key]=group
        group.pack(fill="x",pady=(0,8));group.columnconfigure(1,weight=1);group.columnconfigure(2,weight=1)
        if expert:self.expert_groups.append(group)
        return group

    def choice(self, group, label, variable, values, row=0, callback=None):
        caption=ui.Label(group,text=label,wraplength=168);caption.qt.setMinimumWidth(136);caption.qt.setMaximumWidth(168);caption.grid(row=row,column=0,sticky="ew")
        choice=ui.Choice(group,textvariable=variable,values=values,state="readonly");choice.grid(row=row,column=1,columnspan=3,sticky="ew")
        choice.qt.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        choice.qt.setMinimumContentsLength(4)
        choice.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Fixed)
        if callback:choice.bind("<<ComboboxSelected>>",callback)
        return choice

    def preset_choice(self, group, label, variable, callback, row=0):
        choice=self.choice(group,label,variable,(),row,callback)
        choice.grid(row=row,column=1,columnspan=2,sticky="ew")
        variable._bind_view(lambda value:choice.qt.setToolTip(str(value)),choice)
        button=ui.Button(group,text=text("浏览…","Browse…"),command=lambda:self._browse_presets(choice,label))
        button.grid(row=row,column=3,sticky="e");button.set_tooltip(text("搜索并选择预设","Search and choose a preset"))
        return choice

    def _browse_presets(self, choice, title):
        from film_foundry.ui.preset_picker import PresetPicker
        combo=choice.qt
        labels=tuple(combo.itemText(index) for index in range(combo.count()))
        picker=PresetPicker(self.root.qt,title,labels,combo.currentIndex())
        if picker.exec()==QtWidgets.QDialog.DialogCode.Accepted and picker.selected_index is not None:
            combo.setCurrentIndex(picker.selected_index)
            combo.activated.emit(picker.selected_index)
        picker.deleteLater()

    def _preset_source_note(self, group, variable, row):
        note=ui.Frame(group);note.grid(row=row,column=0,columnspan=4,sticky="ew")
        ui.Label(note,text=text("来源：","Source: ")).pack(side="left")
        source=ui.Label(note,textvariable=variable,wraplength=360)
        source.qt.setObjectName("sectionNote");source.pack(side="left",fill="x",expand=True)
        source.set_tooltip(text("在上方预设列表或“浏览…”中选择。","Choose a preset from the list above or Browse…"))
        return source

    def _bind_section_count(self, key, names):
        section=self.sections[key]
        def update(_=None):
            count=sum(bool(getattr(self,name).get()) for name in names)
            section.set_summary(text(f"已选 {count} 项",f"{count} selected") if count else "")
        for name in names:getattr(self,name)._bind_view(update,section)

    def _editor_link(self, group, zh, en, module, row):
        button=ui.Button(group,text=text(zh,en),command=lambda:self._open_editor("film_foundry.tools."+module))
        button.qt.setObjectName("editorShortcut");button.grid(row=row,column=0,columnspan=4,sticky="ew",pady=(3,0))
        return button

    def _update_curve_summary(self):
        active=[label for key,label in (("tone_curve",text("影调","Tone")),("color_curve_rgb","RGB"),("device_response_rgb",text("设备","Device"))) if self.curve_editors[key].enabled.isChecked()]
        self.sections["scan.curves"].set_summary(" / ".join(active))

    def sliders(self, group, specs):
        short_labels={"exposure_ev":text("曝光补偿 EV","Exposure EV"),"print_exposure":text("成片曝光 EV","Output exposure EV"),
            "print_contrast":text("对比度","Contrast"),"scan_saturation":text("饱和度","Saturation"),"scan_strength":text("归一化强度","Normalization strength")}
        for row,(name,label,low,high,*precision) in enumerate(specs):
            caption=ui.Label(group,text=short_labels.get(label,tr("label."+label)),wraplength=168);caption.qt.setMinimumWidth(136);caption.qt.setMaximumWidth(168);caption.grid(row=row,column=0,sticky="ew",pady=4)
            variable=getattr(self,name)
            slider=ui.Slider(group,from_=low,to=high,variable=variable);slider.grid(row=row,column=1,columnspan=2,sticky="ew")
            entry=ui.NumberInput(group,from_=-1e9,to=1e9,increment=10**-(precision[0] if precision else 2),textvariable=variable)
            entry.qt.setFixedWidth(88);entry.grid(row=row,column=3,sticky="e")
            help_label="exposure_occlusion" if label.startswith("exposure_occlusion_") else "print_color_shift" if label.startswith("filter_") else "highlight_color_bias" if label.startswith("highlight_") else label
            for widget in (caption,slider,entry):legacy.attach_tooltip(widget,"help."+help_label)
            self.fields[name]=(slider,entry)

    def _path_row(self,parent,label,var,file_cmd,folder_cmd,row):
        frame=ui.Frame(parent);frame.grid(row=row,column=0,columnspan=4,sticky="ew",pady=4)
        frame.columnconfigure(0,weight=1)
        caption=ui.Label(frame,text=label,wraplength=360);caption.grid(row=0,column=0,columnspan=2,sticky="ew")
        entry=ui.TextInput(frame,textvariable=var);entry.grid(row=1,column=0,sticky="ew");entry.qt.setClearButtonEnabled(True)
        entry.qt.setAccessibleName(label)
        var._bind_view(lambda value:entry.qt.setToolTip(str(value)),entry)
        buttons=ui.Frame(frame);buttons.grid(row=1,column=1,sticky="e")
        for key,command in (("file",file_cmd),("folder",folder_cmd)):
            if command:ui.Button(buttons,text=tr("button."+key),command=command).pack(side="left",padx=2)
        return frame,caption,entry

    def toggles(self, group, pairs, start=0):
        for row,(name,label) in enumerate(pairs,start):
            self.fields[name]=self.wrapped_toggle(group,tr("label."+label),getattr(self,name),row)
            if name.startswith("exposure_occlusion_"):
                self.fields[name].set_tooltip(tr("help.exposure_occlusion"))

    def wrapped_toggle(self,group,label,variable,row,command=None):
        frame=ui.Frame(group);frame.grid(row=row,column=0,columnspan=4,sticky="ew",pady=3)
        toggle=ui.Toggle(frame,text="",variable=variable,command=command);toggle.pack(side="left")
        toggle.qt.setAccessibleName(label)
        caption=ui.Label(frame,text=label,wraplength=300);caption.pack(side="left",fill="x",expand=True)
        caption.qt.mousePressEvent=lambda event:toggle.qt.click() if event.button() == QtCore.Qt.MouseButton.LeftButton else None
        return frame

    def _build_film(self,page):
        self.film_source_group=self.group(page,"胶卷与画幅","Film & format")
        g=self.film_source_group
        self.material_color_filter=getattr(self,"material_color_filter","all")
        self.material_origin_filter=getattr(self,"material_origin_filter","all")
        color_choices=(("all",text("色彩不限","Any color")),("color",text("彩色","Color")),("monochrome",text("黑白","Black & white")))
        origin_choices=(("all",text("来源不限","Any source")),("official",text("内置","Built-in")),("custom",text("自己保存的","Saved by me")))
        self._color_filter_labels={label:key for key,label in color_choices}
        self._origin_filter_labels={label:key for key,label in origin_choices}
        self.material_color_display=ui.TextValue(value=dict(color_choices)[self.material_color_filter])
        self.material_origin_display=ui.TextValue(value=dict(origin_choices)[self.material_origin_filter])
        self.material_polarity_combo=self.choice(g,tr("label.material_category"),self.material_polarity_display,(material_polarity_label("negative"),material_polarity_label("positive")),callback=self._on_material_category_selected)
        self.film_preset_combo=self.preset_choice(g,tr("label.film_preset"),self.film_preset_display,self._on_film_preset_display_selected,1)
        self.choice(g,tr("label.frame_size"),self.frame_size,("half_frame","35mm","6x6","6x7","4x5"),2)
        self._preset_source_note(g,self.film_preset_source,3)
        self.material_editor_link=self._editor_link(g,"材料编辑器…","Material editor…","run_film_material_editor",4)
        filters=self.group(page,"筛选材料列表","Filter material list",key="film.filters",collapsed=True)
        self.choice(filters,text("色彩","Color"),self.material_color_display,tuple(label for _,label in color_choices),0,self._on_material_list_filter)
        self.choice(filters,text("来源","Source"),self.material_origin_display,tuple(label for _,label in origin_choices),1,self._on_material_list_filter)
        self.wrapped_toggle(filters,text("显示更多风格变体", "Show more style variants"),self.show_preset_variants,2,self._change_preset_visibility)
        self.capture_exposure_group=self.group(page,"单张／批次曝光","Single / batch exposure")
        self.sliders(self.capture_exposure_group,[("exposure_ev","exposure_ev",-5,5)])
        self.film_condition_group=self.group(page,"保存与过期","Storage & ageing",key="film.ageing",collapsed=True)
        self.sliders(self.film_condition_group,[("material_degradation","material_degradation",0,1)])
        self.film_gate_group=self.group(page,"片门脏物","Gate dirt",key="film.gate",collapsed=True)
        self.toggles(self.film_gate_group,[(key,key) for key in ("exposure_occlusion_dust","exposure_occlusion_hair","exposure_occlusion_insect","exposure_occlusion_debris")])
        ui.Label(self.film_gate_group,text=text("作用于曝光，与机身漏光独立保持。", "Applied during exposure; retained independently of the body leak."),wraplength=380).grid(row=4,column=0,columnspan=4,sticky="ew")
        ui.Button(self.film_gate_group,text=text("重设片门脏物身份（保留漏光）", "Reset gate dirt identity (keep the leak)"),command=lambda:self._replace_camera_identity(gate=True)).grid(row=5,column=0,columnspan=4,sticky="ew")
        self.film_effect_group=self.group(page,"机身漏光","Camera-body light leak",key="film.leak",collapsed=True)
        self.sliders(self.film_effect_group,[("light_leak","light_leak",0,1)])
        self.light_leak_template_display.set(light_leak_template_label(self.light_leak_template.get()))
        self.choice(self.film_effect_group,tr("label.light_leak_template"),self.light_leak_template_display,tuple(light_leak_template_labels().values()),1)
        ui.Label(self.film_effect_group,text=text("漏光在形成密度之前加进曝光。更换胶卷不会改这条机身身份。更细的组合仍在工艺编辑器。", "Light leak is extra exposure before density forms. Changing film does not replace this body identity. Finer combinations stay in the process editor."),wraplength=380).grid(row=2,column=0,columnspan=4,sticky="ew")
        ui.Button(self.film_effect_group,text=text("更换机身漏光身份（保留片门）", "Replace leak identity (keep the gate)"),command=lambda:self._replace_camera_identity(structure=True)).grid(row=3,column=0,columnspan=4,sticky="ew")
        self.film_imprint_group=self.group(page,"曝光打印","Exposure imprint",key="film.imprint",collapsed=True)
        g=self.film_imprint_group
        self.imprint_style_display.set(imprint_style_label(self.imprint_style.get()));self.imprint_side_display.set(imprint_side_label(self.imprint_side.get()))
        self.choice(g,tr("label.imprint_style"),self.imprint_style_display,tuple(imprint_style_labels().values()))
        self.choice(g,tr("label.imprint_side"),self.imprint_side_display,tuple(imprint_side_labels().values()),1)
        self._text_row(g,tr("label.imprint_text"),self.imprint_text,2);self._text_row(g,tr("label.imprint_time"),self.imprint_time,3)
        ui.Label(g,text=text("文字和时间都空着就不会印。这是曝光时的边字，不是扫描叠字。", "Empty text and time print nothing. This is an exposure imprint, not lettering added during the scan."),wraplength=380).grid(row=4,column=0,columnspan=4,sticky="ew")
        self.film_sprocket_group=self.group(page,"片边说明","Film border information",key="film.border",collapsed=True)
        ui.Label(self.film_sprocket_group,text=text("桌面成片目前不绘制齿孔，这里没有可调强度。任务与输出里的片基参考边只扩大扫描观察，不是齿孔。", "The desktop picture does not draw sprocket holes, so there is no strength control. The clear-base border under Task & output only extends the scan view."),wraplength=380).grid(row=0,column=0,columnspan=4,sticky="ew")
        ui.Button(self.film_sprocket_group,text=text("编辑片边点阵图案…", "Edit edge stencil…"),command=self._edit_edge_stencil).grid(row=1,column=0,columnspan=4,sticky="ew")
        ui.Label(page,text=text("光谱升维在独立配置里直接开启，不需要附带的 JSON。", "Spectral lift is switched on under Resource editors and does not need a bundled JSON file."),wraplength=400).pack(fill="x")

    def _edit_edge_stencil(self):
        from film_foundry.ui.edge_mark_editor import EdgeMarkEditor
        config = self._config()
        editor = EdgeMarkEditor(config.film_border, self.root.qt)
        if editor.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            try:
                editor.apply_to(config.film_border)
            except ValueError as exc:
                self.status.set(str(exc))
                return
            self._recipe_base = config

    def _build_develop(self,page):
        self.develop_source_group=self.group(page,"本次冲洗程序","Development process")
        self.develop_preset_combo=self.preset_choice(self.develop_source_group,tr("label.develop_preset"),self.develop_preset_display,self._on_develop_preset_display_selected)
        self._preset_source_note(self.develop_source_group,self.develop_preset_source,1)
        self.process_editor_link=self._editor_link(self.develop_source_group,"配置药浴与工艺步骤…","Configure baths & process steps…","run_develop_process_editor",2)
        self.wrapped_toggle(self.develop_source_group,text("显示更多药浴方案","Show more bath recipes"),self.show_preset_variants,3,self._change_preset_visibility)
        self.develop_effect_group=self.group(page,"冲洗操作","Process controls")
        self.sliders(self.develop_effect_group,[("develop_time","develop_time",0,60),("develop_temperature","temperature",4,45),("developer_concentration","concentration",.05,5),("agitation","agitation",0,5),("push","push",-4,6)])
        accidents=self.group(page,"冲洗事故","Process accidents",key="develop.accidents",collapsed=True)
        self.sliders(accidents,[("chemical_stain","chemical_stain",0,1),("uneven_development","uneven_development",0,1),("reticulation_strength","reticulation_strength",0,1),("dye_damage_strength","dye_damage_strength",0,1)])
        formation=self.group(page,"颗粒与高光扩散","Grain & halation",key="develop.formation",collapsed=True)
        self.sliders(formation,[("grain","grain",0,5),("halation","halation",0,5)])
        self.develop_additional_groups=[accidents,formation]
        identity=self.group(page,"单张／批次瑕疵身份","Single / batch defect identity",key="develop.identity",collapsed=True);self.develop_identity_group=identity
        ui.Label(identity,text=text("此处更换冲洗批次瑕疵；相机漏光与片门身份独立保持。颗粒沿用所选随机策略。已保存潜影和冲洗结果不会改变。", "Change process-batch defects here; camera leak and gate identities stay separate. Grain keeps its selected random strategy. Saved latents and results stay unchanged."),wraplength=380).pack(fill="x")
        ui.Button(identity,text=text("更换本次冲洗批次瑕疵", "New process-batch defects"),command=self._new_incident_identity).pack(fill="x")
        self.develop_detail_group=self.group(page,"冲洗与形成细调","Development & formation details",True,key="develop.details")
        g=self.develop_detail_group
        self.sliders(g,[("negative_contrast","negative_contrast",.3,3),("exhaustion","developer_exhaustion",0,1),("fixer_exhaustion","fixer_exhaustion",0,1),("silver_retention","silver_retention",0,1),("silver_plating","silver_plating",0,1),("compensation","compensation",0,1),("development_adjacency_strength","development_adjacency_strength",0,1),("development_adjacency_radius","development_adjacency_radius",.0002,.02,4),("process_variation","process_variation",0,1),("grain_size","grain_size",.1,4),("halation_sensitivity","halation_sensitivity",-1,1),("halation_edge","halation_edge",0,1),("emulsion_mtf","emulsion_mtf",0,1),("artifact_suppression","artifact_suppression",0,1)])
        self.choice(g,tr("label.developer"),self.developer_type,("standard","fine_grain","compensating","high_acutance","high_contrast","monobath","experimental_reversal"),14)
        self.choice(g,tr("label.fixer"),self.fixer_type,("standard","rapid","hardening","monobath","experimental_mask_bleach"),15)
        self.toggles(g,[("enable_mtf","enable_mtf"),("enable_halation","enable_halation"),("enable_grain","enable_grain"),("force_bw","bw_negative")],16)

    def _build_scan(self,page):
        g=self.group(page,"观察方式","Observation")
        self.scanner_preset_combo=self.preset_choice(g,tr("label.scanner_preset"),self.scanner_preset_display,self._on_scanner_preset_display_selected)
        self._preset_source_note(g,self.scanner_preset_source,1)
        ui.Label(g,textvariable=self.scan_recommendation,wraplength=390).grid(row=2,column=0,columnspan=4,sticky="ew")
        self.wrapped_toggle(g,text("显示更多风格变体", "Show more style variants"),self.show_preset_variants,3,self._change_preset_visibility)
        self.scanner_editor_link=self._editor_link(g,"完整扫描设置…","Full scanner settings…","run_scanner_render_editor",4)
        g=self.group(page,"基础调色","Basic adjustments")
        self.sliders(g,[("print_exposure","print_exposure",-5,5),("print_contrast","print_contrast",.3,3),("saturation","scan_saturation",0,3),("scan_strength","scan_strength",0,1)])
        self.toggles(g,[("scan_normalize","scan_normalize")],4)
        self.curve_editors={}
        curves_group=self.group(page,"影调与 RGB 曲线","Tone & RGB curves",key="scan.curves",collapsed=True)
        self.curve_tabs=QtWidgets.QTabWidget(curves_group.qt)
        self.curve_tabs.setMinimumHeight(300)
        curves_group._ensure_pack_layout().addWidget(self.curve_tabs)
        for key,zh,en in [("tone_curve","影调","Tone"),("color_curve_rgb","RGB","RGB"),("device_response_rgb","设备响应","Device")]:
            editor=CurveEditor(key,self.curve_tabs);self.curve_editors[key]=editor
            editor.load(getattr(self._recipe_base.scanner,key) if self._recipe_base else None)
            self.curve_tabs.addTab(editor,text(zh,en))
            editor.changed.connect(self._update_curve_summary)
        self._update_curve_summary()
        g=self.group(page,"底片解释与灯台","Film interpretation & light table",key="scan.interpretation",collapsed=True)
        for row,name in enumerate(("scan_remove_base","scan_invert")):
            self.wrapped_toggle(g,tr("label."+name),getattr(self,name),row,self._sync_manual_scan_controls)
        self.scan_pipeline_label=ui.Label(g,textvariable=self.scan_pipeline_description,wraplength=390)
        self.scan_pipeline_label.grid(row=2,column=0,columnspan=4,sticky="ew")
        lamp=ui.Frame(g);lamp.grid(row=3,column=0,columnspan=4,sticky="ew");lamp.columnconfigure(1,weight=1);lamp.columnconfigure(2,weight=1)
        self.sliders(lamp,[("light_table_ev","transmission_light_ev",-4,4),("light_table_temperature","transmission_light_temperature",2400,10000,0),("positive_color_control","positive_color_control",0,1)])
        g=self.group(page,"观察细调","Observation details",True,key="scan.details")
        self.sliders(g,[("print_shift_r","filter_r",-.5,.5),("print_shift_g","filter_g",-.5,.5),("print_shift_b","filter_b",-.5,.5),("highlight_green","highlight_green",.1,2),("highlight_blue","highlight_blue",.1,2),("projection_white_softness","projection_white_softness",0,1),("projection_black_adaptation","projection_black_adaptation",0,1),("negative_channel_compensation_strength","negative_channel_compensation_strength",0,1)])
        self.toggles(g,[("negative_channel_compensation","negative_channel_compensation")],8)

    def _build_output(self,page):
        g=self.group(page,"来源与输出位置","Sources & destinations")
        self.image_source_row=self._path_row(g,tr("label.input_image"),self.input_path,self._choose_input_file,self._choose_input_folder,0)
        self.medium_source_row=self._path_row(g,tr("label.negative_npz"),self.negative_path,self._choose_negative_file,self._choose_negative_folder,1)
        self.output_location_row=self._path_row(g,tr("label.output"),self.output_path,None,self._choose_output_folder,2)
        self.source_relative_toggle=self.wrapped_toggle(g,text("跟随介质来源确定保存位置","Use source-relative destination"),self.scan_output_near_source,3)
        self.destination_note=ui.Label(g,text="",wraplength=390);self.destination_note.qt.setObjectName("sectionNote");self.destination_note.grid(row=4,column=0,columnspan=4,sticky="ew",pady=3)
        g=self.group(page,"成片格式","Image format")
        self.choice(g,tr("label.output_format"),self.output_format,("jpg","png","tiff","webp"))
        g=self.group(page,"附属文件与诊断输出","Additional files & diagnostics",key="output.exports",collapsed=True)
        self.toggles(g,[("save_sidecar","sidecar"),("debug_output","debug"),("comparison_grid","grid"),("save_scanner_raw","save_scanner_raw"),("include_clear_base_border","include_clear_base_border")])
        for row,name,label in [(5,"export_transparent_plate","export_transparent_plate"),(6,"export_plate_set","export_plate_set"),(7,"export_layer_pack","export_layer_pack")]:
            w=self.wrapped_toggle(g,tr("label."+label),getattr(self,name),row,self._update_export_option_state)
            if name=="export_transparent_plate":self.transparent_export_check=w
            if name=="export_plate_set":self.plate_set_export_check=w
        g=self.group(page,"观察画布的片基参考边","Clear-base reference on the scan",key="output.border",collapsed=True)
        self.sliders(g,[("scanner_raw_border","scanner_raw_border",0,30)])
        ui.Label(g,text=text("这只在扫描观察时加一圈透明片基，不画齿孔，也不写回底片。", "This adds a clear-base margin during scan observation. It does not draw sprockets and does not write back to the film."),wraplength=380).grid(row=1,column=0,columnspan=4,sticky="ew")
        g=self.group(page,"输出修饰","Output finishing",key="output.finishing",collapsed=True)
        self.sliders(g,[("anti_banding","anti_banding",0,1)])
        self._update_export_option_state()
        g=self.group(page,"任务配置","Task configuration")
        for row,(key,callback) in enumerate([("save_session",self._save_session),("load_session",self._load_session),("import_recipe",self._import_recipe),("export_recipe",self._export_recipe)]):
            ui.Button(g,text=tr("button."+key),command=callback).grid(row=row//2,column=(row%2)*2,columnspan=2,sticky="ew",pady=3)
        ui.Button(g,text=text("输入分享码…","Enter share code…"),command=self._enter_share_code).grid(row=2,column=0,columnspan=4,sticky="ew",pady=3)
        g=self.group(page,"电影材料母带","Material movie masters",key="output.movie",collapsed=True)
        ui.Button(g,text=text("打开材料母带工具…","Open material master tools…"),command=self._open_movie_master).pack(fill="x")

    def _enter_share_code(self):
        value,accepted=QtWidgets.QInputDialog.getMultiLineText(self.root.qt,text("输入分享码","Enter share code"),"FFC2:")
        if not accepted or not value.strip():return
        try:
            from film_foundry.app.seasonal_collection import try_unlock
            if try_unlock(value):
                self._refresh_all_preset_combos()
                self.status.set(text("四季合集已解锁：8 款材料与专属扫描。","Seasons unlocked: 8 materials and their matching scans."))
            else:
                from film_foundry.app.workflow import config_from_share_code
                self._apply_loaded_config(config_from_share_code(value.strip()))
                self.status.set(text("分享码已应用","Share code applied"))
        except (ValueError,TypeError,OSError) as exc:
            QtWidgets.QMessageBox.warning(self.root.qt,text("分享码","Share code"),str(exc))

    def _on_film_preset_display_selected(self, _event=None):
        before=self.film_preset.get()
        super()._on_film_preset_display_selected(_event)
        selected=self.film_preset.get()
        if selected != before:
            from film_foundry.app.seasonal_collection import matching_scanner
            paired=matching_scanner(selected)
            if paired:
                self.scanner_preset.set(paired)

    def _open_movie_master(self):
        from film_foundry.ui.movie_master_dialog import MovieMasterDialog
        try: dialog=MovieMasterDialog(self._config(),self.root.qt)
        except (ValueError,TypeError) as exc:
            QtWidgets.QMessageBox.warning(self.root.qt,text("母带设置","Master settings"),str(exc));return
        dialog.exec()

    def _build_compute(self,page):
        g=self.group(page,"独立计算选项","Independent computation options")
        self.toggles(g,[("run_as_preview","preview_output"),("fast_mode","fast_mode")])
        self.choice(g,tr("label.quality"),self.quality_mode,("draft","standard","high"),2)
        g=self.group(page,"正式处理边长","Processing dimensions")
        self.sliders(g,[("preview_long_edge","preview_long_edge",0,12000,0)])
        ui.Label(g,text=text("缩放、降阶和采样档位独立组合。预览不改变正式设置。", "Scaling, reduction and sampling can be combined independently. Preview keeps export settings."),wraplength=380).grid(row=1,column=0,columnspan=4,sticky="ew")
        g=self.group(page,"随机性与身份","Randomness & identity",key="compute.random",collapsed=True)
        self.seed_strategy_display.set(seed_strategy_label(self.seed_strategy.get()))
        self.choice(g,tr("label.seed_strategy"),self.seed_strategy_display,tuple(seed_strategy_labels().values()),callback=self._on_seed_strategy_selected)
        ui.Label(g,text=tr("label.random_seed")).grid(row=1,column=0,sticky="w")
        self.random_seed_entry=ui.TextInput(g,textvariable=self.random_seed);self.random_seed_entry.grid(row=1,column=1,columnspan=3,sticky="ew");self._update_seed_strategy_state()
        ui.Button(page,text=tr("button.runtime_settings"),command=self._open_runtime_settings).pack(fill="x",pady=6)

    def _build_resources(self,page):
        g=self.group(page,"独立材料与药水配置","Independent material & chemistry editors")
        for row,(label,module) in enumerate([("film_editor","run_film_material_editor"),("develop_editor","run_develop_process_editor"),("scanner_editor","run_scanner_render_editor")]):
            ui.Button(g,text=tr("button."+label),command=lambda m=module:self._open_editor("film_foundry.tools."+m)).grid(row=row,column=0,columnspan=4,sticky="ew",pady=4)
        ui.Label(g,text=text("在独立窗口编辑工作副本，保存或显式应用到当前任务。冻结来源不可覆盖。", "Edit a working copy in its own window; save or explicitly apply to this task. Frozen sources cannot be overwritten."),wraplength=380).grid(row=3,column=0,columnspan=4,sticky="ew")
        g=self.group(page,"档案与物理画幅","Profiles & physical frame",key="resources.geometry",collapsed=True)
        self._text_row(g,tr("label.spatial_reference_mm"),self.spatial_reference_mm,0);self._text_row(g,tr("label.frame_long_edge_mm"),self.frame_long_edge_mm,1)
        self.resource_source_group=g
        self._build_exposure_spectrum(page)
        for key in ("spectral_material_profile","spectral_observation_profile"):
            h=self.group(page,tr("profile."+key),tr("profile."+key),key="resources."+key,collapsed=True)
            if key != "spectral_observation_profile":
                if not hasattr(self,"material_profile_groups"):self.material_profile_groups=[]
                self.material_profile_groups.append(h)
            ui.Label(h,text=text("没有附带这类档案。不载入时，计算仍用材料自己的数据。", "No file of this kind is included. Leaving it unused keeps the material's own data."),wraplength=380).grid(row=0,column=0,columnspan=4,sticky="ew")
            ui.Label(h,textvariable=self._profile_labels[key],wraplength=380).grid(row=1,column=0,columnspan=4,sticky="w")
            ui.Button(h,text=text("载入自己的档案","Load your own file"),command=lambda k=key:self._load_profile(k)).grid(row=2,column=0,columnspan=2,sticky="ew")
            ui.Button(h,text=text("不使用额外档案","Use no extra profile"),command=lambda k=key:self._set_profile(k,None)).grid(row=2,column=2,columnspan=2,sticky="ew")
        g=self.group(page,"保存与刷新预设","Save & refresh presets")
        for row,key,callback in [(0,"save_film_preset",self._save_film_preset),(1,"save_scanner_preset",self._save_scanner_preset),(2,"refresh_presets",self._refresh_all_preset_combos)]:
            ui.Button(g,text=tr("button."+key),command=callback).grid(row=row,column=0,columnspan=4,sticky="ew",pady=3)

    def _build_exposure_spectrum(self, page):
        group=self.group(page,"曝光光谱升维","Exposure spectral lift")
        self.exposure_spectrum_group=group
        ui.Label(group,text=text("没有附带光谱 JSON。不开启时，材料继续用自带感光。七色升维是内置算法，打开即可，不必先找文件。", "No spectrum JSON is included. Until you turn this on, the material keeps its own sensitivity. The seven-color lift is built in and does not need a file."),wraplength=380).grid(row=0,column=0,columnspan=4,sticky="ew")
        ui.Label(group,textvariable=self._profile_labels["spectral_exposure_profile"],wraplength=380).grid(row=1,column=0,columnspan=4,sticky="ew")
        ui.Button(group,text=text("使用材料自带感光","Use the material's own sensitivity"),command=self._use_material_sensitivity).grid(row=2,column=0,columnspan=4,sticky="ew",pady=(6,2))
        ui.Button(group,text=text("开启七色升维","Turn on seven-color lift"),command=self._enable_smits_exposure).grid(row=3,column=0,columnspan=4,sticky="ew",pady=2)
        ui.Button(group,text=text("载入自己的光谱","Load your own spectrum"),command=lambda:self._load_profile("spectral_exposure_profile")).grid(row=4,column=0,columnspan=4,sticky="ew",pady=2)
        self._profile_labels["spectral_exposure_profile"].set(self._exposure_spectrum_text(self._profiles["spectral_exposure_profile"]))

    def _exposure_spectrum_text(self, profile):
        if profile is None:
            return text("当前：材料自带感光。","Current: the material's own sensitivity.")
        if "smits" in str(getattr(profile,"model","")):
            return text("当前：七色升维已开启。","Current: seven-color lift is on.")
        return text("当前：已载入自己的光谱。","Current: using a spectrum you loaded.")

    def _smits_profile(self):
        from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
        from film_foundry.core.media import is_monochrome_material
        return make_smits_exposure_profile(1 if is_monochrome_material(self._config()) else 3)

    def _use_material_sensitivity(self):
        self._set_profile("spectral_exposure_profile", None)
        self.status.set(text("已回到材料自带感光。","Restored the material's own sensitivity."))

    def _enable_smits_exposure(self):
        self._set_profile("spectral_exposure_profile", self._smits_profile())
        self.status.set(text("已开启七色升维。更换胶卷后如要继续使用，再按一次。","Seven-color lift is on. Press it again after changing film if you want to keep it."))

    def _film_origin(self, key):
        return "custom" if (legacy.USER_FILM_PRESET_DIR / f"{key}.json").exists() else "official"

    def _narrow_film_records(self, records):
        color=getattr(self,"material_color_filter","all")
        origin=getattr(self,"material_origin_filter","all")
        if color != "all":
            records=[record for record in records if record.get("color") == color]
        if origin != "all":
            records=[record for record in records if self._film_origin(record["key"]) == origin]
        return records

    def _on_material_list_filter(self, _event=None):
        self.material_color_filter=self._color_filter_labels.get(self.material_color_display.get(),"all")
        self.material_origin_filter=self._origin_filter_labels.get(self.material_origin_display.get(),"all")
        self._set_material_ui_from_key(self.film_preset.get())
        self._update_material_filter_summary()

    def _update_material_filter_summary(self):
        labels=[]
        if self.material_color_filter!="all":labels.append(self.material_color_display.get())
        if self.material_origin_filter!="all":labels.append(self.material_origin_display.get())
        if self.show_preset_variants.get():labels.append(text("更多变体","More variants"))
        self.sections["film.filters"].set_summary(" / ".join(labels))

    def _select_mode(self):
        self.pipeline_mode.set(self.mode_keys[self.mode_display.get()]);self._update_mode_visibility()

    def _preview_zoom_changed(self, _percent=None):
        view=self.preview_tabs.currentWidget()
        self.preview_zoom_label.setText(f"{view.zoom_percent}%" if view is not None and view.source is not None else "—")

    def _set_process_button_mode(self, mode):
        super()._set_process_button_mode(mode)
        if mode not in ("cancel","cancelling") and self.process_button is not None and self.process_button.winfo_exists():
            labels={"full":text("开始完整处理","Develop & scan"),"develop":text("冲洗并保存介质","Develop medium"),
                "scan":text("扫描并保存成片","Scan to image"),"expose":text("保存曝光潜影","Expose latent"),"develop_latent":text("冲洗已保存潜影","Develop latent")}
            self.process_button.configure(text=labels[self.pipeline_mode.get()])
        if hasattr(self,"resume_button"):self._sync_activity_ui()

    def _change_preset_visibility(self):
        # Rebuild choices only: never re-select a recipe or reset edited values.
        self._set_material_ui_from_key(self.film_preset.get())
        self._refresh_scanner_catalog()
        self._set_develop_ui_from_key(self.develop_preset.get())
        self._update_material_filter_summary()
        try:
            save_preferences(show_preset_variants=bool(self.show_preset_variants.get()))
        except OSError:
            self.status.set(text("列表已切换，但偏好保存失败。", "Preset list updated, but the preference could not be saved."))

    def _new_incident_identity(self):
        from film_foundry.app.incident_batch import new_incident_seed
        self._incident_batch_seed=new_incident_seed()
        # An explicit user request replaces an inherited common identity too.
        if self._recipe_base is None:self._recipe_base=self._config()
        self._recipe_base.chemistry.incident_identity_seed=self._incident_batch_seed
        self._preview_edited()
        self.status.set(text("已更换冲洗批次身份；相机漏光与片门身份保持。", "Process-batch identity changed; camera leak and gate identities retained."))

    def _replace_camera_identity(self, *, structure=False, gate=False):
        try:
            identity=self._camera_profile.replace(structure=structure,gate=gate)
        except (OSError,ValueError) as error:
            self.status.set(text("相机设置未保存：", "Camera settings could not be saved: ")+str(error));return
        if self._recipe_base is None:self._recipe_base=self._config()
        if structure:self._recipe_base.chemistry.light_leak_identity_seed=identity.structure_seed
        if gate:self._recipe_base.chemistry.exposure_occlusion_identity_seed=identity.gate_seed
        self._preview_edited()
        self.status.set(text("已更新之后曝光使用的相机身份；原潜影和冲洗结果保持。", "Camera identity updated for future exposures; saved latents and results retained."))

    def _freeze_incident_config(self,config):
        prepared=super()._freeze_incident_config(config)
        if self.pipeline_mode.get() not in ("full","develop","expose"):return prepared
        chemistry=prepared.chemistry
        if chemistry.light_leak_identity_seed is not None and chemistry.exposure_occlusion_identity_seed is not None:return prepared
        from film_foundry.app.camera_profile import prepare_camera_config
        return prepare_camera_config(prepared,self._camera_profile.current())

    def _recipe_export_config(self):
        config=self._config()
        if self.pipeline_mode.get() not in ("full","develop","expose"):return config
        chemistry=config.chemistry
        if chemistry.light_leak_identity_seed is not None and chemistry.exposure_occlusion_identity_seed is not None:return config
        from film_foundry.app.camera_profile import prepare_camera_config
        return prepare_camera_config(config,self._camera_profile.current())

    def _update_expert_visibility(self):
        for group in getattr(self,"expert_groups",[]):
            group.qt.setVisible(bool(self.expert_mode.get()))
        if hasattr(self,"curve_tabs"):
            self.curve_tabs.setTabVisible(2,bool(self.expert_mode.get()))

    def _update_scan_parameter_visibility(self, expert=None):
        self.scan_recommendation.set(tr("scan.recommendation.negative" if self._expected_scan_polarity() == "negative" else "scan.recommendation.positive"))
        key="scan.pipeline.manual."+("remove_" if self.scan_remove_base.get() else "preserve_")+("invert" if self.scan_invert.get() else "direct")
        self.scan_pipeline_description.set(tr(key))

    def _update_mode_visibility(self):
        if not hasattr(self,"pages"):return
        mode=self.pipeline_mode.get();capture=mode in ("full","develop","expose");develop=mode in ("full","develop","develop_latent");scan=mode in ("full","scan")
        self.mode_display.set(tr("mode."+mode))
        for group in [self.film_source_group,self.sections["film.filters"],self.film_condition_group,self.capture_exposure_group,self.film_gate_group,self.film_effect_group,self.film_imprint_group,self.film_sprocket_group,self.resource_source_group,self.exposure_spectrum_group,*getattr(self,"material_profile_groups",[])]:group.qt.setEnabled(capture)
        for group in [self.develop_source_group,self.develop_effect_group,self.develop_detail_group,self.develop_identity_group,*self.develop_additional_groups]:group.qt.setEnabled(develop)
        for name in ("halation","halation_sensitivity","halation_edge","emulsion_mtf","artifact_suppression","enable_mtf","enable_halation"):
            widgets=self.fields[name]
            for widget in widgets if isinstance(widgets,tuple) else (widgets,):widget.qt.setEnabled(capture)
        self.pages[self.page_index["scan"]].qt.setEnabled(scan)
        self._refresh_source_summary()
        self._refresh_scanner_catalog();self._update_scan_parameter_visibility();self._update_expert_visibility()
        if not self._processing:self._set_process_button_mode("process")
        descriptions={
            "output":text("来源、保存位置与输出内容","Source, destination and saved files"),
            "film":text("材料、画幅与曝光条件" if capture else "沿用来源中已保存的材料与曝光条件", "Stock, format and exposure" if capture else "Stock and exposure are inherited from the saved source"),
            "develop":text("选择程序，调整药浴与材料反应" if develop else "本次只曝光，稍后可冲洗潜影" if mode=="expose" else "介质已经冲洗，本次只读取与观察", "Choose a process and adjust the bath" if develop else "Expose now, develop the latent later" if mode=="expose" else "The medium is already developed; this task only observes it"),
            "scan":text("只读观察介质，调整成片" if scan else "本次保存材料；可在扫描任务中继续成片", "Observe the medium and adjust the image" if scan else "This task saves material; choose Scan to make an image later"),
            "compute":text("正式处理的尺寸、质量与资源设置","Dimensions, quality and resources for formal processing"),
            "resources":text("编辑材料、药水与扫描配置","Edit material, chemistry and scanner definitions"),
        }
        for key,note in self.page_notes.items():note.configure(text=descriptions[key])
        if not self._processing:self.status.set(tr("status."+mode))

    def _refresh_source_summary(self):
        mode=self.pipeline_mode.get()
        frozen=mode in ("scan","develop_latent")
        source=self.negative_path.get() if frozen else self.input_path.get()
        name=Path(source).name if source else text("未选择", "Not selected")
        self.source_summary.configure(text=text("来源 · ", "Source · ")+name)
        self.source_summary.qt.setToolTip(str(source))
        if not hasattr(self,"image_source_row"):return
        self.image_source_row[0].qt.setVisible(not frozen);self.medium_source_row[0].qt.setVisible(frozen)
        self.medium_source_row[1].configure(text=text("已曝光潜影 · 文件或文件夹","Exposed latent · file or folder") if mode=="develop_latent" else text("冲洗后介质 / scanner raw · 文件或文件夹","Developed medium / scanner raw · file or folder"))
        self.source_relative_toggle.qt.setVisible(mode=="scan")
        relative=mode=="scan" and self.scan_output_near_source.get()
        self.output_location_row[0].qt.setEnabled(not relative and not self._processing)
        if relative:
            target=legacy.default_scan_output_root(Path(source)) if source else None
            note=text("成片保存到：", "Images will be saved in: ")+str(target) if target is not None else text("选择介质后显示保存位置", "Choose a medium to see the destination")
        else:
            if mode=="develop_latent":
                note=text("新建运行文件夹；正式冲洗成功后，潜影会标记为已处理。预览只读。", "Create a new run folder. Successful formal development marks the latent as processed; previews are read-only.")
            else:
                note=text("在此位置新建本次运行文件夹；原文件保留。", "Create a new run folder here; retain the source files.") if mode!="scan" else text("成片按本次时间命名，保留已有结果。", "Time-stamped scan names retain existing results.")
        self.destination_note.configure(text=note)

    def _config(self):
        config=super()._config()
        for key,editor in getattr(self,"curve_editors",{}).items():setattr(config.scanner,key,editor.read())
        return config

    def _apply_loaded_config(self,config):
        from film_foundry.app.camera_profile import preserve_legacy_camera_identity
        config=preserve_legacy_camera_identity(config)
        ready=self._auto_preview_ready;self._auto_preview_ready=False
        try:
            super()._apply_loaded_config(config)
            for key,editor in self.curve_editors.items():editor.load(getattr(config.scanner,key))
            self._update_curve_summary()
            self._update_mode_visibility()
        finally:self._auto_preview_ready=ready
        self._preview_edited()

    def _set_profile(self,key,profile):
        super()._set_profile(key,profile)
        if key == "spectral_exposure_profile":
            self._profile_labels[key].set(self._exposure_spectrum_text(profile))
        self._preview_edited()

    def _sync_scanner_preset_controls(self):
        super()._sync_scanner_preset_controls()
        path=legacy.scanner_preset_path(self.scanner_preset.get())
        if path.exists():
            scanner=legacy.DarkroomConfig.from_json(path).scanner
            for key,editor in self.curve_editors.items():editor.load(getattr(scanner,key))
            self._update_curve_summary()

    def _apply_editor_bank(self,bank,edited):
        mode=self.pipeline_mode.get()
        if bank == "film" and mode in ("scan","develop_latent") or bank == "develop" and mode in ("scan","expose"):
            self.status.set(text("此任务的来源已冻结；请保存配置供新任务使用。", "This task's source is frozen; save the configuration for a new task."));return
        super()._apply_editor_bank(bank,edited)

    def _change_language(self,event=None):
        ready=self._auto_preview_ready;self._auto_preview_ready=False
        # Save active and disabled point drafts before rebuilding native controls.
        drafts={key:(copy.deepcopy(editor.points),editor.enabled.isChecked(),editor.channel.currentIndex()) for key,editor in self.curve_editors.items()}
        images=(self.input_view.source,self.result_view.source,self.inspector_view.source,self.preview_caption.text())
        size=self.root.qt.size();split_sizes=self.workspace_splitter.sizes()
        scroll_positions=[self.stack.widget(index).verticalScrollBar().value() for index in range(self.stack.count())]
        page=self.navigation.currentRow();self.material_profile_groups=[]
        super()._change_language(event)
        for key,user_dir,builtin_dir in (("film",legacy.USER_FILM_PRESET_DIR,legacy.FILM_PRESET_DIR),("develop",legacy.USER_DEVELOP_PRESET_DIR,legacy.DEVELOP_PRESET_DIR),("scanner",legacy.USER_SCANNER_PRESET_DIR,legacy.SCANNER_PRESET_DIR)):
            getattr(self,key+"_preset_source").set(legacy.preset_source_text(getattr(self,key+"_preset").get(),user_dir,builtin_dir))
        for key,(points,enabled,channel) in drafts.items():
            editor=self.curve_editors[key];editor._loading=True;editor.points=points;editor.enabled.setChecked(enabled);editor.channel.setCurrentIndex(channel);editor._loading=False;editor.refresh()
        self._update_curve_summary()
        self.navigation.setCurrentRow(page)
        self.root.qt.resize(size)
        rebuilt_stack=self.stack
        def restore_layout():
            if self.stack is not rebuilt_stack:return
            self.workspace_splitter.setSizes(split_sizes)
            for index,position in enumerate(scroll_positions):
                self.stack.widget(index).verticalScrollBar().setValue(position)
        self.root.after(0,restore_layout)
        self.input_view.source,self.result_view.source=images[:2]
        self.input_view.fit();self.result_view.fit()
        if any(image is not None for image in images[:2]):self.preview_caption.setText(images[3])
        self.inspector_view.source=images[2];self.inspector_view.fit()
        self.preview_tabs.setTabVisible(2,images[2] is not None)
        self._save_display_preferences(language=current_language())
        self._auto_preview_ready=ready
        self._sync_activity_ui()
        if self._processing:
            if self._cancel_flag is not None and self._cancel_flag.requested():
                self.status.set(tr("status.cancelling"))
            elif self._task_stage is not None:
                self._present_progress(self._task_serial,self._task_stage)

    def _change_theme(self,event=None):
        apply_theme(self.theme_keys[self.theme_display.get()])
        self._save_display_preferences(theme=current_theme())

    def _save_display_preferences(self,**values):
        try:
            save_preferences(**values)
        except OSError as exc:
            self.status.set(f"{tr('status.ui_preferences_failed')}: {exc}")

    def _show_preview(self,source_name,left,right,left_title,right_title):
        self.inspector_view.source=None;self.preview_tabs.setTabVisible(2,False)
        self.input_view.show_array(left);self.result_view.show_array(right)
        self.preview_tabs.setTabText(0,right_title);self.preview_tabs.setTabText(1,left_title)
        self.preview_caption.setText(source_name);self.preview_tabs.setCurrentIndex(0)
        self.status.set(text("预览已更新：", "Preview updated: ")+source_name)
        self._set_preview_state("ready")

    def _show_stage_preview(self,source_name,left,result,inspector,left_title,result_title,inspector_title):
        self._show_preview(source_name,left,result,left_title,result_title)
        self.inspector_view.show_array(inspector)
        self.preview_tabs.setTabText(2,text("阶段检查", "Stages"))
        self.inspector_view.setToolTip(inspector_title)
        self.preview_tabs.setTabVisible(2,True)


def main():
    DesktopPanel().run()


if __name__ == "__main__":
    main()
