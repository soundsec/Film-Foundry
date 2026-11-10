"""Small global preferences dialog; workspace defaults apply to new work only."""
from pathlib import Path
from film_foundry.app.desktop_settings import load_settings, save_settings
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text


class DesktopSettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, *, clear_cache=None):
        super().__init__(parent)
        self.setWindowTitle(text('全局设置', 'Global settings'));self.resize(650, 560)
        self.values = load_settings()
        root = QtWidgets.QVBoxLayout(self)
        tabs = QtWidgets.QTabWidget();root.addWidget(tabs)
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
        self.expert = QtWidgets.QCheckBox(text('默认开启专家模式','Start in expert mode'));self.expert.setChecked(self.values['expert']);form.addRow(self.expert)
        self.mode = QtWidgets.QComboBox();self.mode.addItem(text('期望模式','Expected effects'),'expected');self.mode.addItem(text('原理模式','Process principles'),'principle')
        self.mode.setCurrentIndex(self.mode.findData(self.values['expert_mode']));form.addRow(text('专家模式导向','Expert controls'),self.mode)
        self.resize_enabled = QtWidgets.QCheckBox(text('限制形成长边','Limit formation long edge'));self.resize_enabled.setChecked(self.values['resize']);form.addRow(self.resize_enabled)
        self.edge = QtWidgets.QSpinBox();self.edge.setRange(64,65536);self.edge.setSuffix(' px');self.edge.setValue(self.values['long_edge']);form.addRow(text('默认长边','Default long edge'),self.edge)
        self.edge.setEnabled(self.resize_enabled.isChecked());self.resize_enabled.toggled.connect(self.edge.setEnabled)
        self.policy = QtWidgets.QComboBox()
        for key,zh,en in (('ask','询问保存／不保存','Ask: save or discard'),('save','自动保存工作','Always save work'),('discard','不保存，不再询问','Discard without asking')):self.policy.addItem(text(zh,en),key)
        self.policy.setCurrentIndex(self.policy.findData(self.values['leave_policy']));form.addRow(text('关闭或替换工作','Closing or replacing work'),self.policy)
        defaults.setToolTip(text('形成默认值只用于新建胶片工作；已有会话、其他实验材料与扫描设置保留。',
            'Formation defaults apply only to new film work. Existing sessions, experimental media and scanning retain their settings.'))
        maintenance = QtWidgets.QWidget();tabs.addTab(maintenance,text('缓存与软件信息','Cache & information'))
        layout = QtWidgets.QVBoxLayout(maintenance)
        self.cache_status = QtWidgets.QLabel();layout.addWidget(self.cache_status)
        from film_foundry.ui.image_cache import preview_images
        usage=preview_images.statistics()
        self.cache_status.setText(text(f"预览缓存：{usage['entries']} 项，{usage['bytes']/1048576:.1f} / {usage['limit']/1048576:.0f} MiB",
            f"Preview cache: {usage['entries']} entries, {usage['bytes']/1048576:.1f} / {usage['limit']/1048576:.0f} MiB"))
        clear = QtWidgets.QPushButton(text('释放缩略图、预设与预览缓存','Release thumbnail, preset and preview caches'));layout.addWidget(clear)
        def release():
            if clear_cache:clear_cache()
            self.cache_status.setText(text('已释放可清理的内存缓存；工作与原文件保留。','Released available memory caches. Work and source files are retained.'))
        clear.clicked.connect(release)
        from film_foundry.identity import PROJECT_NAME
        layout.addWidget(QtWidgets.QLabel(PROJECT_NAME))
        about = QtWidgets.QPushButton(text('版本、许可与致谢…','Version, licenses & credits…'));layout.addWidget(about)
        from film_foundry.ui.about import show_about
        about.clicked.connect(lambda:show_about(self));layout.addStretch()
        self.status = QtWidgets.QLabel();self.status.setWordWrap(True);root.addWidget(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText(text('保存','Save'))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text('取消','Cancel'))
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
                fast_mode=self.fast.isChecked(),quality=self.quality.currentData(),expert=self.expert.isChecked(),expert_mode=self.mode.currentData(),
                resize=self.resize_enabled.isChecked(),long_edge=self.edge.value(),leave_policy=self.policy.currentData())
            save_settings(value)
        except (ValueError,OSError) as exc:self.status.setText(str(exc));return
        self.accept()
