"""Small global preferences dialog; workspace defaults apply to new work only."""
from pathlib import Path
from film_foundry.app.desktop_settings import load_settings, save_settings
from film_foundry.ui.qt import QtWidgets, QtGui
from film_foundry.ui.curve_editor import text


class DesktopSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, clear_cache=None, change_theme=None, change_language=None, change_preview=None):
        super().__init__(parent)
        self.setWindowTitle(text('全局设置', 'Global settings'));self.resize(760, 620)
        self.values = load_settings()
        root = QtWidgets.QVBoxLayout(self)
        self.tabs=tabs = QtWidgets.QTabWidget();root.addWidget(tabs)
        locations = QtWidgets.QWidget();tabs.addTab(locations, text('工作区与图库', 'Workspace & library'))
        layout = QtWidgets.QVBoxLayout(locations)
        form = QtWidgets.QFormLayout();layout.addLayout(form)
        self.workspace = QtWidgets.QLineEdit(self.values['workspace'])
        row = QtWidgets.QHBoxLayout();row.addWidget(self.workspace)
        choose = QtWidgets.QPushButton(text('选择…', 'Choose…'));row.addWidget(choose)
        choose.clicked.connect(self.choose_workspace)
        form.addRow(text('工作区目录', 'Workspace folder'), row)
        self.workspace.setToolTip(text('会话保存在 sessions，默认结果保存在 outputs/日期。更改目录不搬动已有文件。',
            'Sessions go in sessions; default results go in outputs/date. Existing files are not moved.'))
        layout.addWidget(QtWidgets.QLabel(text('图库来源文件夹', 'Library source folders')))
        self.folders = QtWidgets.QListWidget();self.folders.addItems(self.values['source_folders']);layout.addWidget(self.folders,1)
        row = QtWidgets.QHBoxLayout();layout.addLayout(row)
        add = QtWidgets.QPushButton(text('添加文件夹…', 'Add folder…'));add.clicked.connect(self.add_folder);row.addWidget(add)
        remove = QtWidgets.QPushButton(text('移除来源', 'Remove source'));remove.clicked.connect(lambda:self.folders.takeItem(self.folders.currentRow()));row.addWidget(remove);row.addStretch()
        self.folders.setToolTip(text('递归索引图片，不复制原文件；移除来源停止后续检索，已入库图片可在图库单独移除。',
            'Index recursively without copying. Removing a folder stops indexing; remove existing entries in the library.'))
        defaults = QtWidgets.QWidget();tabs.addTab(defaults,text('新工作默认值','New work defaults'))
        form = QtWidgets.QFormLayout(defaults)
        self.fast = QtWidgets.QCheckBox(text('启用降阶', 'Enable reduced formation'));self.fast.setChecked(self.values['fast_mode']);form.addRow(text('胶片形成','Film formation'),self.fast)
        self.quality = QtWidgets.QComboBox()
        for key,zh,en in (('draft','低','Low'),('standard','中','Medium'),('high','高','High')):self.quality.addItem(text(zh,en),key)
        self.quality.setCurrentIndex(self.quality.findData(self.values['quality']));form.addRow(text('处理质量','Processing quality'),self.quality)
        self.mode = QtWidgets.QComboBox()
        for key,zh,en in (('normal','常用','Common'),('expected','按效果','By effect'),('principle','按药浴','By bath')):self.mode.addItem(text(zh,en),key)
        self.mode.setCurrentIndex(self.mode.findData(self.values['expert_mode'] if self.values['expert'] else 'normal'));form.addRow(text('冲洗控制','Development controls'),self.mode)
        self.resize_enabled = QtWidgets.QCheckBox(text('限制形成长边','Limit formation long edge'));self.resize_enabled.setChecked(self.values['resize']);form.addRow(self.resize_enabled)
        self.edge = QtWidgets.QSpinBox();self.edge.setRange(64,65536);self.edge.setSuffix(' px');self.edge.setValue(self.values['long_edge']);form.addRow(text('默认长边','Default long edge'),self.edge)
        self.edge.setEnabled(self.resize_enabled.isChecked());self.resize_enabled.toggled.connect(self.edge.setEnabled)
        self.policy = QtWidgets.QComboBox()
        for key,zh,en in (('ask','询问保存／不保存','Ask: save or discard'),('save','自动保存工作','Always save work'),('discard','不保存，不再询问','Discard without asking')):self.policy.addItem(text(zh,en),key)
        self.policy.setCurrentIndex(self.policy.findData(self.values['leave_policy']));form.addRow(text('关闭或替换工作','Closing or replacing work'),self.policy)
        defaults.setToolTip(text('形成默认值只用于新建胶片工作；已有会话、其他实验材料与扫描设置保留。',
            'Formation defaults apply only to new film work. Existing sessions, experimental media and scanning retain their settings.'))
        appearance = QtWidgets.QWidget();tabs.addTab(appearance,text('外观与布局','Appearance & layout'))
        layout = QtWidgets.QVBoxLayout(appearance)
        from film_foundry.ui.theme import current_theme, apply_theme, THEME_KEYS, saved_custom_colors, text_on
        from film_foundry.ui.i18n import current_language,language_label,tr,available_languages
        from film_foundry.ui.preferences import save_preferences
        form=QtWidgets.QFormLayout();layout.addLayout(form)
        self.theme=QtWidgets.QComboBox()
        for key in THEME_KEYS:self.theme.addItem(tr('theme.'+key),key)
        self.theme.setCurrentIndex(self.theme.findData(current_theme()));form.addRow(text('主题','Theme'),self.theme)
        custom_row = QtWidgets.QWidget()
        custom_layout = QtWidgets.QHBoxLayout(custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        form.addRow(custom_row)
        self.color_buttons = {}
        labels = {"background": text("工作区底色", "Workspace color"), "accent": text("强调色", "Accent color")}

        def refresh_swatches():
            for role, color in saved_custom_colors().items():
                button = self.color_buttons[role]
                ink = text_on(color)
                button.setText(f"{labels[role]}  {color.upper()}")
                button.setStyleSheet(f"QPushButton {{background:{color};color:{ink};border:1px solid {ink};}} "
                                     f"QPushButton:focus {{border:2px solid {ink};}}")
            custom_row.setVisible(self.theme.currentData() == "custom")

        def edit_color(role):
            values = saved_custom_colors()
            chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(values[role]), self, labels[role])
            if not chosen.isValid():
                return
            values[role] = chosen.name()
            try:
                save_preferences(custom_colors=values)
            except OSError as exc:
                self.appearance_status.setText(str(exc))
                return
            apply_theme("custom")
            refresh_swatches()

        for role in labels:
            button = QtWidgets.QPushButton()
            button.clicked.connect(lambda checked=False, role=role: edit_color(role))
            custom_layout.addWidget(button)
            self.color_buttons[role] = button
        refresh_swatches()
        def apply_theme_choice():
            key=self.theme.currentData()
            if change_theme:change_theme(key)
            else:apply_theme(key);save_preferences(theme=key)
            refresh_swatches()
        self.theme.currentIndexChanged.connect(apply_theme_choice)
        self.language=QtWidgets.QComboBox()
        for key in available_languages():self.language.addItem(language_label(key),key)
        self.language.setCurrentIndex(self.language.findData(current_language()))
        self.language.setEnabled(change_language is not None)
        form.addRow(text('语言','Language'),self.language)
        self.appearance_status=QtWidgets.QLabel(text('外观即时应用并保存。语言更新主工作台；已打开的材料页、编辑器和设置窗口重开后使用新语言。',
            'Appearance applies and saves immediately. Language updates the main workbench; reopen existing material pages, editors and settings to translate them.'))
        self.appearance_status.setWordWrap(True);layout.addWidget(self.appearance_status)
        def apply_language_choice():
            if change_language and not change_language(self.language.currentData()):
                self.appearance_status.setText(text('请先应用或返回当前编辑页，再切换语言。','Apply or leave the current editor before changing language.'))
                self.language.blockSignals(True);self.language.setCurrentIndex(self.language.findData(current_language()));self.language.blockSignals(False)
        self.language.currentIndexChanged.connect(apply_language_choice)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.export_language_button=QtWidgets.QPushButton(text('导出翻译模板…','Export translation template…'));row.addWidget(self.export_language_button)
        self.import_language_button=QtWidgets.QPushButton(text('导入语言包…','Import language pack…'));row.addWidget(self.import_language_button)
        def export_language():
            from film_foundry.ui.language_packs import export_template
            path,_=QtWidgets.QFileDialog.getSaveFileName(self,text('导出翻译模板','Export translation template'),'translation.json','JSON (*.json)')
            if not path:return
            try:export_template(path,current_language())
            except (OSError,ValueError) as exc:self.appearance_status.setText(str(exc));return
            self.appearance_status.setText(text('模板已导出。修改语言代码、名称及 translations，保留占位符；空白译文使用回退语言。','Template exported. Edit code, name and translations; keep placeholders unchanged. Blank entries use the fallback language.'))
        def import_language():
            from film_foundry.ui.language_packs import import_pack
            path,_=QtWidgets.QFileDialog.getOpenFileName(self,text('导入语言包','Import language pack'),'','JSON (*.json)')
            if not path:return
            try:code=import_pack(path)
            except (OSError,ValueError,TypeError,RecursionError) as exc:self.appearance_status.setText(str(exc));return
            self.language.blockSignals(True);self.language.clear()
            for key in available_languages():self.language.addItem(language_label(key),key)
            self.language.setCurrentIndex(self.language.findData(current_language()));self.language.blockSignals(False)
            self.appearance_status.setText(text('语言包已导入，请在语言列表中选择。更新正在使用的语言包后，请重新打开程序。','Language pack imported. Select it in the language list. Restart the app after updating the active pack.'))
        self.export_language_button.clicked.connect(export_language);self.import_language_button.clicked.connect(import_language)
        note = QtWidgets.QLabel(text('拖动图像与控件之间的分隔条调整宽度；胶片直方图下沿、材料预览下沿也可拖动调节高度。窗口大小、位置与最大化状态，以及单侧 / 双侧面板比例会自动记忆。',
            'Drag between image and controls to adjust widths; drag below the film histogram or material preview to adjust heights. Window size, position, maximized state and compact / studio pane sizes are saved automatically.'))
        note.setWordWrap(True);layout.addWidget(note)
        self.reset_layout_button = QtWidgets.QPushButton(text('重置窗口与面板布局','Reset window and panel layouts'));layout.addWidget(self.reset_layout_button)
        self.layout_status = QtWidgets.QLabel(text('立即恢复默认尺寸与比例；保留当前单侧 / 双侧模式、材料参数和工作内容。',
            'Immediately restore default sizes and proportions; keep the current compact / studio mode, parameters and work.'))
        self.layout_status.setWordWrap(True);layout.addWidget(self.layout_status);layout.addStretch()
        def reset_layout():
            from film_foundry.ui.layout_memory import reset_layouts
            try:reset_layouts()
            except OSError as exc:self.layout_status.setText(str(exc));return
            self.layout_status.setText(text('已恢复默认窗口与面板布局。','Default window and panel layouts restored.'))
        self.reset_layout_button.clicked.connect(reset_layout)
        from film_foundry.ui.histogram_settings import HistogramSettingsPanel
        self.histogram_settings=HistogramSettingsPanel();tabs.addTab(self.histogram_settings,text('直方图','Histogram'))
        from film_foundry.ui.shortcut_settings import ShortcutSettingsPanel
        self.shortcut_settings=ShortcutSettingsPanel();tabs.addTab(self.shortcut_settings,text('快捷键','Shortcuts'))
        preview = QtWidgets.QWidget()
        tabs.addTab(preview, text('预览与恢复', 'Preview & recovery'))
        form = QtWidgets.QFormLayout(preview)
        from film_foundry.ui.preferences import load_preferences
        options = load_preferences().get('preview_options', {})
        def update(key, value):
            save_preferences(preview_options={key: value})
            if change_preview:
                change_preview(key, value)
        for key, zh, en, choices, default in (
            ('edge', '胶片预览长边', 'Film preview edge', (480, 640, 900, 1200, 1600), 900),
            ('debounce', '自动预览等待（毫秒）', 'Auto preview delay (ms)', (100, 200, 350, 500, 800), 350),
            ('history', '每页预览历史条数', 'History entries per page', (3, 6, 12, 24), 6),
            ('memory', '历史图像总预算（MiB）', 'History image budget (MiB)', (16, 32, 64, 128), 32),
            ('generations', '自动保险保留份数', 'Recovery generations', (1, 3, 5), 1),
            ('recovery_minutes', '历史保险间隔（分钟）', 'Recovery history interval (min)', (1, 5, 10, 30), 5)):
            choice = QtWidgets.QComboBox()
            for value in choices:
                choice.addItem(str(value), value)
            choice.setCurrentIndex(choice.findData(options[key] if options.get(key) in choices else default))
            choice.currentIndexChanged.connect(lambda _, c=choice, k=key: update(k, c.currentData()))
            form.addRow(text(zh, en), choice)
        maintenance = QtWidgets.QWidget();tabs.addTab(maintenance,text('缓存与软件信息','Cache & information'))
        layout = QtWidgets.QVBoxLayout(maintenance)
        self.cache_status = QtWidgets.QLabel();layout.addWidget(self.cache_status)
        from film_foundry.ui.image_cache import preview_images
        usage=preview_images.statistics()
        self.cache_status.setText(text('预览缓存：{count} 项，{size:.1f} / {limit:.0f} MiB',
            'Preview cache: {count} entries, {size:.1f} / {limit:.0f} MiB').format(count=usage['entries'],size=usage['bytes']/1048576,limit=usage['limit']/1048576))
        clear = QtWidgets.QPushButton(text('释放缩略图、预设与预览缓存','Release thumbnail, preset and preview caches'));layout.addWidget(clear)
        def release():
            if clear_cache:clear_cache()
            self.cache_status.setText(text('已释放可清理的内存缓存；工作与原文件保留。','Released available memory caches. Work and source files are retained.'))
        clear.clicked.connect(release)
        from film_foundry.app.thumbnail_cache import ThumbnailCache
        self.disk_cache_status=QtWidgets.QLabel();layout.addWidget(self.disk_cache_status)
        def update_disk_usage():
            usage=ThumbnailCache().statistics()
            self.disk_cache_status.setText(text('磁盘缩略图：{count} 项，{size:.1f} / {limit:.0f} MiB','Disk thumbnails: {count} entries, {size:.1f} / {limit:.0f} MiB').format(count=usage['entries'],size=usage['bytes']/1048576,limit=usage['limit']/1048576))
        update_disk_usage()
        self.clear_thumbnails_button=QtWidgets.QPushButton(text('清理磁盘缩略图','Clear disk thumbnails'));layout.addWidget(self.clear_thumbnails_button)
        def clear_thumbnails():
            try:ThumbnailCache().clear()
            except Exception as exc:self.disk_cache_status.setText(str(exc));return
            update_disk_usage()
        self.clear_thumbnails_button.clicked.connect(clear_thumbnails)
        note=QtWidgets.QLabel(text('包含选材、预设和图库小图。需要时自动重建；不包含原图、介质或“上一次工作”。','Includes material, preset and library thumbnails. Rebuilt as needed; excludes originals, media and Last work.'));note.setWordWrap(True);layout.addWidget(note)
        from film_foundry.identity import PROJECT_NAME
        layout.addWidget(QtWidgets.QLabel(PROJECT_NAME))
        about = QtWidgets.QPushButton(text('版本、许可与致谢…','Version, licenses & credits…'));layout.addWidget(about)
        from film_foundry.ui.about import show_about
        about.clicked.connect(lambda:show_about(self));layout.addStretch()
        self.status = QtWidgets.QLabel();self.status.setWordWrap(True);root.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText(text('保存工作区与默认值并关闭','Save workspace & defaults and close'))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text('关闭','Close'))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setToolTip(text('关闭窗口；不保存未提交的工作区、默认值或快捷键编辑。已应用的快捷键、外观、直方图与布局操作保留。',
            'Close without saving unsubmitted workspace, defaults or shortcut edits. Applied shortcuts, appearance, histogram and layout actions remain.'))
        buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);root.addWidget(buttons)

    def choose_workspace(self):
        value = QtWidgets.QFileDialog.getExistingDirectory(self,text('工作区目录','Workspace folder'),self.workspace.text())
        if value:self.workspace.setText(value)

    def add_folder(self):
        value = QtWidgets.QFileDialog.getExistingDirectory(self,text('图库来源','Library source'))
        if value and value not in [self.folders.item(i).text() for i in range(self.folders.count())]:self.folders.addItem(value)

    def apply(self):
        try:
            if not self.workspace.text().strip():raise ValueError(text('请选择工作区目录','Choose a workspace folder'))
            path=Path(self.workspace.text()).expanduser()
            if path.exists() and not path.is_dir():raise ValueError(text('工作区必须是目录','Workspace must be a folder'))
            value=load_settings()  # Preserve work history added while this dialog was open.
            value.update(workspace=str(path),source_folders=[self.folders.item(i).text() for i in range(self.folders.count())],
                fast_mode=self.fast.isChecked(),quality=self.quality.currentData(),expert=self.mode.currentData()!='normal',expert_mode=self.mode.currentData() if self.mode.currentData()!='normal' else self.values['expert_mode'],
                resize=self.resize_enabled.isChecked(),long_edge=self.edge.value(),leave_policy=self.policy.currentData())
            save_settings(value)
        except (ValueError,OSError) as exc:self.status.setText(str(exc));return
        self.accept()
