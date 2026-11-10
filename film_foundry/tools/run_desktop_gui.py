"""桌面工作台的页面。

导航、各组控件和预览显示在这里。配方状态、批处理和任务线程在
`run_darkroom_gui.DarkroomPanel`。两边都不另写一套形成公式。
图库、各材料工序、观察和配置编辑器由同一工作区承载。
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
from film_foundry.ui.comparison_view import ComparisonView
from film_foundry.ui.color_mixer import ColorMixerEditor
from film_foundry.ui.histogram import Histogram, display_bins
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
        self._workspace_layout=load_preferences().get("workspace_layout","compact")
        self._material_windows={}
        self._selected_photos=[]
        self._active_roll=None
        self._expert_operating_mode='expected'
        self._principle_chemistry=None
        self.effect_intent_enabled=ui.BoolValue(value=True)
        self.link_scanner=ui.BoolValue(value=True)
        self.auto_develop=ui.BoolValue(value=True)
        self._applying_recommendations=False
        self._legacy_effect_controls=False
        self._effect_intent_model='process_effect_intent_v3'
        self._result_index_errors=[]
        self._embedded_editor_keys=set()
        self._layout_sizes={}
        self._settings_dialog=None
        self._section_states={}
        self._task_serial=0
        self._close_after_process=False
        self._workspace_exit_pending=False
        self._workspace_io_busy=False
        self._latest_output=None
        self._task_stage=None
        self._preview_state_kind="empty"
        self._auto_preview_ready = False
        self._preview_revision = 0
        self._active_preview_revision = -1
        self._preview_pending = False
        self.auto_preview = ui.BoolValue(value=load_preferences().get("auto_preview", True))
        from film_foundry.ui.control_reset import ControlResets
        self.control_resets=ControlResets(self._preview_edited)
        from film_foundry.app.camera_profile import CameraProfileStore
        self._camera_profile = CameraProfileStore()
        super().__init__()
        self._recipe_base=self._config()
        self._recipe_base.film_border.imprint_font='seven_segment'
        self.control_resets.capture()
        for variable in (self.fast_mode, self.quality_mode):
            variable.trace_add('write', lambda *_: self.root.after(0, self._refresh_formation_status))
        self._refresh_formation_status()
        self._preview_timer = QtCore.QTimer(self.root.qt)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._run_auto_preview)
        self._auto_preview_ready = True
        for name in ('develop_time','develop_temperature','developer_concentration','agitation','push','chemical_stain','uneven_development','reticulation_strength','silver_plating','dye_damage_strength','silver_retention'):
            getattr(self,name).trace_add('write',lambda *_:self._mark_manual_development())
        from film_foundry.ui.material_workspace_io import MaterialWorkspaceFiles
        self._workspace_files=MaterialWorkspaceFiles(self)
        from film_foundry.ui.film_workspace_io import FilmWorkspaceFiles
        self._film_workspace_files=FilmWorkspaceFiles(self)
        from film_foundry.ui.close_guard import CloseGuard
        self._close_guard=CloseGuard(self.root.qt,self._allow_window_close)
        self._sync_activity_ui()

        self.edit_tools.initialize()
        for variable in (self.quality_mode,self.fast_mode,self.run_as_preview):
            variable.trace_add('write',lambda *_:self.edit_tools.schedule())
        from film_foundry.ui.workspace_sessions import WorkspaceSessions
        self.work_sessions=WorkspaceSessions(self)
        self.workspace_host.before_close=self._guard_page_close
        self._update_save_work_action()

    def _build(self):
        self._applied_layout=None
        self.root.title("Film Foundry · "+text("工作台", "Workbench"))
        self.root.geometry("1380x900");self.root.minsize(1024, 680)
        self.expert_groups=[];self.pages=[];self.fields={};self.sections={}
        self.control_resets.clear_widgets()
        shell=ui.Frame(self.root, padding=12);shell.pack(fill="both", expand=True)
        shell.qt.setObjectName("workbench")
        nav=QtWidgets.QHBoxLayout();shell._ensure_pack_layout().addLayout(nav)
        library_button=QtWidgets.QPushButton(text('返回图库','Library'));self.library_navigation_button=library_button;library_button.clicked.connect(self.open_library);nav.addWidget(library_button)
        from film_foundry.ui.workspace_host import WorkspaceHost
        self.workspace_host=WorkspaceHost(shell.qt);shell._ensure_pack_layout().addWidget(self.workspace_host,1)
        self.workspace_host.tabBar().hide()
        self.workspace_host.before_close=self._guard_page_close
        workspace_menu=QtWidgets.QToolButton();self.workspace_menu=workspace_menu;workspace_menu.setText(text('快捷导航','Navigate'))
        workspace_menu.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(workspace_menu)
        menu.addAction(text('图库','Library'),self.open_library)
        menu.addAction(text('当前胶片工作','Current film work'),self.show_workbench)
        opened=menu.addMenu(text('已打开的工作','Open work'))
        def list_opened():
            opened.clear()
            for page,(title,key,_) in self.workspace_host._pages.items():
                if key not in ('library','editors'):
                    opened.addAction(title,lambda p=page:self.workspace_host.setCurrentWidget(p))
            opened.addSeparator()
            close=opened.addAction(text('关闭当前工作','Close current work'),lambda:self.workspace_host.close_page(self.workspace_host.currentIndex()))
            current=self.workspace_host.currentWidget()
            close.setEnabled(bool(self.workspace_host._pages.get(current,('',None,False))[2]) and not current.property('navigationLocked'))
            if current.property('navigationLocked'):close.setToolTip(text('请先在图库侧栏解锁此工作。','Unlock this work in the library sidebar first.'))
        opened.aboutToShow.connect(list_opened)
        menu.addAction(text('整卷暗房','Roll darkroom'),self.open_roll_workspace)
        menu.addAction(text('胶片分段冲洗','Film reaction stages'),self.open_film_process_workspace)
        menu.addAction(text('印刷与制版','Print & plate studio'),self._open_print_workspace)
        menu.addSeparator()
        from film_foundry.ui.desktop_library import FAMILIES
        for family,zh,en in FAMILIES[1:]:
            menu.addAction(text(zh,en),lambda family=family:self._show_material_workspace(family))
        editors=menu.addMenu(text('配置编辑器','Configuration editors'))
        for bank,zh,en in (('film','胶片材料','Film material'),('develop','药水与工艺','Chemistry & process'),('scanner','扫描','Scanner'),('runtime','运行设置','Runtime settings')):
            editors.addAction(text(zh,en),lambda bank=bank:self.open_embedded_editor(bank))
        menu.addSeparator()
        menu.addAction(text('保存材料工作区…','Save material workspace…'),lambda:self._workspace_files.save())
        menu.addAction(text('材料工作区另存为…','Save material workspace as…'),lambda:self._workspace_files.save(save_as=True))
        menu.addAction(text('打开材料工作区…','Open material workspace…'),lambda:self._workspace_files.open())
        menu.setToolTipsVisible(True)
        for action in menu.actions()[-3:]:action.setToolTip(text('保存七类材料状态、曝光、参数与观看设置，并携带全部批量来源和待载入的光学来源。胶片、整卷另行存取。','Saves seven-family states, exposure pixels, drafts, viewing settings, all batch sources and pending optical sources. Film and rolls use separate archives.'))
        menu.addAction(text('恢复材料批次…','Resume material batch…'),self._resume_material_batch)
        menu.addSeparator()
        menu.addAction(text('保存胶片工作…','Save film work…'),lambda:self._film_workspace_files.save())
        menu.addAction(text('胶片工作另存为…','Save film work as…'),lambda:self._film_workspace_files.save(save_as=True))
        menu.addAction(text('打开胶片工作…','Open film work…'),lambda:self._film_workspace_files.open())
        for action in menu.actions()[-3:]:action.setToolTip(text('携带原始照片、潜影或扫描来源及完整配方，保留来源顺序和路径随机身份。重新执行所选流程；不保存冲洗中途状态。整卷使用电子胶卷。','Carries source photos, latents or scan sources and the complete recipe, source order and path seed identities. Re-runs the selected operation; not a mid-bath checkpoint. Rolls use electronic film rolls.'))
        workspace_menu.setMenu(menu);nav.addWidget(workspace_menu)
        config_button=QtWidgets.QToolButton();config_button.setText(text('材料与预设','Materials & presets'))
        config_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        config_menu=QtWidgets.QMenu(config_button)
        config_menu.addAction(text('预设管理…','Preset library…'),self.open_preset_library)
        config_menu.addAction(text('分享当前配置…','Share current configuration…'),self._share_current_presets)
        config_menu.addSeparator()
        for bank,zh,en in (('film','胶片材料','Film materials'),('develop','药水与流程','Chemistry & process'),('scanner','扫描预设','Scan presets')):
            config_menu.addAction(text(zh,en),lambda b=bank:self.open_embedded_editor(b))
        config_menu.addSeparator()
        for key,callback in (('import_recipe',self._import_recipe),('export_recipe',self._export_recipe)):
            config_menu.addAction(tr('button.'+key),callback)
        config_menu.addAction(text('输入配方分享码…','Enter recipe share code…'),self._enter_share_code)
        self.film_border_menu_action=config_menu.addAction(text('片边与齿孔…','Film edges & perforations…'),self._edit_film_border)
        self.film_border_menu_action.setToolTip(text('仅在新曝光中设置；已有潜影和底片的片边已冻结。','Configure new exposures only; existing latent and developed film rebates are frozen.'))
        tools_menu=menu.addMenu(text('其他工具','Other tools'))
        tools_menu.addAction(text('打开底片灯台…','Open film light table…'),self._open_medium_viewer)
        tools_menu.addAction(text('视频逐帧材料…','Video frame materials…'),self._open_movie_master)
        config_button.setMenu(config_menu);nav.addWidget(config_button)
        self.workspace_heading=QtWidgets.QLabel();nav.addWidget(self.workspace_heading,1)
        def current_workspace_changed(_):
            self.workspace_heading.setText(self.workspace_host.tabText(self.workspace_host.currentIndex()))
            self.library_navigation_button.setEnabled(self.workspace_host.currentWidget() is not getattr(self,'library_view',None))
            self._update_save_work_action()
        self.workspace_host.currentChanged.connect(current_workspace_changed)
        self.save_work_button=QtWidgets.QPushButton(text('保存工作','Save work'))
        self.save_work_button.setToolTip(text('保存当前工作，可从图库的最近工作重新打开。','Save the current work to reopen from the library.'))
        self.save_work_button.clicked.connect(lambda:self.work_sessions.save([self.workspace_host.currentWidget()]))
        nav.addWidget(self.save_work_button);self.save_work_button.hide()
        self.workspace_host.timer.timeout.connect(self._update_save_work_action)
        settings=QtWidgets.QPushButton(text('全局设置','Settings'));settings.clicked.connect(self.open_global_settings);nav.addWidget(settings)
        # Keep all film controls inside their page, so the document bar remains
        # available in every workflow without hiding or reparenting its toolbar.
        shell=ui.Frame(shell)
        self.film_workspace=shell.qt
        self.workspace_host.add_page(shell.qt,text('胶片工作','Film work'),key='film',closable=False)
        top=ui.Frame(shell);top.pack(fill="x", pady=(0,8))
        ui.Label(top, text=text("处理流程", "Workflow")).pack(side="left", padx=6)
        self.mode_display=ui.TextValue(value=tr("mode."+self.pipeline_mode.get()))
        self.workflow_labels={
            'full':text('照片 → 冲洗 → 扫描成片','Photo → develop → scan'),
            'develop':text('照片 → 冲洗底片','Photo → developed film'),
            'expose':text('照片 → 曝光潜影','Photo → exposed latent'),
            'develop_latent':text('潜影 → 冲洗底片','Latent → developed film'),
            'scan':text('图像 / 介质 → 仅扫描','Image / medium → scan only'),
        }
        self.mode_keys={label:key for key,label in self.workflow_labels.items()}
        mode=ui.Choice(top, textvariable=self.mode_display, values=tuple(self.mode_keys), state="readonly")
        self.mode_choice=mode
        mode.qt.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        mode.qt.setMinimumContentsLength(16)
        mode.pack(side="left", padx=6,fill='x',expand=True)
        mode.bind("<<ComboboxSelected>>", lambda _: self._select_mode())
        self.language.set(language_label(current_language()))
        language=ui.Choice(top, textvariable=self.language, values=language_options(), state="readonly", width=12)
        language.qt.setMaximumWidth(110)
        language.set_tooltip(tr("help.ui_language"))
        language.qt.hide();language.bind("<<ComboboxSelected>>",self._change_language)
        self.theme_display=ui.TextValue(value=tr("theme."+current_theme()))
        self.theme_keys={tr("theme."+key):key for key in ("dark","ivory")}
        self.theme_combo=ui.Choice(top,textvariable=self.theme_display,values=tuple(self.theme_keys),state="readonly")
        self.theme_combo.qt.setMinimumWidth(90)
        self.theme_combo.qt.setMaximumWidth(110)
        self.theme_combo.qt.setAccessibleName(tr("label.theme"))
        self.theme_combo.qt.hide()
        self.theme_combo.bind("<<ComboboxSelected>>",self._change_theme)
        source_bar=ui.Frame(shell);source_bar.pack(fill="x",pady=(0,8))
        self.source_summary=ui.Label(source_bar, text="",wraplength=950);self.source_summary.pack(side="left",fill="x",expand=True)
        library=ui.Button(source_bar,text=text("更换来源…","Change source…"),command=self.open_library);library.pack(side="right",padx=6)
        library.set_tooltip(text('回图库选择其他照片、潜影或已冲洗介质。当前工作保留，替换时沿用保存提示。','Choose another photo, latent or developed medium in the library. The current work is retained until replacement, with the normal save prompt.'))
        self.layout_button=QtWidgets.QToolButton(source_bar.qt)
        self.layout_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        layout_menu=QtWidgets.QMenu(self.layout_button)
        for mode,zh,en in (("compact","单侧布局","Compact layout"),("studio","双侧布局","Studio layout")):
            layout_menu.addAction(text(zh,en),lambda mode=mode:self.set_workspace_layout(mode))
        layout_menu.addSeparator()
        appearance=layout_menu.addMenu(text('主题','Theme'));language_menu=layout_menu.addMenu(text('语言','Language'))
        self._view_submenus=(appearance,language_menu)
        for label in self.theme_keys:
            def choose_theme(label=label):
                self.theme_display.set(label);self._change_theme(None)
            appearance.addAction(label,choose_theme)
        for label in language_options():
            def choose_language(label=label):
                self.language.set(label);self._change_language(None)
            language_menu.addAction(label,choose_language)
        self.layout_button.setToolTip(text('单侧 / 双侧布局、主题与语言','Compact / studio layout, theme and language'))
        self.layout_button.setMenu(layout_menu);source_bar._ensure_pack_layout("left").addWidget(self.layout_button)
        for variable in (self.input_path,self.negative_path,self.output_path,self.scan_output_near_source):
            variable._bind_view(lambda _: self._refresh_source_summary(),self.source_summary)

        self.library_view=None;self.editor_host=None;self.roll_view=None
        body=ui.Frame(shell);self.workbench_body=body;body.pack(fill='both',expand=True)
        layout=body._ensure_pack_layout()
        split=QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, body.qt);layout.addWidget(split)
        self.workspace_splitter=split
        self.navigation=QtWidgets.QListWidget()
        self.navigation.setObjectName("workbenchNavigation")
        self.navigation.setMinimumWidth(172)
        self.navigation.setMaximumWidth(200)
        self.navigation.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setTextElideMode(QtCore.Qt.TextElideMode.ElideNone)
        self.navigator_panel=QtWidgets.QWidget();nav_layout=QtWidgets.QVBoxLayout(self.navigator_panel);nav_layout.setContentsMargins(0,0,0,0)
        nav_layout.addWidget(self.navigation)
        self.selection_list=QtWidgets.QListWidget();self.selection_list.setToolTip(text('本次选中的原图；批量处理使用当前同一组参数','Selected originals; the batch uses the same current settings'))
        self.studio_tabs=QtWidgets.QTabWidget();self.studio_tabs.addTab(self.selection_list,text('来源','Sources'));nav_layout.addWidget(self.studio_tabs,1)
        self.studio_placeholders={};self.studio_moved={}
        self.studio_navigation=QtWidgets.QComboBox();nav_layout.insertWidget(0,self.studio_navigation)
        self.selection_list.itemClicked.connect(lambda item:self._focus_selected_photo(item.data(QtCore.Qt.ItemDataRole.UserRole)))
        split.addWidget(self.navigator_panel)
        self.control_panel=QtWidgets.QWidget();self.control_layout=QtWidgets.QVBoxLayout(self.control_panel);self.control_layout.setContentsMargins(0,0,0,0);self.control_layout.setSpacing(6)
        self.stack=QtWidgets.QStackedWidget();self.stack.setMinimumWidth(300);self.control_layout.addWidget(self.stack,1);split.addWidget(self.control_panel)
        self.page_index={};self.page_notes={}
        for key,zh,en in (
            ("output","输出","Output"),
            ("film","材料与曝光","Material & exposure"),
            ("develop","冲洗","Development"),
            ("scan","观察与调色","Observe & color"),
            ("compute","计算设置","Computation"),
            ("resources","材料与预设配置","Materials & presets"),
        ):
            self.page_index[key]=self.navigation.count()
            self.navigation.addItem(text(zh,en))
            self.studio_navigation.addItem(text(zh,en))
            page=ui.Frame(body, padding=8);page.qt.show()
            title=ui.Label(page,text=text(zh,en),font=("Segoe UI",15,"bold"));title.qt.setObjectName("pageTitle");title.pack(fill="x",pady=(0,3))
            self.page_notes[key]=title
            scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page.qt)
            self.stack.addWidget(scroll);self.pages.append(page)
        self.navigation.currentRowChanged.connect(self._navigate_workspace)
        self.studio_navigation.currentIndexChanged.connect(self.navigation.setCurrentRow)
        self.studio_tabs.currentChanged.connect(self._studio_page_changed)
        self.studio_tabs.tabBarClicked.connect(self._studio_page_changed)
        self.navigation.setCurrentRow(0)
        sections=QtWidgets.QToolButton();sections.setText(text('展开 / 收起参数','Expand / collapse controls'));sections.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        section_menu=QtWidgets.QMenu(sections)
        for expanded,zh,en in ((True,'展开当前页','Expand current page'),(False,'收起当前页','Collapse current page')):
            section_menu.addAction(text(zh,en),lambda e=expanded:self._expand_current_sections(e))
        sections.setMenu(section_menu);nav_layout.addWidget(sections)
        preview=QtWidgets.QWidget();preview_layout=QtWidgets.QVBoxLayout(preview);preview_layout.setContentsMargins(8,0,0,0);preview_layout.setSpacing(4)
        self.preview_layout=preview_layout
        self.preview_caption=QtWidgets.QLabel(text("预览", "Preview"));self.preview_caption.setWordWrap(True);preview_layout.addWidget(self.preview_caption)
        self.preview_tabs=QtWidgets.QTabWidget();self.input_view=ImageView();self.result_view=ComparisonView()
        self.preview_tabs.addTab(self.result_view,text("结果", "Result"));self.preview_tabs.addTab(self.input_view,text("来源 / 介质", "Source / medium"));preview_layout.addWidget(self.preview_tabs,1)
        self.inspector_view=ImageView();self.preview_tabs.addTab(self.inspector_view,text("阶段检查", "Stages"));self.preview_tabs.setTabVisible(2,False)
        self.preview_histogram=Histogram(compact=True);preview_layout.addWidget(self.preview_histogram)
        for view in (self.input_view,self.result_view,self.inspector_view):
            view.histogram_bins=None
            def accept_pixels(pixels, target=view):
                target.histogram_bins=display_bins(pixels)
                if target is self.preview_tabs.currentWidget():
                    self.preview_histogram.bins=target.histogram_bins;self.preview_histogram.update()
                if target is self.result_view:
                    for editor in getattr(self,'curve_editors',{}).values():editor.set_histogram(target.histogram_bins)
            view.pixels_changed.connect(accept_pixels)
        def update_histogram_tab(_):
            bins=self.preview_tabs.currentWidget().histogram_bins
            self.preview_histogram.bins=bins;self.preview_histogram.update()
        self.preview_tabs.currentChanged.connect(update_histogram_tab)
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
        histogram_toggle=QtWidgets.QPushButton(text("直方图", "Histogram"));histogram_toggle.setCheckable(True);histogram_toggle.setChecked(True)
        self.histogram_toggle=histogram_toggle
        histogram_toggle.toggled.connect(self.preview_histogram.setVisible);tools.addWidget(histogram_toggle)
        tools.addWidget(self.preview_zoom_label);tools.addStretch(1)
        for view in (self.input_view,self.result_view,self.inspector_view):view.zoom_changed.connect(self._preview_zoom_changed)
        self.preview_tabs.currentChanged.connect(lambda _:self._preview_zoom_changed())
        from film_foundry.ui.film_edit_tools import FilmEditTools
        edit_bar=QtWidgets.QHBoxLayout();preview_layout.addLayout(edit_bar)
        self.edit_tools=FilmEditTools(self,edit_bar)
        preview_help=text("滚轮缩放 · 拖动平移 · 双击适合窗口。预览尺寸小于正式输出。", "Scroll to zoom · drag to pan · double-click to fit. Preview dimensions are smaller than the final output.")
        self.preview_caption.setToolTip(preview_help)
        for view in (self.input_view,self.result_view,self.inspector_view):view.setToolTip(preview_help)
        self.preview_state_label=QtWidgets.QLabel(text("尚未生成预览","No preview yet"));self.preview_state_label.setObjectName("sectionNote")
        self.preview_state_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Preferred)
        self._set_preview_state(self._preview_state_kind)
        self.preview_actions=ui.Frame(shell)
        preview_layout.addWidget(self.preview_actions.qt);self.preview_actions.qt.show()
        auto=ui.Toggle(self.preview_actions,text=text("自动刷新", "Auto-refresh"),variable=self.auto_preview,command=self._change_auto_preview)
        auto.pack(side="left")
        self.preview_actions._ensure_pack_layout('left').addWidget(self.preview_state_label,1)
        split.addWidget(preview);split.setChildrenCollapsible(False)
        self.preview_surface=preview
        split.setStretchFactor(0,0);split.setStretchFactor(1,0);split.setStretchFactor(2,1)
        split.setSizes([180,470,710])
        self.set_workspace_layout(self._workspace_layout,persist=False)
        self._build_output(self.pages[self.page_index["output"]]);self._build_film(self.pages[self.page_index["film"]])
        self._build_develop(self.pages[self.page_index["develop"]]);self._build_scan(self.pages[self.page_index["scan"]])
        self._build_compute(self.pages[self.page_index["compute"]]);self._build_resources(self.pages[self.page_index["resources"]])
        self._update_material_filter_summary()
        for key,names in {
            "film.ageing":("material_degradation",),
            "film.gate":("exposure_occlusion_dust","exposure_occlusion_hair","exposure_occlusion_insect","exposure_occlusion_debris"),
            "film.leak":("light_leak",),
            "develop.accidents":("chemical_stain","uneven_development","reticulation_strength","dye_damage_strength"),
            "output.exports":("save_sidecar","debug_output","comparison_grid","save_scanner_raw","export_transparent_plate","export_plate_set","export_layer_pack"),
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
        expert=ui.Toggle(bottom,text=text('专家模式','Expert mode'),variable=self.expert_mode,command=self._update_expert_visibility);expert.pack(side='left',padx=12)
        expert.set_tooltip(text('F8 切换专家参数；当前草稿保持不变','F8 toggles expert controls without discarding settings'))
        shortcut=QtGui.QShortcut(QtGui.QKeySequence('F8'),shell.qt)
        shortcut.activated.connect(lambda:(self.expert_mode.set(not self.expert_mode.get()),self._update_expert_visibility()))
        self.expert_operation=QtWidgets.QComboBox()
        self.expert_operation.addItem(text('期望模式','Expected effects'),'expected');self.expert_operation.addItem(text('原理模式','Process principles'),'principle')
        self.expert_operation.setCurrentIndex(max(0,self.expert_operation.findData(self._expert_operating_mode)))
        self.expert_operation.setToolTip(text('期望模式指定效果；原理模式只使用药浴与操作条件。两者共用材料算子，切换保留各自草稿。','Expected mode sets effects; principle mode uses bath conditions. Both share material operators and keep separate drafts.'))
        self.expert_operation.currentIndexChanged.connect(self._set_expert_operation);bottom._ensure_pack_layout().addWidget(self.expert_operation)
        from film_foundry.ui.formation_execution import FormationExecutionBadge
        self.formation_badge = FormationExecutionBadge()
        self.formation_badge.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        formation_menu = QtWidgets.QMenu(self.formation_badge)
        formation_menu.addAction(text('完整形成', 'Full formation'), lambda: self.fast_mode.set(False))
        formation_menu.addAction(text('启用当前降阶方案', 'Enable current reduced profile'), lambda: self.fast_mode.set(True))
        formation_menu.addSeparator()
        formation_menu.addAction(text('计算与输出设置…', 'Compute and output settings…'), self._open_runtime_settings)
        self.formation_badge.setMenu(formation_menu)
        bottom._ensure_pack_layout().addWidget(self.formation_badge)
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
        self.workbench_chrome=[top.qt,source_bar.qt,activity.qt,bottom.qt,footer.qt]
        self._sync_selected_photos()
        if self._preview_running:
            self.preview_button.state(["disabled"]);self.clear_preview_cache_button.state(["disabled"])
        if self._processing:
            self._set_process_button_mode("cancelling" if self._cancel_flag and self._cancel_flag.requested() else "cancel")
        self._update_expert_visibility()
        self._bind_auto_preview(shell)
        self._sync_activity_ui()

    def _navigate_workspace(self,index):
        if index<0:return
        self.studio_navigation.blockSignals(True);self.studio_navigation.setCurrentIndex(index);self.studio_navigation.blockSignals(False)
        if index in self.studio_moved:
            self.studio_tabs.blockSignals(True);self.studio_tabs.setCurrentWidget(self.studio_moved[index]);self.studio_tabs.blockSignals(False)
            mode=self.pipeline_mode.get()
            self.stack.setCurrentIndex(self.page_index['scan' if mode in ('full','scan') else 'compute' if mode=='expose' else 'output'])
        else:self.stack.setCurrentIndex(index)

    def _studio_page_changed(self,index):
        if self._workspace_layout!='studio' or getattr(self,'_changing_workspace_layout',False):return
        page=self.studio_tabs.widget(index)
        target=next((key for key,value in self.studio_moved.items() if value is page),self.page_index['scan'])
        self.navigation.blockSignals(True);self.navigation.setCurrentRow(target);self.navigation.blockSignals(False)
        self._navigate_workspace(target)

    def set_workspace_layout(self, mode, *, persist=True):
        if mode not in ("compact","studio"):raise ValueError("Unknown workspace layout")
        previous=getattr(self,'_applied_layout',None)
        if previous==mode:return
        if previous:self._layout_sizes[previous]=self.workspace_splitter.sizes()
        selected=self.navigation.currentRow()
        self._changing_workspace_layout=True
        self._applied_layout=mode
        self._workspace_layout=mode
        split=self.workspace_splitter
        split.insertWidget(1,self.control_panel if mode=="compact" else self.preview_surface)
        self.preview_layout.removeWidget(self.preview_histogram)
        self.control_layout.insertWidget(0,self.preview_histogram)
        self.preview_histogram.setVisible(self.histogram_toggle.isChecked())
        split.setStretchFactor(1,0 if mode=="compact" else 1)
        split.setStretchFactor(2,1 if mode=="compact" else 0)
        split.setSizes([180,470,710] if mode=="compact" else [172,710,420])
        if mode=='studio':
            for key,zh,en in (('film','材料','Material'),('develop','冲洗','Develop')):
                index=self.page_index[key]
                if index not in self.studio_moved:
                    scroll=self.stack.widget(index);placeholder=QtWidgets.QWidget()
                    self.stack.removeWidget(scroll);self.stack.insertWidget(index,placeholder)
                    self.studio_placeholders[index]=placeholder;self.studio_moved[index]=scroll
                    self.studio_tabs.addTab(scroll,text(zh,en))
            if self.stack.currentIndex() in self.studio_moved:self.stack.setCurrentIndex(self.page_index['scan'])
        else:
            for index,scroll in sorted(self.studio_moved.items()):
                self.studio_tabs.removeTab(self.studio_tabs.indexOf(scroll))
                placeholder=self.studio_placeholders[index];self.stack.removeWidget(placeholder);placeholder.deleteLater()
                self.stack.insertWidget(index,scroll)
            self.studio_moved.clear();self.studio_placeholders.clear();self.stack.setCurrentIndex(self.navigation.currentRow())
        self.studio_tabs.setVisible(mode=='studio');self.studio_navigation.setVisible(mode=='studio');self.navigation.setVisible(mode!='studio')
        self.navigator_panel.setMinimumWidth(280 if mode=='studio' else 172)
        self.navigator_panel.setMaximumWidth(520 if mode=='studio' else 200)
        self.control_panel.setMaximumWidth(580)
        self.preview_surface.setMinimumWidth(260)
        split.setSizes(self._layout_sizes.get(mode,[180,380,760] if mode=='compact' else [300,700,320]))
        self._changing_workspace_layout=False
        self._navigate_workspace(selected)
        self.layout_button.setText(text("视图 · 单侧","View · Compact") if mode=="compact" else text("视图 · 双侧","View · Studio"))
        self.layout_button.setToolTip(text("切换面板位置；保留当前参数、图像和任务","Switch panel positions; retain settings, images and tasks"))
        for window in self._material_windows.values():
            if not window._closing:window.set_workspace_layout(mode)
        if persist:save_preferences(workspace_layout=mode)

    def show_workbench(self):
        self.workspace_host.setCurrentWidget(self.film_workspace)

    def open_library(self):
        from film_foundry.ui.desktop_library import DesktopLibrary
        try:
            if self.library_view is None:
                self.library_view=DesktopLibrary(self._open_catalog_source,self.open_embedded_editor,self.show_workbench,open_batch=self._open_catalog_batch,open_roll=self.open_roll_workspace,open_artifact=self._open_result_artifact,open_stack=self._open_result_stack,print_asset=self._print_catalog_asset,
                    open_browser=self._open_library_browser,open_settings=self.open_global_settings,open_work=lambda p,k:self.work_sessions.open_work(p,k),
                    live_work=lambda:[(v[0],p) for p,v in self.workspace_host._pages.items() if v[1] not in ('library','editors','browser')])
                self.workspace_host.add_page(self.library_view,text('图库','Library'),key='library',closable=False)
                self.workspace_host.tabBar().moveTab(self.workspace_host.indexOf(self.library_view),0)
            self.workspace_host.setCurrentWidget(self.library_view)
            self.library_view.return_to_photos()
            self.library_navigation_button.setEnabled(False)
        except Exception as exc:self.status.set(str(exc))

    def _open_library_browser(self,entries,identity):
        from film_foundry.ui.library_browser import LibraryBrowser
        old=self.workspace_host.find_page('browser')
        if old is not None:
            old.close();self.workspace_host.remove_page(old)
        page=LibraryBrowser(entries,identity,self.library_view.catalog,annotations_changed=self.library_view.refresh)
        self.workspace_host.add_page(page,text('图像浏览','Image browser'),key='browser')

    def open_global_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.raise_();self._settings_dialog.activateWindow();return
        from film_foundry.ui.desktop_settings_dialog import DesktopSettingsDialog
        dialog=DesktopSettingsDialog(self.root.qt,clear_cache=self._release_caches);self._settings_dialog=dialog
        def finished(result):
            self._settings_dialog=None
            if result and self.library_view:self.library_view.sidebar.refresh();self.library_view.sidebar.index_folders()
            dialog.deleteLater()
        dialog.finished.connect(finished);dialog.open()

    def _release_caches(self):
        from film_foundry.ui.image_cache import preview_images
        from film_foundry.model.config import _config_file_cache
        preview_images.clear();_config_file_cache.clear()
        from film_foundry.app.workflow import _preset_summary_cache
        _preset_summary_cache.clear()
        if self.library_view:
            self.library_view.cache.clear()
            for item in self.library_view.items.values():item.setIcon(QtGui.QIcon())
        if not self._preview_running:self._preview_session.clear()

    def _expand_current_sections(self,expanded):
        current=self.pages[self.navigation.currentRow()].qt
        for section in self.sections.values():
            if current.isAncestorOf(section.qt):section.set_expanded(expanded)

    def _guard_page_close(self,page):
        if not hasattr(self,'work_sessions') or self.work_sessions.bypass:return True
        if getattr(page,'busy',False):return True  # The page defers close until its worker completes.
        def close():
            if page not in self.workspace_host._pages:return
            self.work_sessions.bypass=True
            try:self.workspace_host.close_page(self.workspace_host.indexOf(page))
            finally:self.work_sessions.bypass=False
        self.work_sessions.protect(lambda:self.root.after(0,close),pages=[page]);return False

    def _apply_new_work_defaults(self):
        from film_foundry.app.desktop_settings import load_settings
        values=load_settings()
        self.fast_mode.set(values['fast_mode']);self.quality_mode.set(values['quality'])
        self.run_as_preview.set(values['resize']);self.preview_long_edge.set(str(values['long_edge']))
        self.expert_mode.set(values['expert']);self._expert_operating_mode=values['expert_mode']
        self.expert_operation.setCurrentIndex(self.expert_operation.findData(values['expert_mode']));self._update_expert_visibility()

    def _save_session(self):
        self.work_sessions.save([self.film_workspace])

    def _load_session(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self.root.qt,text('打开会话','Open session'),'','Film Foundry session (*.json)')
        if path:self.work_sessions.open_work(path,'session')

    def _mount_material_workspace(self,window,key,title=None):
        from film_foundry.ui.desktop_library import FAMILIES
        self._material_windows[key]=window
        if hasattr(self,'work_sessions'):
            from film_foundry.ui.close_guard import CloseGuard
            window._work_close_guard=CloseGuard(window,lambda:True if window.busy else self._guard_page_close(window))
            window.protect_replacement=lambda action:self.work_sessions.protect(action,pages=[window])
            self.work_sessions.material_saved[window]=self.work_sessions.material_snapshot(window)
        window.result_ready.connect(self._record_result)
        window.print_requested.connect(self._receive_print_source)
        window.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose,True)
        def forget(*_):
            if self._material_windows.get(key) is window:self._material_windows.pop(key,None)
        window.closed.connect(forget);window.destroyed.connect(forget)
        window.set_workspace_layout(self._workspace_layout)
        label=next(text(zh,en) for family,zh,en in FAMILIES if family==window.kind)
        self.workspace_host.add_page(window,title or label,key=key)
        return window

    def _open_print_workspace(self):
        from film_foundry.ui.printmaking_workspace import PrintmakingWorkspace
        page=self.workspace_host.find_page('print-studio')
        if page is None:
            page=PrintmakingWorkspace()
            self._attach_print_guard(page)
            page.result_ready.connect(self._record_result)
            self.workspace_host.add_page(page,text('印刷与制版','Print & plate studio'),key='print-studio')
        self.workspace_host.setCurrentWidget(page)
        return page

    def _attach_print_guard(self,page):
        from film_foundry.ui.close_guard import CloseGuard
        page.protect_replacement=lambda action:self.work_sessions.protect(action,pages=[page])
        page._work_close_guard=CloseGuard(page,lambda:self._guard_page_close(page))

    def _receive_print_source(self,payload):
        from film_foundry.ui.printmaking_workspace import PrintmakingWorkspace
        page=PrintmakingWorkspace()
        self._attach_print_guard(page)
        page.result_ready.connect(self._record_result)
        page.set_source(payload['image'],payload.get('provenance'),payload.get('components'))
        name=payload.get('provenance',{}).get('name') or text('材料','Material')
        self.workspace_host.add_page(page,text('制版 · ','Print · ')+name)
        return page

    def _print_catalog_asset(self,asset):
        from film_foundry.ui.printmaking_workspace import PrintmakingWorkspace
        page=PrintmakingWorkspace();page.result_ready.connect(self._record_result)
        self._attach_print_guard(page)
        self.workspace_host.add_page(page,text('制版 · ','Print · ')+Path(asset['path']).name)
        page.load_asset(asset,film_config=self._config() if asset.get('family')=='film' or asset['kind']=='scanner_raw' else None)

    def _show_material_workspace(self,family):
        from film_foundry.ui.material_lab_window import create_material_window
        window=self._material_windows.get(family)
        if window is None:window=self._mount_material_workspace(create_material_window(family),family)
        self.workspace_host.setCurrentWidget(window)
        return window

    def _open_catalog_source(self, path, family):
        if family=="film":
            if self._processing:raise ValueError(text("胶片任务仍在运行，请完成后切换来源","Wait for the current film task before replacing its source"))
            return self.work_sessions.protect(lambda:self._replace_film_sources([str(path)]),pages=[self.film_workspace])
        from film_foundry.ui.material_lab_window import create_material_window
        # A new selection creates a new work document; older material work stays reachable.
        key=family if family not in self._material_windows else 'source:'+family+':'+str(len(self._material_windows))
        window=self._mount_material_workspace(create_material_window(family),key)
        size=window.work_size.value()
        window._submit(lambda:window.session.open_image(path,size),text("读取图像…","Reading image…"),body_index=0)

    def _replace_film_sources(self,paths):
        self._active_roll=None;self._selected_photos=list(paths);self._sync_selected_photos()
        self.mode_choice.qt.setEnabled(True);self.pages[self.page_index['film']].qt.setEnabled(True)
        self._apply_new_work_defaults()
        self.input_path.set(paths[0]);self.pipeline_mode.set('full')
        self.link_scanner.set(True);self.auto_develop.set(True)
        self._apply_recommended_development();self._apply_matching_scanner()
        self._update_mode_visibility();self.navigation.setCurrentRow(self.page_index['film']);self.show_workbench()

    def _sync_selected_photos(self):
        self.selection_list.clear()
        for path in self._selected_photos:
            item=QtWidgets.QListWidgetItem(Path(path).name);item.setToolTip(path);item.setData(QtCore.Qt.ItemDataRole.UserRole,path);self.selection_list.addItem(item)

    def _focus_selected_photo(self,path):
        if not self._processing and path:
            self.input_path.set(path)
            if self.pipeline_mode.get()=='scan':self.negative_path.set(path)

    def _open_catalog_batch(self,paths,family):
        if family!='film':
            from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
            from film_foundry.app.material_lab_session import SHEET_FAMILIES
            from film_foundry.ui.sheet_lab_window import SheetBatchWindow
            dialog=SheetBatchWindow(family,paths) if family in SHEET_FAMILIES else MaterialBatchDialog(family,paths,self.root.qt)
            from film_foundry.ui.desktop_library import FAMILIES
            label=next(text(zh,en) for key,zh,en in FAMILIES if key==family)
            self._mount_material_workspace(dialog,'batch:'+str(id(dialog)),text('批量 · ','Batch · ')+label)
            return
        if self._processing:raise ValueError(text('请等待当前任务完成','Wait for the current task'))
        self.work_sessions.protect(lambda:self._replace_film_sources(paths),pages=[self.film_workspace])

    def _record_generated_result(self,record):
        self.root.after(0,lambda:self._record_result(record))

    def _record_result(self,record):
        from film_foundry.app.desktop_catalog import DesktopCatalog
        catalog=self.library_view.catalog if self.library_view else DesktopCatalog()
        try:catalog.register_result(record)
        except (OSError,ValueError) as exc:
            self._result_index_errors.append(str(exc));self.status.set(text('结果已保存，图库索引写入失败：','Results saved; catalog update failed: ')+str(exc));return
        if self.library_view:
            self.library_view.refresh()
        for stack in catalog.results:
            page=self.workspace_host.find_page('result:'+stack['id'])
            if page is not None:
                selected=page.selected_asset()
                page.load(dict(stack,focus_asset=selected['id'] if selected else None))

    def open_film_process_workspace(self):
        from film_foundry.ui.film_process_workspace import FilmProcessWorkspace
        page=self.workspace_host.find_page('film-process')
        if page is None:
            page=FilmProcessWorkspace(lambda:self._config())
            page.result_ready.connect(self._record_result)
            page.view_result.connect(lambda path:self._open_result_artifact(path,'film'))
            self.workspace_host.add_page(page,text('胶片分段冲洗','Film reaction stages'),key='film-process')
        self.workspace_host.setCurrentWidget(page)
        return page

    def _open_result_artifact(self,path,family,*,body_id=None,rework=False,stage=None,_approved=False):
        if family=='film' and (stage=='state' or path.endswith('.ffprocess.npz')):
            self.open_film_process_workspace().open_from(path);return
        if family=='film':
            if self._processing:raise ValueError(text('请等待当前任务完成','Wait for the current task'))
            if not _approved:
                return self.work_sessions.protect(lambda:self._open_result_artifact(path,family,body_id=body_id,rework=rework,stage=stage,_approved=True),pages=[self.film_workspace])
            self._active_roll=None;self._selected_photos=[];self._sync_selected_photos()
            latent=stage=='latent' or path.endswith('.darkroom_latent.npz')
            self.negative_path.set(path);self.pipeline_mode.set('develop_latent' if latent else 'scan');self._update_mode_visibility()
            self.mode_choice.qt.setEnabled(True);self.pages[self.page_index['film']].qt.setEnabled(True);self.show_workbench()
            self.navigation.setCurrentRow(self.page_index['develop' if latent else 'scan']);self._start_preview();return
        from film_foundry.ui.material_lab_window import create_material_window
        key='inspect:'+str(Path(path).resolve())
        window=self._material_windows.get(key)
        if window is not None:
            self.workspace_host.setCurrentWidget(window)
            if body_id:window.select_catalog_body(body_id,rework=rework)
            return
        window=self._mount_material_workspace(create_material_window(family),key,text('材料 · ','Material · ')+Path(path).name)
        window._catalog_body_selection=(body_id,rework)
        window.open_archive(path)

    def _open_catalog_asset(self,asset,action,target=None):
        path=asset['path']
        if not Path(path).is_file():raise ValueError(text('文件已移动或不存在','File moved or missing'))
        if action=='image':return self._open_catalog_source(path,target or 'film')
        if action=='reexpose':return self._send_result_to_material(path,target,body_id=asset.get('body_id'))
        return self._open_result_artifact(path,asset.get('family','film'),body_id=asset.get('body_id'),rework=action=='continue',stage=asset['kind'])

    def _open_result_stack(self,entry):
        from film_foundry.ui.desktop_library import ResultStackView
        key='result:'+entry['id'];page=self.workspace_host.find_page(key)
        if page is None:
            from film_foundry.app.desktop_catalog import DesktopCatalog
            catalog=self.library_view.catalog if self.library_view else DesktopCatalog()
            page=ResultStackView(entry,self._open_result_artifact,send_medium=self._send_result_to_material,
                catalog=catalog,open_asset=self._open_catalog_asset,print_asset=self._print_catalog_asset,
                annotations_changed=lambda:self.library_view.refresh() if self.library_view else None)
            self.workspace_host.add_page(page,text('结果 · ','Result · ')+entry['label'],key=key)
        else:
            page.load(entry);self.workspace_host.setCurrentWidget(page)

    def _resume_material_batch(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self.root.qt,text('打开材料批次','Open material batch'),'','Material batch (material_batch.json)')
        if not path:return
        try:
            from film_foundry.app.material_batch_run import load_plan
            from film_foundry.ui.material_batch_dialog import MaterialBatchDialog
            from film_foundry.ui.sheet_lab_window import SheetBatchWindow
            from film_foundry.ui.material_batch_io import resume
            _,plan=load_plan(path);kind=plan['settings']['kind']
            factory=MaterialBatchDialog if kind in ('peel','integral','plate') else SheetBatchWindow
            page=factory(kind,[item['source'] for item in plan['items']],load_source=False)
            self._mount_material_workspace(page,'batch-resume:'+str(id(page)),text('恢复批次','Resume batch'))
            resume(page,path)
        except Exception as exc:self.status.set(str(exc))

    def _send_result_to_material(self,path,family,*,body_id=None):
        if family=='film':return self._expose_result_on_film(path,body_id=body_id)
        window=self._show_material_workspace(family)
        if window.busy:raise ValueError(text('该材料正在处理，请稍后重试','This material is busy; try again when it completes'))
        window.choose_optical_source(path,body_id=body_id)

    def _expose_result_on_film(self,path,*,body_id=None):
        if self._processing:raise ValueError(text('请等待当前任务完成','Wait for the current task'))
        from film_foundry.ui.optical_source_panel import OpticalSourcePanel
        from film_foundry.app.optical_cascade import source_choices,expose_medium_file
        from film_foundry.app.output_destination import default_output_directory
        from film_foundry.ui.inline_editor import present_inline_editor
        dialog=QtWidgets.QDialog(self.film_workspace);dialog.setWindowTitle(text('胶片光学曝光','Optical exposure onto film'))
        layout=QtWidgets.QVBoxLayout(dialog);panel=OpticalSourcePanel(path,source_choices(path),dialog);layout.addWidget(panel)
        if body_id is not None:panel.body.setCurrentIndex(panel.body.findData(body_id))
        panel.cancelled.connect(dialog.reject)
        def apply():
            if self._processing:return
            try:
                selected,relay=panel.selection();config=self._config()
                target,_=QtWidgets.QFileDialog.getSaveFileName(dialog,text('保存未冲洗潜影','Save undeveloped exposure'),
                    str(default_output_directory()/(Path(path).stem+'.darkroom_latent.npz')),'Latent (*.darkroom_latent.npz)')
                if not target:return
                if not target.endswith('.darkroom_latent.npz'):target+='.darkroom_latent.npz'
            except (ValueError,OSError) as exc:self.status.set(str(exc));return
            self._active_roll=None
            def work():return expose_medium_file(path,target,config,relay,body_id=selected,control=self._workflow_control(self._cancel_flag))
            def done(_):
                self._record_result({'key':'photo:'+str(Path(path).resolve()),'source':str(Path(path).resolve()),'label':Path(path).name,
                    'family':'film','medium':str(target),'artifacts':{}})
                self._open_result_artifact(str(target),'film')
            self._submit_workspace_job(work,done);dialog.accept()
        panel.applied.connect(apply);self.show_workbench();present_inline_editor(self.film_workspace,dialog)

    def open_roll_workspace(self):
        from film_foundry.ui.roll_workspace import RollWorkspace
        from film_foundry.app.desktop_catalog import DesktopCatalog
        if self.roll_view is None:
            storage=DesktopCatalog().path.parent/'electronic_rolls'
            self.roll_view=RollWorkspace(storage,self._submit_workspace_job,self._configure_roll,self.open_library,cancel=self._request_cancel,present=self._present_workspace_dialog)
            self.workspace_host.add_page(self.roll_view,text('整卷暗房','Roll darkroom'),key='roll',closable=False)
        self.roll_view.refresh();self.workspace_host.setCurrentWidget(self.roll_view)
        self.roll_view.set_busy(self._processing)

    def _configure_roll(self,session,stage=None,*,destination=None):
        if self._processing:return
        if session is None:return
        if stage in ('develop','scan') and destination is None:
            from film_foundry.ui.output_destination import OutputDestinationDialog
            from film_foundry.ui.inline_editor import present_inline_editor
            dialog=OutputDestinationDialog(session.data['roll'].get('label') or text('整卷','Whole roll'),self.roll_view or self.film_workspace)
            present_inline_editor(self.roll_view or self.film_workspace,dialog,lambda:self._configure_roll(session,stage,destination=dialog.directory))
            return
        if stage is None:
            if session.data['phase']!='sealed':return
            from film_foundry.ui.roll_process_dialog import RollProcessDialog
            from film_foundry.model.config import DarkroomConfig
            from dataclasses import asdict
            try:
                key='roll-recipe:'+str(session.path)
                existing=self.workspace_host.find_page(key)
                if existing is not None:self.workspace_host.setCurrentWidget(existing);return
                config=DarkroomConfig.from_dict(session.data['recipe_draft']) if session.data.get('recipe_draft') else self._config()
                # Use the exposed material to choose native topology; never a currently selected photo stock.
                dialog=RollProcessDialog(session.process_config(config),self.root.qt)
                def apply_recipe():
                    # Another tab may have started the roll while this draft
                    # stayed open. Re-read its phase; never thaw frozen baths.
                    from film_foundry.app.electronic_roll import RollSession
                    current=RollSession(session.path)
                    if current.data['phase']!='sealed' or self._processing:
                        self.roll_view.status.setText(text('本卷已开始冲洗，药水已冻结；未应用草稿','Development has started; chemistry is frozen. Draft not applied.'));return
                    try:
                        current.data['recipe_draft']=asdict(dialog.config);current.save()
                        self.roll_view.refresh()
                    except (ValueError,OSError) as exc:self.roll_view.status.setText(str(exc))
                self._present_workspace_dialog(dialog,key=key,on_accept=apply_recipe)
            except Exception as exc:
                self.roll_view.status.setText(str(exc))
            return
        self._active_roll=session
        self._selected_photos=[];self._sync_selected_photos()
        for view in (self.input_view,self.result_view,self.inspector_view):
            view.source=None;view.histogram_bins=None;view._sync_source()
        self.preview_histogram.bins=None;self.preview_histogram.update()
        self.pipeline_mode.set('develop_latent' if session.data['phase'] in ('sealed','developing') else 'scan')
        self._update_mode_visibility();self.mode_choice.qt.setEnabled(False)
        self.pages[self.page_index['film']].qt.setEnabled(False)
        if stage in ('develop','scan'):
            try:
                if destination is None and not self.output_path.get().strip():raise ValueError(text('请选择输出目录','Choose an output directory'))
                config=self._config();output=Path(destination or self.output_path.get())
                if stage=='develop' and session.data.get('recipe_draft'):
                    from film_foundry.model.config import DarkroomConfig
                    config.chemistry=DarkroomConfig.from_dict(session.data['recipe_draft']).chemistry
            except Exception as exc:
                self.status.set(str(exc))
                if self.roll_view:self.roll_view.status.setText(str(exc))
                return
            def work():
                callback=lambda record:self.root.after(0,lambda record=record:self._record_result(record))
                control=self._workflow_control(self._cancel_flag)
                if stage=='develop':session.develop(config,output,control=control,on_result=callback)
                else:session.scan(config,control=control,on_result=callback,output=output)
            self._submit_workspace_job(work,lambda _:self.roll_view.refresh() if self.roll_view else None)
        else:
            self.show_workbench();self.navigation.setCurrentRow(self.page_index['scan'])
            self.status.set(text('整卷暗房：材料与曝光已冻结；冲洗开始后药水也冻结。','Roll darkroom: material and exposure are frozen; chemistry freezes when development starts.'))

    def _submit_workspace_job(self,work,done=None):
        if self._processing:
            self.status.set(text('已有任务运行中','A task is already running'));return
        self._cancel_flag=legacy.CancelFlag();self._processing=True;self._task_serial+=1
        self._result_index_errors=[]
        self._preview_timer.stop();self._preview_revision+=1;self._preview_pending=False
        self._set_process_button_mode('cancel');self._sync_activity_ui()
        if self.roll_view:self.roll_view.set_busy(True)
        def finish(value,error):
            self._finish_process()
            if self._active_roll:
                self.pipeline_mode.set('develop_latent' if self._active_roll.data['phase'] in ('sealed','developing') else 'scan')
                self._update_mode_visibility();self._sync_activity_ui()
            if self.roll_view:self.roll_view.set_busy(False);self.roll_view.refresh()
            if error:
                self.status.set(str(error))
                if self.roll_view:self.roll_view.status.setText(str(error))
                QtWidgets.QMessageBox.warning(self.root.qt,text('任务未完成','Task incomplete'),str(error))
            else:
                if done:done(value)
                self.status.set(text('结果已保存，图库索引写入失败：','Results saved; catalog update failed: ')+self._result_index_errors[0] if self._result_index_errors else text('任务完成','Task complete'))
        def run():
            value=None;error=None
            try:value=work()
            except Exception as exc:error=exc
            self.root.after(0,lambda:finish(value,error))
        Thread(target=run,daemon=True).start()

    def open_embedded_editor(self, bank):
        if bank=='runtime':
            from film_foundry.ui.runtime_settings import RuntimeSettings
            dialog=QtWidgets.QDialog(self.root.qt);dialog.setWindowTitle(text('运行与输出设置','Runtime & output settings'));dialog.resize(780,610)
            editor=RuntimeSettings(parent=self.root,config=self._config(),identity=self._frame_identity,embedded=True,
                on_apply=lambda config:(self._apply_editor_bank('runtime',config),setattr(self,'_frame_identity',editor.read_identity())))
            self._editor_windows['runtime']=editor
            def forget_runtime(*_):
                if self._editor_windows.get('runtime') is editor:self._editor_windows.pop('runtime',None)
            dialog.finished.connect(forget_runtime)
            layout=QtWidgets.QVBoxLayout(dialog);editor.root.qt.setParent(dialog,QtCore.Qt.WindowType.Widget);layout.addWidget(editor.root.qt)
            buttons=QtWidgets.QDialogButtonBox();layout.addWidget(buttons)
            buttons.addButton(text('应用并关闭','Apply and close'),QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
            buttons.addButton(text('取消','Cancel'),QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
            def apply_runtime():
                try:
                    config=editor._config_from_controls();identity=editor.read_identity()
                    self._apply_editor_bank('runtime',config);self._frame_identity=identity;dialog.accept()
                except (ValueError,TypeError) as exc:editor.status.set(str(exc))
            buttons.accepted.connect(apply_runtime);buttons.rejected.connect(dialog.reject)
            dialog._editor=editor
            self._present_workspace_dialog(dialog)
            return
        editor=self._editor_windows.get(bank)
        if editor is None or not editor.root.winfo_exists():
            try:
                from film_foundry.tools.run_film_material_editor import FilmMaterialEditor
                from film_foundry.tools.run_develop_process_editor import DevelopProcessEditor
                from film_foundry.tools.run_scanner_render_editor import ScannerRenderEditor
                from film_foundry.ui.runtime_settings import RuntimeSettings
                cls={'film':FilmMaterialEditor,'develop':DevelopProcessEditor,'scanner':ScannerRenderEditor,
                     'positive_scanner':ScannerRenderEditor,'runtime':RuntimeSettings}[bank]
                kwargs={'interpretation':'positive'} if bank=='positive_scanner' else {'identity':self._frame_identity} if bank=='runtime' else {}
                def apply(config):
                    identity=editor.read_identity() if bank=='runtime' else None
                    self._apply_editor_bank(bank,config)
                    if bank=='runtime':self._frame_identity=identity
                editor=cls(parent=self.root,config=self._config(),on_apply=apply,embedded=True,**kwargs)
                self._editor_windows[bank]=editor
                if hasattr(self,'work_sessions'):
                    from dataclasses import asdict
                    self.work_sessions.film_saved['desktop_work']['editors'][bank]=asdict(editor._config_from_controls())
            except Exception as exc:self.status.set(str(exc));return
        if hasattr(editor,'controls_scroller'):
            from film_foundry.ui.editor_navigator import install_editor_navigator
            install_editor_navigator(editor)
        if hasattr(editor, 'set_execution_context'):
            editor.set_execution_context(self._config())
        self._ensure_editor_host()
        if self.editor_tabs.indexOf(editor.root.qt)<0:
            editor.root.qt.hide();editor.root.qt.setWindowFlags(QtCore.Qt.WindowType.Widget)
            self.editor_tabs.addTab(editor.root.qt,text(*{'film':('材料','Material'),'develop':('流程','Process'),'scanner':('扫描','Scanner'),
                'positive_scanner':('正像扫描','Positive scanner'),'runtime':('运行设置','Runtime settings')}[bank]))
        self._embedded_editor_keys.add(bank)
        self.editor_tabs.setCurrentWidget(editor.root.qt);self.workspace_host.setCurrentWidget(self.editor_host)

    def _ensure_editor_host(self):
        if self.editor_host is None:
            self.editor_host=QtWidgets.QWidget();layout=QtWidgets.QVBoxLayout(self.editor_host)
            layout.setContentsMargins(0,6,0,0)
            self.editor_tabs=QtWidgets.QTabWidget();layout.addWidget(self.editor_tabs,1)
            self.workspace_host.add_page(self.editor_host,text('材料与预设','Materials & presets'),key='editors',closable=False)

    def open_preset_library(self):
        from film_foundry.ui.preset_exchange import PresetLibrary
        self._ensure_editor_host()
        page=next((self.editor_tabs.widget(i) for i in range(self.editor_tabs.count()) if isinstance(self.editor_tabs.widget(i),PresetLibrary)),None)
        if page is None:
            page=PresetLibrary(current=self._recipe_export_config,apply=self._apply_shared_presets,
                can_apply=self._can_apply_preset,edit=self._edit_library_preset,changed=self._preset_library_changed)
            self.editor_tabs.insertTab(0,page,text('预设管理','Preset library'))
        else:page.refresh()
        self.editor_tabs.setCurrentWidget(page);self.workspace_host.setCurrentWidget(self.editor_host)
        return page

    def _preset_library_changed(self):
        # Changing catalogue files is not a request to change the active image.
        config=self._config();pairing=(self.link_scanner.get(),self.auto_develop.get())
        baselines=copy.deepcopy(self.control_resets.baselines)
        ready=self._auto_preview_ready;self._auto_preview_ready=False
        try:
            self._refresh_all_preset_combos();self._apply_loaded_config(config)
            self.link_scanner.set(pairing[0]);self.auto_develop.set(pairing[1])
            self.control_resets.baselines=baselines
        finally:self._auto_preview_ready=ready

    def _share_current_presets(self):
        from film_foundry.app.preset_exchange import current_items
        from film_foundry.ui.preset_exchange import SharePresetsDialog
        try:self._present_workspace_dialog(SharePresetsDialog(current_items(self._recipe_export_config()),self.root.qt))
        except (ValueError,TypeError) as exc:self.status.set(str(exc))

    def _import_recipe(self):
        self.open_preset_library().import_file()

    def _can_apply_preset(self,kind):
        mode=self.pipeline_mode.get()
        return kind!='solution' and not (kind in ('film','capture') and mode in ('scan','develop_latent') or kind=='develop' and mode in ('scan','expose'))

    def _apply_shared_presets(self,items,*,validate_only=False):
        from film_foundry.app.preset_exchange import apply_items
        if any(not self._can_apply_preset(item['kind']) for item in items):
            raise ValueError(text('该阶段已冻结，请仅保存为预设','This stage is frozen; save the preset for later'))
        config=apply_items(self._config(),items)
        if validate_only:return config
        paired=(self.link_scanner.get(),self.auto_develop.get());kinds={item['kind'] for item in items}
        self._apply_loaded_config(config)
        if not kinds.intersection(('film','scanner')):self.link_scanner.set(paired[0])
        if not kinds.intersection(('film','develop')):self.auto_develop.set(paired[1])
        self.status.set(text('已应用勾选的预设；运行与输出设置保留','Selected presets applied; runtime and output settings retained'))

    def _edit_library_preset(self,item):
        from film_foundry.app.preset_exchange import apply_items
        kind=item['kind']
        if kind=='solution':
            from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
            self._present_workspace_dialog(BathSolutionDialog(item['payload']['action'],item['payload'],self.root.qt));return
        if kind=='capture':
            from film_foundry.ui.preset_exchange import ReceivePresetsDialog
            self._present_workspace_dialog(ReceivePresetsDialog([item],self.root.qt,on_apply=self._apply_shared_presets,can_apply=self._can_apply_preset,keep=False));return
        existing=self._editor_windows.get(kind)
        if existing is not None and existing.root.winfo_exists():
            if QtWidgets.QMessageBox.question(self.root.qt,text('载入预设副本','Load preset copy'),text('替换该编辑器中的草稿？当前工作区不会被更改。','Replace the draft in this editor? The working image is unchanged.'))!=QtWidgets.QMessageBox.StandardButton.Yes:return
        self.open_embedded_editor(kind)
        editor=self._editor_windows.get(kind)
        if editor is not None:editor._set_controls_from_config(apply_items(self._config(),[item]))

    def _open_runtime_settings(self):
        self.open_embedded_editor('runtime')

    def _refresh_formation_status(self):
        if not self.root.winfo_exists():return
        from film_foundry.model.config import DarkroomConfig
        config = copy.deepcopy(self._recipe_base) if self._recipe_base is not None else DarkroomConfig()
        config.fast_mode = bool(self.fast_mode.get())
        config.processing.execution_mode = 'reduced_fast' if config.fast_mode else 'quality'
        config.processing.quality_mode = self.quality_mode.get()
        self.formation_badge.set_config(config)
        for bank in ('film', 'develop'):
            editor = self._editor_windows.get(bank)
            if editor is not None and editor.root.winfo_exists():
                editor.set_execution_context(config)

    def _present_workspace_dialog(self,dialog,title=None,*,key=None,on_accept=None):
        if not key or not key.startswith('light-table:'):
            from film_foundry.ui.inline_editor import present_inline_editor
            if title:dialog.setWindowTitle(title)
            dialog.setProperty('configurationKey',key)
            return present_inline_editor(self.root.qt,dialog,on_accept)
        if on_accept is not None:dialog.accepted.connect(on_accept)
        self.workspace_host.add_page(dialog,title or dialog.windowTitle(),key=key)
        return dialog

    def find_configuration_dialog(self,key):
        return next((dialog for dialog in self.root.qt.findChildren(QtWidgets.QDialog)
                     if dialog.property('configurationKey')==key and dialog.isVisible()),None)

    def _open_editor(self, module_name):
        routes={"film_foundry.tools.run_film_material_editor":"film",
                "film_foundry.tools.run_develop_process_editor":"develop",
                "film_foundry.tools.run_scanner_render_editor":"scanner",
                "film_foundry.tools.run_positive_scanner_editor":"positive_scanner"}
        if module_name in routes:self.open_embedded_editor(routes[module_name])
        else:super()._open_editor(module_name)

    def run(self):
        self.open_library()
        self.root.mainloop()

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
        if hasattr(self,'edit_tools'):self.edit_tools.schedule()
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
        if self._active_roll:
            self._preview_pending=False
            if not automatic:self.status.set(text('整卷暗房请在冲洗完成后扫描观察','Develop the whole roll before observing it through scanning'))
            return
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
        request=legacy.PreviewRequest(self._preview_generation,mode,config,path,self.PREVIEW_EDGE,True,
            path_seed_offset=legacy.offset_for(path,self._path_seed_offsets))
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
        if not self._active_roll and not self._required_paths(preview=True):return
        from film_foundry.ui.output_destination import OutputDestinationDialog
        from film_foundry.ui.inline_editor import present_inline_editor
        identity=(self.pipeline_mode.get(),self.input_path.get(),self.negative_path.get(),tuple(self._selected_photos),self._active_roll)
        try:export_config=self._config()
        except Exception as exc:self.status.set(str(exc));return
        quality={'draft':text('低','Low'),'standard':text('中','Medium'),'high':text('高','High')}.get(export_config.processing.quality_mode,export_config.processing.quality_mode)
        from film_foundry.core.execution import processing_long_edge
        limit=export_config.output.preview_long_edge if self.run_as_preview.get() else processing_long_edge(export_config)
        from film_foundry.ui.precision_labels import film_storage_label
        details=[(text('处理质量','Processing quality'),quality),
                 (text('形成尺寸','Formation size'),text(f'长边上限 {limit} px',f'Long edge up to {limit} px') if limit else text('原尺寸','Original size')),
                 (text('材料保存精度','Material storage precision'),film_storage_label(export_config.processing.quality_mode,export_config.output.material_storage_precision)),
                 (text('成片旋转','Image rotation'),f'{export_config.output.rotation_quarters*90}°'),
                 (text('输出格式','Output format'),f'{export_config.output.format.upper()} · {export_config.output.bit_depth}-bit'),
                 (text('编码质量','Encoding quality'),str(export_config.output.quality) if export_config.output.format.lower() in ('jpg','jpeg','webp') else text('无损','Lossless'))]
        dialog=OutputDestinationDialog(self.source_summary.qt.text(),self.film_workspace,details=details)
        def start():
            current=(self.pipeline_mode.get(),self.input_path.get(),self.negative_path.get(),tuple(self._selected_photos),self._active_roll)
            if current!=identity:
                self.status.set(text('来源已改变，请重新执行','Source changed; start the task again'));return
            self.output_path.set(str(dialog.directory))
            self.scan_output_near_source.set(False)
            self._execute_process()
        present_inline_editor(self.film_workspace,dialog,start)

    def _execute_process(self):
        if self._processing:return
        if self._active_roll:
            phase=self._active_roll.data['phase']
            self._configure_roll(self._active_roll,'develop' if phase in ('sealed','developing') else 'scan',destination=Path(self.output_path.get()));return
        if self._selected_photos and self._is_photo_workflow() and Path(self.input_path.get()).is_file():
            from film_foundry.app.workspace_jobs import process_photos
            try:config=self._freeze_incident_config(self._config())
            except Exception as exc:self.status.set(str(exc));return
            paths=self._selected_photos if self.input_path.get() in self._selected_photos else [self.input_path.get()]
            output=Path(self.output_path.get())
            scaled=bool(self.run_as_preview.get())
            path_seed_offsets=copy.deepcopy(self._path_seed_offsets)
            command=self.pipeline_mode.get()
            def work():
                failures=process_photos(paths,output,config,control=self._workflow_control(self._cancel_flag),scaled_override=scaled,
                    path_seed_offsets=path_seed_offsets,command=command,
                    on_run=lambda root:self.root.after(0,lambda:self._remember_workspace_output(root)),
                    on_result=lambda record:self.root.after(0,lambda record=record:self._record_result(record)))
                if failures:raise ValueError('\n'.join(f"{f['source']}: {f['error']}" for f in failures))
            self._submit_workspace_job(work);return
        if not self._required_paths():return
        self._task_serial+=1;self._task_stage=None
        self._preview_timer.stop()
        self._preview_pending=False
        self._preview_revision+=1
        super()._start_process()
        self._sync_activity_ui()

    def _remember_workspace_output(self,path):
        self._latest_output=Path(path);self._sync_activity_ui()

    def _finish_process(self):
        super()._finish_process()
        self._sync_activity_ui()
        if self._close_after_process:
            self._preview_pending=False;self._preview_timer.stop();self.root.after(0,self.root.qt.close);return
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

    def _resume_batch(self,plan,cancel_flag):
        try:
            from film_foundry.app.replay_batch import run_resume_batch
            result=run_resume_batch(plan,control=self._workflow_control(cancel_flag),on_result=lambda record:self.root.after(0,lambda record=record:self._record_result(record)))
            self.root.after(0,lambda:self._remember_workspace_output(plan.output_root))
            message=tr('status.resume_complete').format(completed=result.completed,reused=result.reused,failed=len(result.failures),output=plan.output_root)
            if result.cancelled:message+=' · '+tr('status.cancelling')
            if result.failures:message+=' · '+str(result.failures[0][1])
            if result.record_error:message+=' · '+str(result.record_error)
            self._post_status(message)
        except Exception as exc:self._post_status(str(exc))
        finally:self.root.after(0,self._finish_process)

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
        self.preview_state_label.setToolTip(messages[kind])

    def _update_save_work_action(self):
        if not hasattr(self,'save_work_button'):return
        sessions=getattr(self,'work_sessions',None)
        visible=bool(sessions and sessions.has_content(self.workspace_host.currentWidget()))
        self.save_work_button.setVisible(visible)
        self.save_work_button.setEnabled(bool(sessions and not sessions.pending and not self._workspace_io_busy))

    def _sync_activity_ui(self):
        if not hasattr(self,"activity_label") or not self.root.winfo_exists():return
        busy=self._processing
        self.film_workspace.setProperty('workspaceBusy',busy or self._preview_running)
        if self.roll_view:self.roll_view.setProperty('workspaceBusy',busy and self._active_roll is not None)
        strict=self._active_roll is not None
        self.mode_choice.qt.setEnabled(not busy and not strict)
        self.resume_button.qt.setEnabled(not busy and not strict)
        self.pages[self.page_index['film']].qt.setEnabled(not strict)
        self.pages[self.page_index['develop']].qt.setEnabled(not strict)
        for button in (self.preview_button,self.clear_preview_cache_button):button.qt.setEnabled(not busy and not self._preview_running and not strict)
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
            if self.roll_view and self.workspace_host.currentWidget() is self.roll_view:self.roll_view.status.setText(self.status.get())

    def _remember_output(self, path, serial):
        if serial==self._task_serial and Path(path).is_dir():
            self._latest_output=Path(path);self._sync_activity_ui()

    def _batch_status(self, operation, completed, failures, output_root):
        serial=self._task_serial
        self.root.after(0,lambda:self._remember_output(output_root,serial))
        return super()._batch_status(operation,completed,failures,output_root)

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
        if self._workspace_exit_pending:return False
        pending=self._processing or self._preview_running or self._workspace_io_busy or self.workspace_host.busy_pages()
        if pending:
            if self._processing:
                if not self._confirm_cancel_close():return False
            else:
                answer=QtWidgets.QMessageBox.question(self.root.qt,text('后台任务正在运行','Background tasks running'),
                    text('等待当前操作完成后退出？批量任务将停止在下一张。','Exit after active operations finish? Batches stop before the next image.'),
                    QtWidgets.QMessageBox.StandardButton.Yes|QtWidgets.QMessageBox.StandardButton.No,QtWidgets.QMessageBox.StandardButton.No)
                if answer!=QtWidgets.QMessageBox.StandardButton.Yes:return False
            self._workspace_exit_pending=True;self._preview_pending=False;self._preview_timer.stop()
            self.workspace_host.setEnabled(False)
            if self._processing:self._request_cancel()
            for window in self._material_windows.values():
                cancel=getattr(window,'_batch_cancel',None)
                if cancel is not None:cancel.cancel()
            def finish_exit():
                if self._processing or self._preview_running or self._workspace_io_busy or self.workspace_host.busy_pages():
                    self.root.after(100,finish_exit);return
                self._workspace_exit_pending=False;self.root.qt.close()
            self.root.after(100,finish_exit)
            return False
        if hasattr(self,'work_sessions') and not self.work_sessions.bypass:
            def close_saved():
                self.work_sessions.bypass=True
                try:self.root.qt.close()
                finally:self.work_sessions.bypass=False
            self.work_sessions.protect(lambda:self.root.after(0,close_saved))
            return False
        # Parent destruction does not deliver closeEvent to embedded children.
        # Let each page stop its timers and release its executor first.
        for page in list(self._material_windows.values()):page.close()
        for page in list(self.workspace_host._pages):
            if isinstance(page,QtWidgets.QDialog) or page.__class__.__name__=='PrintmakingWorkspace':page.close()
        self._workspace_files.shutdown()
        self._film_workspace_files.shutdown()
        return True

    def group(self, page, zh, en, expert=False, *, key=None, collapsed=False):
        if key is None:
            group=ui.Group(page,text=text(zh,en).replace("&","&&"),padding=10)
        else:
            from film_foundry.ui.disclosure import Disclosure
            group=Disclosure(page,text(zh,en),expanded=self._section_states.get(key,not collapsed),
                on_toggle=lambda value:self._section_states.__setitem__(key,value))
            self.sections[key]=group
            group.reset_button.show()
            group.reset_button.clicked.connect(lambda:self.control_resets.reset_group(group.qt))
        group.pack(fill="x",pady=(0,8));group.columnconfigure(1,weight=1);group.columnconfigure(2,weight=1)
        if expert:self.expert_groups.append(group)
        return group

    def choice(self, group, label, variable, values, row=0, callback=None):
        caption=ui.Label(group,text=label,wraplength=120);caption.qt.setMinimumWidth(88);caption.qt.setMaximumWidth(120);caption.grid(row=row,column=0,sticky="ew")
        choice=ui.Choice(group,textvariable=variable,values=values,state="readonly");choice.grid(row=row,column=1,columnspan=3,sticky="ew")
        choice.qt.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        choice.qt.setMinimumContentsLength(4)
        choice.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Fixed)
        if callback:choice.bind("<<ComboboxSelected>>",callback)
        def reset(value):
            variable.set(value)
            if callback:callback()
        reset_button=self.control_resets.button(group.qt,variable,variable.get,reset)
        group._ensure_grid_layout().addWidget(reset_button,row,4)
        return choice

    def preset_choice(self, group, label, variable, callback, row=0):
        choice=self.choice(group,label,variable,(),row,callback)
        choice.grid(row=row,column=1,columnspan=2,sticky="ew")
        variable._bind_view(lambda value:choice.qt.setToolTip(str(value)),choice)
        button=ui.Button(group,text=text("选择","Choose"),command=lambda:self._browse_presets(choice,label))
        button.grid(row=row,column=3,sticky="e");button.set_tooltip(text("搜索并选择预设","Search and choose a preset"))
        return choice

    def _browse_presets(self, choice, title):
        from film_foundry.ui.preset_picker import PresetPicker
        combo=choice.qt
        labels=tuple(combo.itemText(index) for index in range(combo.count()))
        records=[]
        for bank in ('film','develop','scanner'):
            if choice is not getattr(self,bank+'_preset_combo',None):continue
            mapping=getattr(self,'_'+bank+'_display_key_map',{})
            for label in labels:
                key=mapping.get(label,'');record=getattr(legacy,bank+'_preset_record')(key) or {}
                path=getattr(legacy,bank+'_preset_path')(key)
                user_dir=getattr(legacy,'USER_'+bank.upper()+'_PRESET_DIR')
                records.append({**record,'key':key,'origin':'custom' if path.resolve().is_relative_to(user_dir.resolve()) else 'builtin'})
        picker=PresetPicker(self.root.qt,title,labels,combo.currentIndex(),records=records or None)
        def apply():
            if picker.selected_index is not None:
                combo.setCurrentIndex(picker.selected_index)
                combo.activated.emit(picker.selected_index)
        self._present_workspace_dialog(picker,on_accept=apply)

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

    def sliders(self, group, specs, start=0):
        short_labels={"exposure_ev":text("曝光补偿 EV","Exposure EV"),"print_exposure":text("成片曝光 EV","Output exposure EV"),
            "scanner_raw_border":text("参考边宽度 %","Reference margin %"),"anti_banding":text("防色带","Anti-banding"),
            "print_contrast":text("对比度","Contrast"),"scan_saturation":text("饱和度","Saturation"),"scan_strength":text("归一化强度","Normalization strength")}
        for row,(name,label,low,high,*precision) in enumerate(specs,start):
            caption=ui.Label(group,text=short_labels.get(label,tr("label."+label)),wraplength=120);caption.qt.setMinimumWidth(88);caption.qt.setMaximumWidth(120);caption.grid(row=row,column=0,sticky="ew",pady=4)
            variable=getattr(self,name)
            slider=ui.Slider(group,from_=low,to=high,variable=variable);slider.grid(row=row,column=1,columnspan=2,sticky="ew")
            slider.qt.setMinimumWidth(32);slider.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Fixed)
            entry=ui.NumberInput(group,from_=-1e9,to=1e9,increment=10**-(precision[0] if precision else 2),textvariable=variable)
            entry.qt.setFixedWidth(88);entry.grid(row=row,column=3,sticky="e")
            help_label="exposure_occlusion" if label.startswith("exposure_occlusion_") else "print_color_shift" if label.startswith("filter_") else "highlight_color_bias" if label.startswith("highlight_") else label
            for widget in (caption,slider,entry):legacy.attach_tooltip(widget,"help."+help_label)
            self.fields[name]=(slider,entry)
            reset_button=self.control_resets.button(group.qt,variable,variable.get,variable.set)
            group._ensure_grid_layout().addWidget(reset_button,row,4)

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
        def reset(value):
            variable.set(value)
            if command:command()
        reset_button=self.control_resets.button(group.qt,variable,variable.get,reset)
        group._ensure_grid_layout().addWidget(reset_button,row,4)
        caption=ui.Label(frame,text=label,wraplength=300);caption.pack(side="left",fill="x",expand=True)
        caption.qt.mousePressEvent=lambda event:toggle.qt.click() if event.button() == QtCore.Qt.MouseButton.LeftButton else None
        return frame

    def _text_row(self,parent,label,var,row,help_key=None):
        super()._text_row(parent,label,var,row,help_key)
        button=self.control_resets.button(parent.qt,var,var.get,var.set)
        parent._ensure_grid_layout().addWidget(button,row,4)

    def _build_film(self,page):
        self.film_source_group=self.group(page,"材料与画幅","Material & format")
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
        filters=ui.Frame(g);self.material_filter_row=filters;filters.grid(row=1,column=0,columnspan=4,sticky='ew',pady=3)
        self.material_filter_choices=[]
        for variable,values,caption in ((self.material_color_display,color_choices,text('色彩筛选','Color filter')),(self.material_origin_display,origin_choices,text('来源筛选','Source filter'))):
            choice=ui.Choice(filters,textvariable=variable,values=tuple(label for _,label in values),state='readonly')
            choice.pack(side='left',fill='x',expand=True,padx=(0,3));choice.bind('<<ComboboxSelected>>',self._on_material_list_filter)
            choice.qt.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            choice.qt.setMinimumContentsLength(4);choice.qt.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,QtWidgets.QSizePolicy.Policy.Fixed)
            choice.qt.setAccessibleName(caption);choice.set_tooltip(caption);self.material_filter_choices.append(choice)
        self.film_preset_combo=self.preset_choice(g,tr("label.film_preset"),self.film_preset_display,self._on_film_preset_display_selected,2)
        self.wrapped_toggle(g,text("显示更多风格变体", "Show more style variants"),self.show_preset_variants,3,self._change_preset_visibility)
        self.choice(g,tr("label.frame_size"),self.frame_size,("half_frame","35mm","6x6","6x7","4x5"),4)
        self._preset_source_note(g,self.film_preset_source,5)
        self.material_editor_link=self._editor_link(g,"材料编辑器…","Material editor…","run_film_material_editor",6)
        self.film_border_group=self.group(page,'片边与齿孔','Film edges & perforations')
        self.film_border_button=ui.Button(self.film_border_group,text=text('配置片边与齿孔…','Configure film edges…'),command=self._edit_film_border)
        self.film_border_button.pack(fill='x')
        self.film_border_group.set_tooltip(text('位于画面外的真实介质片边。边字通过材料与药浴形成，齿孔没有片基。画面内的文字在“画面打印”中设置。','Physical film rebates outside the image. Lettering forms through the stock and baths; perforations contain no film base. Use Picture imprint for text inside the photograph.'))
        self.capture_exposure_group=self.group(page,"单张／批次曝光","Single / batch exposure")
        self.sliders(self.capture_exposure_group,[("exposure_ev","exposure_ev",-5,5)])
        ui.Button(self.capture_exposure_group,text=text('曝光时间条件…','Exposure time conditions…'),command=self._edit_exposure_conditions).grid(row=1,column=0,columnspan=4,sticky='ew')
        self.film_condition_group=self.group(page,"保存与过期","Storage & ageing",key="film.ageing",collapsed=False)
        self.sliders(self.film_condition_group,[("material_degradation","material_degradation",0,1)])
        self.film_gate_group=self.group(page,"片门脏物","Gate dirt",key="film.gate",collapsed=True)
        self.toggles(self.film_gate_group,[(key,key) for key in ("exposure_occlusion_dust","exposure_occlusion_hair","exposure_occlusion_insect","exposure_occlusion_debris")])
        self.film_gate_group.set_tooltip(text("作用于曝光，与机身漏光独立保持。", "Applied during exposure; retained independently of the body leak."))
        ui.Button(self.film_gate_group,text=text("重设片门脏物身份（保留漏光）", "Reset gate dirt identity (keep the leak)"),command=lambda:self._replace_camera_identity(gate=True)).grid(row=5,column=0,columnspan=4,sticky="ew")
        self.film_effect_group=self.group(page,"机身漏光","Camera-body light leak",key="film.leak",collapsed=True)
        self.sliders(self.film_effect_group,[("light_leak","light_leak",0,1)])
        self.light_leak_template_display.set(light_leak_template_label(self.light_leak_template.get()))
        self.choice(self.film_effect_group,tr("label.light_leak_template"),self.light_leak_template_display,tuple(light_leak_template_labels().values()),1)
        self.film_effect_group.set_tooltip(text("漏光在形成密度之前加进曝光。更换胶卷不会改这条机身身份。更细的组合仍在工艺编辑器。", "Light leak is extra exposure before density forms. Changing film does not replace this body identity. Finer combinations stay in the process editor."))
        ui.Button(self.film_effect_group,text=text("更换机身漏光身份（保留片门）", "Replace leak identity (keep the gate)"),command=lambda:self._replace_camera_identity(structure=True)).grid(row=3,column=0,columnspan=4,sticky="ew")
        self.film_imprint_group=self.group(page,"画面打印","In-picture imprint",key="film.imprint",collapsed=True)
        g=self.film_imprint_group
        self.imprint_style_display.set(imprint_style_label(self.imprint_style.get()));self.imprint_side_display.set(imprint_side_label(self.imprint_side.get()))
        self.choice(g,tr("label.imprint_style"),self.imprint_style_display,tuple(imprint_style_labels().values()))
        self.choice(g,tr("label.imprint_side"),self.imprint_side_display,tuple(imprint_side_labels().values()),1)
        self._text_row(g,tr("label.imprint_text"),self.imprint_text,2);self._text_row(g,tr("label.imprint_time"),self.imprint_time,3)
        ui.Button(g,text=text('字形与自由位置…','Typeface & free position…'),command=self._edit_imprint).grid(row=4,column=0,columnspan=4,sticky='ew')
        self.control_resets.add(g.qt,'imprint.placement',self._imprint_placement,self._restore_imprint_placement)
        g.set_tooltip(text("打印位于照片画面内，在曝光时形成，随冲洗响应。文字与时间均为空或光源关闭时不打印。片基边沿的厂标、条码另在“材料与预设”编辑布局。", "Printed inside the picture during exposure, then developed with the image. No imprint when the lamp is off or both fields are empty. Edit film-edge factory lettering and bars separately under Materials & presets."))
        page.set_tooltip(text("光谱升维在独立配置里直接开启，不需要附带的 JSON。", "Spectral lift is switched on under Resource editors and does not need a bundled JSON file."))

    def _restore_imprint_placement(self,value):
        if self._recipe_base is None:self._recipe_base=self._config()
        border=self._recipe_base.film_border
        border.imprint_font,border.imprint_position_xy,border.imprint_scale=value

    def _imprint_placement(self):
        border=(self._recipe_base or legacy.DarkroomConfig()).film_border
        return border.imprint_font,border.imprint_position_xy,border.imprint_scale

    def _edit_imprint(self):
        from film_foundry.ui.imprint_dialog import ImprintDialog
        dialog=ImprintDialog(self._config().film_border,self.root.qt)
        def apply():
            value=dialog.value();self._restore_imprint_placement((value.imprint_font,value.imprint_position_xy,value.imprint_scale));self._preview_edited()
        self._present_workspace_dialog(dialog,key='imprint',on_accept=apply)

    def _edit_exposure_conditions(self):
        from film_foundry.ui.film_exposure_editor import FilmExposureDialog
        try:
            existing=self.workspace_host.find_page('film-exposure')
            if existing is not None:self.workspace_host.setCurrentWidget(existing);return
            dialog=FilmExposureDialog(self._config(),self.root.qt)
            def apply():
                config=self._config()
                config.exposure.duration_seconds=dialog.duration
                self._recipe_base=config
                self._preview_edited()
            self._present_workspace_dialog(dialog,key='film-exposure',on_accept=apply)
        except ValueError as exc:self.status.set(str(exc))

    def _edit_edge_stencil(self):
        from film_foundry.ui.edge_mark_editor import EdgeMarkEditor
        config = self._config()
        editor = EdgeMarkEditor(config.film_border, self.root.qt)
        def apply():
            try:
                editor.apply_to(config.film_border)
            except ValueError as exc:
                self.status.set(str(exc))
                return
            self._recipe_base = config
            self._preview_edited()
        self._present_workspace_dialog(editor,on_accept=apply)

    def _edit_film_border(self):
        from film_foundry.ui.film_border_dialog import FilmBorderDialog
        editor=FilmBorderDialog(self._config().film_border,self.root.qt)
        def apply():
            config=self._config();config.film_border=editor.value();self._recipe_base=config
            self._update_mode_visibility();self._preview_edited()
        self._present_workspace_dialog(editor,key='film-border',on_accept=apply)

    def _build_develop(self,page):
        self.principle_group=self.group(page,'药浴与操作条件','Baths & operations')
        ui.Button(self.principle_group,text=text('编辑工艺条件…','Edit process conditions…'),command=self._edit_principle_process).pack(fill='x')
        self.principle_group.set_tooltip(text('药浴控制物质反应；操作页记录浸润、清洗、变温、硬化和含银液接触。材料响应在材料编辑器中设置，潜影曝光后冻结。','Baths drive material reactions. Operations record contact, washing, temperature, hardening and silver solution. Material responses are configured in the material editor and frozen at exposure.'))
        self.develop_source_group=self.group(page,"本次冲洗程序","Development process")
        self.develop_preset_combo=self.preset_choice(self.develop_source_group,tr("label.develop_preset"),self.develop_preset_display,self._on_develop_preset_display_selected)
        self._preset_source_note(self.develop_source_group,self.develop_preset_source,1)
        self.process_editor_link=self._editor_link(self.develop_source_group,"配置药浴与工艺步骤…","Configure baths & process steps…","run_develop_process_editor",2)
        self.process_editor_link.configure(text=text('编辑步骤与药浴…','Edit steps & baths…'),command=self._edit_current_process)
        self.wrapped_toggle(self.develop_source_group,text("显示更多药浴方案","Show more bath recipes"),self.show_preset_variants,3,self._change_preset_visibility)
        self.wrapped_toggle(self.develop_source_group,text('自动 · 材料推荐冲洗','Auto · recommended development'),self.auto_develop,4,self._apply_recommended_development)
        self.develop_source_group.set_tooltip(text('自动档采用材料所属工艺的标准药浴。手动调整即转为手动；不代替特殊交叉冲洗，也不改曝光和材料特色。','Auto uses the standard baths for the material process. Editing switches to manual; exposure and material character are retained.'))
        self.develop_effect_group=self.group(page,"冲洗操作","Process controls")
        self.sliders(self.develop_effect_group,[("develop_time","develop_time",0,60),("develop_temperature","temperature",4,45),("developer_concentration","concentration",.05,5),("agitation","agitation",0,5),("push","push",-4,6)])
        accidents=self.group(page,"冲洗事故","Process accidents",key="develop.accidents",collapsed=True)
        self.sliders(accidents,[("chemical_stain","chemical_stain",0,1),("uneven_development","uneven_development",0,1),("reticulation_strength","reticulation_strength",0,1),("silver_plating","silver_plating",0,1),("dye_damage_strength","dye_damage_strength",0,1),("silver_retention","silver_retention",0,1)])
        self.effect_master_control=self.wrapped_toggle(accidents,text('期望事故总开关','Expected incidents enabled'),self.effect_intent_enabled,6,self._preview_edited)
        self.effect_inspector_button=ui.Button(accidents,text=text('查看实际工艺参数…','Inspect actual process parameters…'),command=self._inspect_effect_intent)
        self.effect_inspector_button.grid(row=7,column=0,columnspan=4,sticky='ew')
        self.effect_upgrade_button=ui.Button(accidents,text=text('将旧版事故转换为工艺映射','Convert legacy incidents to process mapping'),command=self._upgrade_effect_controls)
        self.effect_upgrade_button.grid(row=8,column=0,columnspan=4,sticky='ew');self._sync_effect_mapping_ui()
        ui.Button(accidents,text=text('关联影响…','Linked effects…'),command=self._inspect_effect_couplings).grid(row=9,column=0,columnspan=4,sticky='ew')
        accidents.set_tooltip(text('效果由实际材料与操作形成，可能同时改变密度、反差或色彩。总开关保留数值，手工药浴/操作仍执行。线性对象为材料量，非屏幕RGB；关联影响入口显示当前条件。','Actual materials and operations may also change density, contrast or colour. The master switch keeps values; manual baths remain. Linearity concerns material quantities, not display RGB. Linked effects shows current conditions.'))
        formation=self.group(page,"颗粒与高光扩散","Grain & halation",key="develop.formation",collapsed=True)
        self.sliders(formation,[("grain","grain",0,5),("halation","halation",0,5)])
        self.develop_additional_groups=[accidents,formation]
        identity=self.group(page,"单张／批次瑕疵身份","Single / batch defect identity",key="develop.identity",collapsed=True);self.develop_identity_group=identity
        identity.set_tooltip(text("此处更换冲洗批次瑕疵；相机漏光与片门身份独立保持。颗粒沿用所选随机策略。已保存潜影和冲洗结果不会改变。", "Change process-batch defects here; camera leak and gate identities stay separate. Grain keeps its selected random strategy. Saved latents and results stay unchanged."))
        ui.Button(identity,text=text("更换本次冲洗批次瑕疵", "New process-batch defects"),command=self._new_incident_identity).pack(fill="x")
        self.control_resets.add(identity.qt,'chemistry.incident_identity_seed',lambda:getattr(self,'_incident_batch_seed',None),self._restore_incident_identity)
        self.develop_detail_group=self.group(page,"冲洗与形成细调","Development & formation details",True,key="develop.details")
        g=self.develop_detail_group
        self.sliders(g,[("negative_contrast","negative_contrast",.3,3),("exhaustion","developer_exhaustion",0,1),("fixer_exhaustion","fixer_exhaustion",0,1),("compensation","compensation",0,1),("development_adjacency_strength","development_adjacency_strength",0,1),("development_adjacency_radius","development_adjacency_radius",.0002,.02,4),("process_variation","process_variation",0,1),("grain_size","grain_size",.1,4),("halation_sensitivity","halation_sensitivity",-1,1),("halation_edge","halation_edge",0,1),("emulsion_mtf","emulsion_mtf",0,1),("artifact_suppression","artifact_suppression",0,1)])
        self.choice(g,tr("label.developer"),self.developer_type,("standard","fine_grain","compensating","high_acutance","high_contrast","monobath","experimental_reversal"),12)
        self.choice(g,tr("label.fixer"),self.fixer_type,("standard","rapid","hardening","monobath","experimental_mask_bleach"),13)
        self.toggles(g,[("enable_mtf","enable_mtf"),("enable_halation","enable_halation"),("enable_grain","enable_grain"),("force_bw","bw_negative")],14)

    def _build_scan(self,page):
        g=self.group(page,"观察方式","Observation")
        self.scanner_preset_combo=self.preset_choice(g,tr("label.scanner_preset"),self.scanner_preset_display,self._on_scanner_preset_display_selected)
        self._preset_source_note(g,self.scanner_preset_source,1)
        self.scan_recommendation._bind_view(lambda value,target=g:target.set_tooltip(str(value)),g)
        self.wrapped_toggle(g,text("显示更多风格变体", "Show more style variants"),self.show_preset_variants,3,self._change_preset_visibility)
        self.scanner_editor_link=self._editor_link(g,"完整扫描设置…","Full scanner settings…","run_scanner_render_editor",4)
        self.wrapped_toggle(g,text('随材料匹配扫描','Link scan to material'),self.link_scanner,5,self._apply_matching_scanner)
        g=self.group(page,"基础调色","Basic adjustments")
        self.sliders(g,[("print_exposure","print_exposure",-5,5),("print_contrast","print_contrast",.3,3),("saturation","scan_saturation",0,3),("scan_strength","scan_strength",0,1)])
        self.toggles(g,[("scan_normalize","scan_normalize")],4)
        mixer_group=self.group(page,"分色 HSL","Color Mixer · HSL",key="scan.hsl",collapsed=True)
        self.color_mixer=ColorMixerEditor(mixer_group.qt);mixer_group._ensure_pack_layout().addWidget(self.color_mixer)
        self.color_mixer.load(self._recipe_base.scanner.color_mixer if self._recipe_base else None)
        self.color_mixer.changed.connect(self._preview_edited)
        self.control_resets.add(mixer_group.qt,'scanner.color_mixer',self.color_mixer.read,self.color_mixer.load)
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
            self.control_resets.add(curves_group.qt,'scanner.'+key,editor.read,lambda value,e=editor:(e.load(value),self._update_curve_summary()))
            editor.canvas.setToolTip(text('背景是当前成片的分布，供调节参照；不是该曲线之前的独立测量。','Background shows the current output distribution for reference, not a separate measurement before this curve.'))
        self._update_curve_summary()
        g=self.group(page,"底片解释与灯台","Film interpretation & light table",key="scan.interpretation",collapsed=False)
        for row,name in enumerate(("scan_remove_base","scan_invert")):
            self.wrapped_toggle(g,tr("label."+name),getattr(self,name),row,self._sync_manual_scan_controls)
        self.scan_interpretation_group=g
        self.scan_pipeline_description._bind_view(lambda value,target=g:target.set_tooltip(str(value)),g)
        lamp=ui.Frame(g);lamp.grid(row=3,column=0,columnspan=4,sticky="ew");lamp.columnconfigure(1,weight=1);lamp.columnconfigure(2,weight=1)
        self.sliders(lamp,[("light_table_ev","transmission_light_ev",-4,4),("light_table_temperature","transmission_light_temperature",2400,10000,0),("positive_color_control","positive_color_control",0,1)])
        g=self.group(page,"观察细调","Observation details",True,key="scan.details")
        self.sliders(g,[("print_shift_r","filter_r",-.5,.5),("print_shift_g","filter_g",-.5,.5),("print_shift_b","filter_b",-.5,.5),("highlight_green","highlight_green",.1,2),("highlight_blue","highlight_blue",.1,2),("projection_white_softness","projection_white_softness",0,1),("projection_black_adaptation","projection_black_adaptation",0,1),("negative_channel_compensation_strength","negative_channel_compensation_strength",0,1)])
        self.toggles(g,[("negative_channel_compensation","negative_channel_compensation")],8)

    def _build_output(self,page):
        g=self.group(page,"成片格式","Image format")
        self.output_format_group=g
        self.choice(g,tr("label.output_format"),self.output_format,("jpg","png","tiff","webp"))
        g=self.group(page,"扫描输出选项","Scan output options",key="output.scan",collapsed=False)
        self.toggles(g,[("include_clear_base_border","include_clear_base_border")])
        self.sliders(g,[("scanner_raw_border","scanner_raw_border",0,30),("anti_banding","anti_banding",0,1)],start=1)
        g.set_tooltip(text("片基参考边用于扫描测光或展示，不属于底片齿孔、厂标或画面打印。防色带用于成片编码前的微弱抖动。", "The clear-base reference margin is for scan observation, not sprockets, factory marks or an in-picture imprint. Anti-banding applies subtle dither before image encoding."))
        g=self.group(page,"附属文件与诊断输出","Additional files & diagnostics",key="output.exports",collapsed=True)
        self.toggles(g,[("save_sidecar","sidecar"),("debug_output","debug"),("comparison_grid","grid"),("save_scanner_raw","save_scanner_raw")])
        for row,name,label in [(4,"export_transparent_plate","export_transparent_plate"),(5,"export_plate_set","export_plate_set"),(6,"export_layer_pack","export_layer_pack")]:
            w=self.wrapped_toggle(g,tr("label."+label),getattr(self,name),row,self._update_export_option_state)
            if name=="export_transparent_plate":self.transparent_export_check=w
            if name=="export_plate_set":self.plate_set_export_check=w
        self._update_export_option_state()

    def _enter_share_code(self):
        value,accepted=QtWidgets.QInputDialog.getMultiLineText(self.root.qt,text("输入分享码","Enter share code"),"FFC2:")
        if not accepted or not value.strip():return
        try:
            from film_foundry.app.seasonal_collection import try_unlock
            if try_unlock(value):
                self._refresh_all_preset_combos()
                self.status.set(text("四季合集已解锁：8 款材料与专属扫描。","Seasons unlocked: 8 materials and their matching scans."))
            else:
                from film_foundry.app.preset_exchange import read_items
                from film_foundry.ui.preset_exchange import ReceivePresetsDialog
                self._present_workspace_dialog(ReceivePresetsDialog(read_items(value.strip()),self.root.qt,
                    on_apply=self._apply_shared_presets,can_apply=self._can_apply_preset,on_saved=self._preset_library_changed))
        except (ValueError,TypeError,OSError) as exc:
            QtWidgets.QMessageBox.warning(self.root.qt,text("分享码","Share code"),str(exc))

    def _on_film_preset_display_selected(self, _event=None):
        self._applying_recommendations=True
        try:super()._on_film_preset_display_selected(_event)
        finally:self._applying_recommendations=False
        self._apply_recommended_development()
        self._apply_matching_scanner()
        self.control_resets.capture(self.pages[self.page_index['film']].qt)

    def _apply_matching_scanner(self):
        if not self.link_scanner.get() or self.pipeline_mode.get()=='scan':return
        from film_foundry.app.preset_pairing import recommended_scanner
        from film_foundry.core.media import developed_medium_contract
        config=self._config()
        key=recommended_scanner(self.film_preset.get(),config)
        self.scanner_preset.set(key)
        negative=developed_medium_contract(config)['image_polarity']=='negative'
        self.scan_invert.set(negative);self.scan_remove_base.set(negative)
        self.control_resets.capture(self.pages[self.page_index['scan']].qt)
        self._preview_edited()

    def _on_scanner_preset_display_selected(self,_event=None):
        self.link_scanner.set(False)
        super()._on_scanner_preset_display_selected(_event)
        path=legacy.scanner_preset_path(self.scanner_preset.get())
        if path.exists():
            requested=legacy.DarkroomConfig.from_json(path)
            mode=requested.scanner.interpretation_mode
            if mode in ('negative','positive','direct','manual'):
                from film_foundry.core.scan_runtime import configure_scan_interpretation
                configure_scan_interpretation(requested,mode)
                self.scan_invert.set(requested.scanner.invert_transmission);self.scan_remove_base.set(requested.scanner.remove_base_mask)
        self.control_resets.capture(self.pages[self.page_index['scan']].qt)

    def _on_develop_preset_display_selected(self,_event=None):
        self.auto_develop.set(False)
        super()._on_develop_preset_display_selected(_event)
        self._apply_matching_scanner()
        self.control_resets.capture(self.pages[self.page_index['develop']].qt)

    def _mark_manual_development(self):
        if self._auto_preview_ready and not self._applying_recommendations:self.auto_develop.set(False)

    def _apply_recommended_development(self):
        if not self.auto_develop.get() or self.pipeline_mode.get() not in ('full','develop','expose'):return
        self._applying_recommendations=True
        try:
            from film_foundry.app.workflow import recommended_standard_develop_preset
            self.develop_preset.set(recommended_standard_develop_preset(self._config()))
            self._principle_chemistry=None
            self.control_resets.capture(self.pages[self.page_index['develop']].qt)
        finally:self._applying_recommendations=False
        self._preview_edited()

    def _session_payload(self):
        payload=super()._session_payload()
        payload.setdefault('ui_options',{}).update(link_scanner=self.link_scanner.get(),auto_develop=self.auto_develop.get())
        return payload

    def _apply_session_payload(self,payload):
        super()._apply_session_payload(payload)
        options=payload.get('ui_options',{})
        self.link_scanner.set(bool(options.get('link_scanner',False)));self.auto_develop.set(bool(options.get('auto_develop',False)))

    def _open_movie_master(self):
        from film_foundry.ui.movie_master_dialog import MovieMasterDialog
        try: dialog=MovieMasterDialog(self._config(),self.root.qt)
        except (ValueError,TypeError) as exc:
            QtWidgets.QMessageBox.warning(self.root.qt,text("母带设置","Master settings"),str(exc));return
        self._present_workspace_dialog(dialog)

    def _build_compute(self,page):
        g=self.group(page,"处理质量","Processing quality")
        self.toggles(g,[("run_as_preview","preview_output"),("fast_mode","fast_mode")])
        quality=self.choice(g,tr("label.quality"),self.quality_mode,("draft","standard","high"),2)
        quality.set_tooltip(text(
            "低档降低内部细节采样，保留全部已开启的现象；中档平衡细节与速度；高档保留更多细节。此选项不缩小输出，也不启用降阶算法。自定义模块尺寸优先。",
            "Draft uses coarser internal detail sampling while keeping all enabled phenomena; standard balances detail and speed; high retains more detail. This setting does not resize the output or enable reduced algorithms. Explicit module sizes take precedence."))
        from film_foundry.ui.precision_labels import PRECISION_TOOLTIP
        quality.set_tooltip(quality.qt.toolTip()+'\n'+text(*PRECISION_TOOLTIP))
        g=self.group(page,"正式处理边长","Processing dimensions")
        self.sliders(g,[("preview_long_edge","preview_long_edge",0,12000,0)])
        g.set_tooltip(text("缩放、降阶和采样档位独立组合。预览不改变正式设置。", "Scaling, reduction and sampling can be combined independently. Preview keeps export settings."))
        g=self.group(page,"随机性与身份","Randomness & identity",key="compute.random",collapsed=True)
        self.seed_strategy_display.set(seed_strategy_label(self.seed_strategy.get()))
        self.choice(g,tr("label.seed_strategy"),self.seed_strategy_display,tuple(seed_strategy_labels().values()),callback=self._on_seed_strategy_selected)
        ui.Label(g,text=tr("label.random_seed")).grid(row=1,column=0,sticky="w")
        self.random_seed_entry=ui.TextInput(g,textvariable=self.random_seed);self.random_seed_entry.grid(row=1,column=1,columnspan=3,sticky="ew");self._update_seed_strategy_state()
        g._ensure_grid_layout().addWidget(self.control_resets.button(g.qt,self.random_seed,self.random_seed.get,self.random_seed.set),1,4)
        ui.Button(page,text=tr("button.runtime_settings"),command=self._open_runtime_settings).pack(fill="x",pady=6)

    def _build_resources(self,page):
        g=self.group(page,"材料与药水配置","Material & chemistry editors")
        for row,(label,module) in enumerate([("film_editor","run_film_material_editor"),("develop_editor","run_develop_process_editor"),("scanner_editor","run_scanner_render_editor")]):
            ui.Button(g,text=tr("button."+label),command=lambda m=module:self._open_editor("film_foundry.tools."+m)).grid(row=row,column=0,columnspan=4,sticky="ew",pady=4)
        g.set_tooltip(text("在主窗口打开完整配置编辑器，保存或显式应用工作副本；切换保留当前工作。冻结来源不可覆盖。", "Open the full editor in the main window. Save or explicitly apply the working copy; switching retains the current work. Frozen sources cannot be overwritten."))
        g=self.group(page,"档案与物理画幅","Profiles & physical frame",key="resources.geometry",collapsed=True)
        self._text_row(g,tr("label.spatial_reference_mm"),self.spatial_reference_mm,0);self._text_row(g,tr("label.frame_long_edge_mm"),self.frame_long_edge_mm,1)
        self.resource_source_group=g
        self._build_exposure_spectrum(page)
        for key in ("spectral_material_profile","spectral_observation_profile"):
            h=self.group(page,tr("profile."+key),tr("profile."+key),key="resources."+key,collapsed=True)
            self.control_resets.add(h.qt,key,lambda k=key:self._profiles[k],lambda value,k=key:self._set_profile(k,value))
            if key != "spectral_observation_profile":
                if not hasattr(self,"material_profile_groups"):self.material_profile_groups=[]
                self.material_profile_groups.append(h)
            h.set_tooltip(text("没有附带这类档案。不载入时，计算仍用材料自己的数据。", "No file of this kind is included. Leaving it unused keeps the material's own data."))
            ui.Label(h,textvariable=self._profile_labels[key],wraplength=380).grid(row=1,column=0,columnspan=4,sticky="w")
            ui.Button(h,text=text("载入自己的档案","Load your own file"),command=lambda k=key:self._load_profile(k)).grid(row=2,column=0,columnspan=2,sticky="ew")
            ui.Button(h,text=text("不使用额外档案","Use no extra profile"),command=lambda k=key:self._set_profile(k,None)).grid(row=2,column=2,columnspan=2,sticky="ew")
        g=self.group(page,"保存与刷新预设","Save & refresh presets")
        for row,key,callback in [(0,"save_film_preset",self._save_film_preset),(1,"save_scanner_preset",self._save_scanner_preset),(2,"refresh_presets",self._refresh_all_preset_combos)]:
            ui.Button(g,text=tr("button."+key),command=callback).grid(row=row,column=0,columnspan=4,sticky="ew",pady=3)

    def _restore_incident_identity(self,value):
        self._incident_batch_seed=value
        if self._recipe_base is None:self._recipe_base=self._config()
        self._recipe_base.chemistry.incident_identity_seed=value

    def _build_exposure_spectrum(self, page):
        group=self.group(page,"曝光光谱升维","Exposure spectral lift")
        self.exposure_spectrum_group=group
        group.set_tooltip(text("没有附带光谱 JSON。不开启时，材料继续用自带感光。七色升维是内置算法，打开即可，不必先找文件。", "No spectrum JSON is included. Until you turn this on, the material keeps its own sensitivity. The seven-color lift is built in and does not need a file."))
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
        self.material_filter_row.set_tooltip(text('筛选下方材料列表；保留当前已选材料与参数。','Filter the material list below; keep the selected material and its settings.')+('\n'+' / '.join(labels) if labels else ''))

    def _select_mode(self):
        key=self.mode_keys.get(self.mode_display.get())
        if key is None:return
        previous=self.pipeline_mode.get()
        if key=='scan' and previous in ('full','develop','expose'):
            self.negative_path.set(self.input_path.get())
            self.scan_invert.set(False);self.scan_remove_base.set(False)
        elif previous=='scan' and key in ('full','develop','expose') and self._is_photo_workflow():
            self.input_path.set(self.negative_path.get())
            self.scan_invert.set(self._expected_scan_polarity()=='negative')
            self.scan_remove_base.set(True)
        self.pipeline_mode.set(key);self._update_mode_visibility()
        if key=='expose':self.navigation.setCurrentRow(self.page_index['film'])
        elif key in ('develop','develop_latent'):self.navigation.setCurrentRow(self.page_index['develop'])
        elif key=='scan':self.navigation.setCurrentRow(self.page_index['scan'])
        self._navigate_workspace(self.navigation.currentRow())
        self._preview_edited()

    def _preview_zoom_changed(self, _percent=None):
        view=self.preview_tabs.currentWidget()
        self.preview_zoom_label.setText(f"{view.zoom_percent}%" if view is not None and view.source is not None else "—")

    def _set_process_button_mode(self, mode):
        super()._set_process_button_mode(mode)
        if mode not in ("cancel","cancelling") and self.process_button is not None and self.process_button.winfo_exists():
            labels={"full":text("冲洗并扫描","Develop & scan"),"develop":text("冲洗并保存介质","Develop medium"),
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
        expert=bool(self.expert_mode.get());principle=expert and self._expert_operating_mode=='principle'
        if hasattr(self,'expert_operation'):self.expert_operation.setVisible(expert)
        if hasattr(self,'principle_group'):
            self.principle_group.qt.setVisible(principle)
            for group in (self.develop_source_group,self.develop_effect_group):group.qt.setVisible(not principle)
            for group in (self.develop_detail_group,self.develop_identity_group,*self.develop_additional_groups):group.qt.setVisible(expert and not principle)

    def _set_expert_operation(self,*_):
        self._expert_operating_mode=self.expert_operation.currentData()
        self._update_expert_visibility();self._preview_edited()

    def _edit_principle_process(self):
        from film_foundry.ui.roll_process_dialog import RollProcessDialog
        try:
            existing=self.workspace_host.find_page('principle-process')
            if existing is not None:self.workspace_host.setCurrentWidget(existing);return
            dialog=RollProcessDialog(self._config(),self.root.qt)
            dialog.setWindowTitle(text('原理模式 · 药浴与步骤','Process principles · Baths & steps'))
            def apply():
                self._principle_chemistry=copy.deepcopy(dialog.config.chemistry);self._preview_edited()
            self._present_workspace_dialog(dialog,key='principle-process',on_accept=apply)
        except ValueError as exc:self.status.set(str(exc))

    def _edit_current_process(self):
        from film_foundry.ui.roll_process_dialog import RollProcessDialog
        try:
            existing=self.find_configuration_dialog('current-process')
            if existing is not None:existing.raise_();return
            dialog=RollProcessDialog(self._config(),self.root.qt,principle_only=False)
            def apply():
                config=self._config();config.chemistry=copy.deepcopy(dialog.config.chemistry)
                self._apply_loaded_config(config)
            self._present_workspace_dialog(dialog,key='current-process',on_accept=apply)
        except (ValueError,TypeError) as exc:self.status.set(str(exc))

    def _effect_inspection_config(self):
        shape=(512,512)
        if self.input_view.source is not None:shape=(self.input_view.source.height(),self.input_view.source.width())
        source=Path(self.input_path.get())
        if source.is_file() and self.pipeline_mode.get() not in ('develop_latent','scan'):
            from PIL import Image
            with Image.open(source) as image:
                width,height=image.size
                if image.getexif().get(274) in (5,6,7,8):width,height=height,width
                shape=(height,width)
        config=self._config()
        if self.pipeline_mode.get()=='develop_latent':
            from film_foundry.core.latent_io import inspect_latent_material
            material,geometry,shape=inspect_latent_material(Path(self.negative_path.get()))
            config.film=legacy.DarkroomConfig.from_dict({'film':material.parameters()}).film
            config.film.spatial_reference_short_edge_mm=geometry.reference_short_edge_mm if geometry else None
            config.chemistry.frame_long_edge_mm=geometry.frame_long_edge_mm if geometry else None
        return config,shape

    def _inspect_effect_couplings(self):
        from film_foundry.ui.process_couplings_dialog import ProcessCouplingsDialog
        try:
            config,_=self._effect_inspection_config()
            self._present_workspace_dialog(ProcessCouplingsDialog(config,self.root.qt))
        except (ValueError,OSError) as exc:self.status.set(str(exc))

    def _inspect_effect_intent(self):
        from film_foundry.core.effect_intent import compile_effect_intent
        from film_foundry.ui.roll_process_dialog import RollProcessDialog
        try:
            existing=self.workspace_host.find_page('effect-process')
            if existing is not None:self.workspace_host.setCurrentWidget(existing);return
            config,shape=self._effect_inspection_config()
            config,audit=compile_effect_intent(config,shape)
            dialog=RollProcessDialog(config,self.root.qt)
            dialog.setWindowTitle(text('期望效果 · 实际工艺参数','Expected effects · Actual process parameters'))
            targets=(audit or {}).get('targets',{});notes=[]
            if 'silver_unreachable' in targets:notes.append(text('材料不响应还原，无法形成期望析银。','This material cannot reduce silver; the requested deposit is unavailable.'))
            if 'damage_unreachable' in targets:notes.append(text('可用温差无法越过材料损伤阈值。','The available temperature range cannot reach the material damage threshold.'))
            if 'retention_unreachable' in targets:notes.append(text('当前工艺无法通过减少漂白增加留银。','This process cannot increase retained silver by reducing bleach activity.'))
            if 'retention_note' in targets:notes.append(text('留银按末次显影后的参考银量标定；局部浸润和层选择性会改变实际影像比例。','Retention is calibrated to metal after final development; local contact and layer selectivity can alter image-weighted retention.'))
            if 'dye_unreachable' in targets:notes.append(text('当前材料／工艺不能产生期望染料损伤。','The material/process cannot produce the requested dye loss.'))
            if 'uneven_reachable_maximum' in targets:notes.append(text('已按抑制剂最大可达响应缩放。','Scaled to the maximum reachable inhibition response.'))
            if 'uneven_note' in targets:notes.append(text('已有携带液或清洗可能改变标定响应。','Existing carryover or washing may change the calibrated response.'))
            if notes:dialog.status.setText(' '.join(notes))
            def apply():
                self._principle_chemistry=copy.deepcopy(dialog.config.chemistry)
                self.expert_mode.set(True);self.expert_operation.setCurrentIndex(self.expert_operation.findData('principle'))
            self._present_workspace_dialog(dialog,key='effect-process',on_accept=apply)
        except (ValueError,OSError) as exc:self.status.set(str(exc))

    def _sync_effect_mapping_ui(self):
        if not hasattr(self,'effect_upgrade_button'):return
        from film_foundry.contracts.effect_intent import MODEL_V3
        self.effect_upgrade_button.qt.setVisible(self._legacy_effect_controls or self._effect_intent_model!=MODEL_V3)
        self.effect_upgrade_button.configure(text=text('将旧版事故转换为工艺映射','Convert legacy incidents to process mapping') if self._legacy_effect_controls else text('升级留银与染料工艺映射','Upgrade retention and dye process mapping'))
        self.effect_master_control.qt.setEnabled(not self._legacy_effect_controls)
        self.effect_inspector_button.qt.setEnabled(not self._legacy_effect_controls)
        self.effect_upgrade_button.set_tooltip(text('当前保持旧版事故算法。转换保留滑杆值，但改为材料量与真实操作映射，外观会改变；原配置文件不改写。','Legacy rendering is preserved. Conversion keeps slider values but changes their meaning to material quantities and operations; appearance will change. The original file is untouched.'))
        self.effect_master_control.set_tooltip(text('当前控制六项期望效果；手工条件产生的关联后果仍保留。','Controls six desired effects; consequences of manual conditions remain.') if self._effect_intent_model==MODEL_V3 else text('旧版意图保持原控制范围；新增项需点击升级才纳入总开关。','Legacy intent keeps its control scope; upgrade to include new controls.'))

    def _upgrade_effect_controls(self):
        self._effect_intent_model='process_effect_intent_v3'
        self._legacy_effect_controls=False;self.effect_intent_enabled.set(True)
        self._sync_effect_mapping_ui();self._preview_edited()

    def _load_effect_controls(self,recipe):
        from film_foundry.contracts.effect_intent import EFFECTS_V3,MODEL_V3,validate_effect_intent
        intent=validate_effect_intent(recipe.effect_intent)
        self._effect_intent_model=intent['model'] if intent else MODEL_V3
        self._legacy_effect_controls=intent is None and any(getattr(recipe,key)!=0 for key in EFFECTS_V3)
        self.effect_intent_enabled.set(intent['enabled'] if intent else True)
        if intent:
            for key,value in intent['amounts'].items():getattr(self,key).set(value)
        self._sync_effect_mapping_ui()

    def _sync_develop_preset_controls(self):
        # A bath change must not replace the capture conditions or stock texture.
        names=('exposure_ev','negative_contrast','halation','halation_sensitivity','grain','grain_size','frame_size','light_leak','light_leak_template','light_leak_template_display','exposure_occlusion_dust','exposure_occlusion_hair','exposure_occlusion_insect','exposure_occlusion_debris')
        saved={name:getattr(self,name).get() for name in names}
        super()._sync_develop_preset_controls()
        for name,value in saved.items():getattr(self,name).set(value)
        path=legacy.develop_preset_path(self.develop_preset.get())
        if path.exists():
            self._load_effect_controls(legacy.DarkroomConfig.from_json(path).chemistry)
            # New explicitly scoped process presets own all of their declared
            # switches and formation controls. Historical bath presets retain
            # the older material/capture-preserving behaviour above.
            from film_foundry.app.preset_exchange import read_preset, is_scoped_process
            if hasattr(self,'color_mixer') and is_scoped_process(path):
                self._apply_shared_presets([read_preset(path,'develop')])

    def _update_scan_parameter_visibility(self, expert=None):
        self.scan_recommendation.set(tr("scan.recommendation.negative" if self._expected_scan_polarity() == "negative" else "scan.recommendation.positive"))
        key="scan.pipeline.manual."+("remove_" if self.scan_remove_base.get() else "preserve_")+("invert" if self.scan_invert.get() else "direct")
        self.scan_pipeline_description.set(tr(key))

    def _update_mode_visibility(self):
        if not hasattr(self,"pages"):return
        mode=self.pipeline_mode.get();capture=mode in ("full","develop","expose");develop=mode in ("full","develop","develop_latent");scan=mode in ("full","scan")
        choices=('full','develop','expose','scan') if self._is_photo_workflow() else (mode,)
        self.mode_choice.configure(values=tuple(self.workflow_labels[key] for key in choices))
        self.mode_display.set(self.workflow_labels[mode])
        self.sections['output.scan'].qt.setVisible(scan)
        self.sections['output.exports'].qt.setEnabled(mode!='expose')
        self.output_format_group.qt.setEnabled(mode!='expose')
        self.output_format_group.qt.setTitle(text('成片格式','Image format') if scan else text('材料预览图格式','Material preview format'))
        if hasattr(self,'film_border_group'):
            self.film_border_group.qt.setEnabled(capture)
            self.film_border_menu_action.setEnabled(capture)
            border=self._recipe_base.film_border if self._recipe_base is not None else None
            self.film_border_button.configure(text=text('片边已开启 · 配置…','Film edges enabled · Configure…') if border and border.enabled else text('启用片边与齿孔…','Enable film edges…'))
        for group in [self.film_source_group,self.film_condition_group,self.capture_exposure_group,self.film_gate_group,self.film_effect_group,self.film_imprint_group,self.resource_source_group,self.exposure_spectrum_group,*getattr(self,"material_profile_groups",[])]:group.qt.setEnabled(capture)
        for group in [self.develop_source_group,self.develop_effect_group,self.develop_detail_group,self.develop_identity_group,*self.develop_additional_groups]:group.qt.setEnabled(develop)
        for name in ("halation","halation_sensitivity","halation_edge","emulsion_mtf","artifact_suppression","enable_mtf","enable_halation"):
            widgets=self.fields[name]
            for widget in widgets if isinstance(widgets,tuple) else (widgets,):widget.qt.setEnabled(capture)
        self.pages[self.page_index["scan"]].qt.setEnabled(scan)
        self._refresh_source_summary()
        self._refresh_scanner_catalog();self._update_scan_parameter_visibility();self._update_expert_visibility()
        if not self._processing:self._set_process_button_mode("process")
        descriptions={
            "output":text("来源信息与输出内容；执行时选择保存位置","Source information and output content; choose the destination when running"),
            "film":text("材料、画幅与曝光条件" if capture else "沿用来源中已保存的材料与曝光条件", "Stock, format and exposure" if capture else "Stock and exposure are inherited from the saved source"),
            "develop":text("选择程序，调整药浴与材料反应" if develop else "本次只曝光，稍后可冲洗潜影" if mode=="expose" else "介质已经冲洗，本次只读取与观察", "Choose a process and adjust the bath" if develop else "Expose now, develop the latent later" if mode=="expose" else "The medium is already developed; this task only observes it"),
            "scan":text("只读观察介质，调整成片" if scan else "本次保存材料；可在扫描任务中继续成片", "Observe the medium and adjust the image" if scan else "This task saves material; choose Scan to make an image later"),
            "compute":text("正式处理的尺寸、质量与资源设置","Dimensions, quality and resources for formal processing"),
            "resources":text("编辑材料、药水与扫描配置","Edit material, chemistry and scanner definitions"),
        }
        for key,title in self.page_notes.items():
            title.set_tooltip(descriptions[key])
            self.navigation.item(self.page_index[key]).setToolTip(descriptions[key])
        self.mode_choice.set_tooltip(tr("status."+mode))
        if not self._processing:self.status.set("")

    def _is_photo_workflow(self):
        if self._active_roll is not None:return False
        if self.pipeline_mode.get() in ('full','develop','expose'):return True
        from film_foundry.core.io_utils import SUPPORTED_EXTENSIONS
        return self.pipeline_mode.get()=='scan' and Path(self.negative_path.get()).suffix.lower() in SUPPORTED_EXTENSIONS

    def _refresh_source_summary(self):
        if self._active_roll:
            roll=self._active_roll.data['roll']
            self.source_summary.configure(text=text('整卷：','Roll: ')+f"{roll.get('label') or roll['roll_id']} · {roll['capacity']}")
            return
        mode=self.pipeline_mode.get()
        frozen=mode in ("scan","develop_latent")
        source=self.negative_path.get() if frozen else self.input_path.get()
        name=Path(source).name if source else text("未选择", "Not selected")
        self.source_summary.configure(text=text("来源 · ", "Source · ")+name)
        if self._is_photo_workflow() and len(self._selected_photos)>1:
            self.source_summary.configure(text=text(f'已选 {len(self._selected_photos)} 张 · ',f'{len(self._selected_photos)} selected · ')+name)
        self.source_summary.qt.setToolTip(str(source))

    def _show_source_information(self):
        dialog=QtWidgets.QDialog(self.film_workspace);dialog.setWindowTitle(text('来源信息','Source information'))
        layout=QtWidgets.QVBoxLayout(dialog)
        paths=self._selected_photos if self._is_photo_workflow() and self._selected_photos else [self.negative_path.get() if self.pipeline_mode.get() in ('scan','develop_latent') else self.input_path.get()]
        view=QtWidgets.QPlainTextEdit('\n'.join(paths));view.setReadOnly(True);layout.addWidget(view)
        button=QtWidgets.QPushButton(text('返回','Back'));button.clicked.connect(dialog.reject);layout.addWidget(button)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.film_workspace,dialog)

    def _config(self):
        config=super()._config()
        for key,editor in getattr(self,"curve_editors",{}).items():setattr(config.scanner,key,editor.read())
        if hasattr(self,"color_mixer"):config.scanner.color_mixer=self.color_mixer.read()
        config.process_control_mode=self._expert_operating_mode if self.expert_mode.get() else 'normal'
        if config.process_control_mode!='principle' and not self._legacy_effect_controls:
            from film_foundry.contracts.effect_intent import effects_for_model
            keys=effects_for_model(self._effect_intent_model)
            config.chemistry.effect_intent={'model':self._effect_intent_model,'enabled':bool(self.effect_intent_enabled.get()),
                'amounts':{key:float(getattr(config.chemistry,key)) for key in keys}}
        elif config.process_control_mode!='principle':config.chemistry.effect_intent=None
        if config.process_control_mode=='principle':
            if self._principle_chemistry is not None:
                capture=config.chemistry;config.chemistry=copy.deepcopy(self._principle_chemistry)
                for name in capture.__dataclass_fields__:
                    if name.startswith(('light_leak_','exposure_occlusion_')) or name=='incident_identity_seed':
                        setattr(config.chemistry,name,copy.deepcopy(getattr(capture,name)))
            from film_foundry.app.darkroom_process import condition_driven_config
            config=condition_driven_config(config)
        return config

    def _open_medium_viewer(self):
        from film_foundry.ui.roll_strips import RollLightTableDialog
        path,_=QtWidgets.QFileDialog.getOpenFileName(self.root.qt,text('打开已冲洗介质','Open developed medium'),self.input_path.get(),'Film medium (*.npz)')
        if not path:return
        key='light-table:'+str(Path(path).resolve());window=self.workspace_host.find_page(key)
        if window is not None:self.workspace_host.setCurrentWidget(window);return
        window=RollLightTableDialog(parent=self.root.qt,medium_path=path)
        self._present_workspace_dialog(window,text('灯台 · ','Light table · ')+Path(path).name,key=key)

    def _apply_loaded_config(self,config):
        from film_foundry.app.camera_profile import preserve_legacy_camera_identity
        config=preserve_legacy_camera_identity(config)
        ready=self._auto_preview_ready;self._auto_preview_ready=False
        try:
            super()._apply_loaded_config(config)
            self._load_effect_controls(config.chemistry)
            if config.process_control_mode=='principle':self._principle_chemistry=copy.deepcopy(config.chemistry)
            self._expert_operating_mode=config.process_control_mode if config.process_control_mode!='normal' else 'expected'
            self.expert_mode.set(config.process_control_mode!='normal')
            if hasattr(self,'expert_operation'):
                self.expert_operation.blockSignals(True);self.expert_operation.setCurrentIndex(self.expert_operation.findData(self._expert_operating_mode));self.expert_operation.blockSignals(False)
            self.color_mixer.load(config.scanner.color_mixer)
            for key,editor in self.curve_editors.items():editor.load(getattr(config.scanner,key))
            self._update_curve_summary()
            self._update_mode_visibility()
            self.result_view.set_rotation(config.output.rotation_quarters)
            self.link_scanner.set(False);self.auto_develop.set(False)
            self.control_resets.capture()
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
            self.color_mixer.load(scanner.color_mixer)
            for key,editor in self.curve_editors.items():editor.load(getattr(scanner,key))
            self._update_curve_summary()

    def _apply_editor_bank(self,bank,edited):
        mode=self.pipeline_mode.get()
        if bank == "film" and mode in ("scan","develop_latent") or bank == "develop" and mode in ("scan","expose"):
            self.status.set(text("此任务的来源已冻结；请保存配置供新任务使用。", "This task's source is frozen; save the configuration for a new task."));return
        super()._apply_editor_bank(bank,edited)
        self._refresh_formation_status()

    def _change_language(self,event=None):
        from film_foundry.ui.inline_editor import InlineEditorHost
        if any(host.frames for host in self.root.qt.findChildren(InlineEditorHost)):
            self.language.set(language_label(current_language()))
            self.status.set(text('请先应用或返回当前编辑页，再切换语言','Apply or leave the current editor before changing language'))
            return
        self.edit_tools.record();edit_history=self.edit_tools.history
        pairing=(self.link_scanner.get(),self.auto_develop.get())
        reset_baselines={key:copy.deepcopy(value) for key,value in self.control_resets.baselines.items()}
        comparison=(self.result_view.original,self.result_view.result,self.result_view.mode,self.result_view.divider,self.result_view.rotation_quarters)
        ready=self._auto_preview_ready;self._auto_preview_ready=False
        # Save active and disabled point drafts before rebuilding native controls.
        mixer_draft=self.color_mixer.read()
        drafts={key:(copy.deepcopy(editor.points),editor.enabled.isChecked(),editor.channel.currentIndex()) for key,editor in self.curve_editors.items()}
        images=(self.input_view.source,self.result_view.source,self.inspector_view.source,self.preview_caption.text())
        size=self.root.qt.size();split_sizes=self.workspace_splitter.sizes()
        scroll_positions=[self.studio_moved.get(index,self.stack.widget(index)).verticalScrollBar().value() for index in range(self.stack.count())]
        page=self.navigation.currentRow();self.material_profile_groups=[]
        active=self.workspace_host.currentWidget()
        was_film=active is self.film_workspace
        library,roll,editors=self.library_view,self.roll_view,self.editor_host
        editor_tabs=getattr(self,'editor_tabs',None)
        preserved=self.workspace_host.detach_pages(self.root.qt,exclude=(self.film_workspace,))
        embedded=list(self._embedded_editor_keys)
        active_editor=next((bank for bank in embedded if self.editor_host is not None and
                            self._editor_windows[bank].root.qt is self.editor_tabs.currentWidget()),None)
        for bank in embedded:
            editor=self._editor_windows.get(bank)
            if editor and editor.root.winfo_exists():
                editor.root.qt.setParent(self.root.qt,QtCore.Qt.WindowType.Window);editor.root.qt.hide()
        super()._change_language(event)
        self.edit_tools.history=edit_history;self.edit_tools.sync()
        self.library_view,self.roll_view,self.editor_host=library,roll,editors
        if editor_tabs is not None:self.editor_tabs=editor_tabs
        for widget,title,key,closable in preserved:
            title=text(*{'library':('图库','Library'),'roll':('整卷暗房','Roll darkroom'),'editors':('配置','Configuration')}[key]) if key in ('library','roll','editors') else title
            self.workspace_host.add_page(widget,title,key=key,closable=closable)
        if library is not None:self.workspace_host.tabBar().moveTab(self.workspace_host.indexOf(library),0)
        for bank in embedded:self.open_embedded_editor(bank)
        if active_editor:self.open_embedded_editor(active_editor)
        if was_film:self.show_workbench()
        elif active is not None:self.workspace_host.setCurrentWidget(active)
        for key,user_dir,builtin_dir in (("film",legacy.USER_FILM_PRESET_DIR,legacy.FILM_PRESET_DIR),("develop",legacy.USER_DEVELOP_PRESET_DIR,legacy.DEVELOP_PRESET_DIR),("scanner",legacy.USER_SCANNER_PRESET_DIR,legacy.SCANNER_PRESET_DIR)):
            getattr(self,key+"_preset_source").set(legacy.preset_source_text(getattr(self,key+"_preset").get(),user_dir,builtin_dir))
        self.color_mixer.load(mixer_draft)
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
                self.studio_moved.get(index,self.stack.widget(index)).verticalScrollBar().setValue(position)
        self.root.after(0,restore_layout)
        self.input_view.source,self.result_view.source=images[:2]
        self.input_view.fit();self.result_view.fit()
        self.result_view.original,self.result_view.result,self.result_view.mode,self.result_view.divider,self.result_view.rotation_quarters=comparison
        self.result_view._aligned=None;self.result_view._present()
        self.edit_tools.comparison.setCurrentIndex(self.edit_tools.comparison.findData(comparison[2]))
        if any(image is not None for image in images[:2]):self.preview_caption.setText(images[3])
        self.inspector_view.source=images[2];self.inspector_view.fit()
        self.preview_tabs.setTabVisible(2,images[2] is not None)
        self._save_display_preferences(language=current_language())
        self.link_scanner.set(pairing[0]);self.auto_develop.set(pairing[1])
        self.control_resets.baselines.update(reset_baselines)
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
        self.input_view.show_array(left);self.result_view.set_pair(left,right)
        self.result_view.set_rotation(self._config().output.rotation_quarters)
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

