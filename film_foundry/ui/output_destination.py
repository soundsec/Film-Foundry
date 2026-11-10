from pathlib import Path
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.app.output_destination import default_output_directory


class OutputDestinationDialog(QtWidgets.QDialog):
    def __init__(self, source, parent=None, *, details=()):
        super().__init__(parent)
        self.setWindowTitle(text('执行与保存','Run and save'))
        self.directory=None
        layout=QtWidgets.QVBoxLayout(self)
        form=QtWidgets.QFormLayout();layout.addLayout(form)
        label=QtWidgets.QLabel(source);label.setWordWrap(True)
        form.addRow(text('处理来源','Source'),label)
        for title,value in details:
            item=QtWidgets.QLabel(str(value));item.setWordWrap(True);form.addRow(title,item)
        self.path=QtWidgets.QLineEdit(str(default_output_directory()))
        form.addRow(text('输出文件夹','Output folder'),self.path)
        self.path.setToolTip(text('在此文件夹内创建独立任务目录，保留已有结果。','Create a separate run directory here, preserving existing results.'))
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        browse=QtWidgets.QPushButton(text('选择文件夹…','Choose folder…'));row.addWidget(browse)
        reset=QtWidgets.QPushButton(text('使用默认位置','Use default location'));row.addWidget(reset);row.addStretch()
        browse.clicked.connect(self.browse)
        reset.clicked.connect(lambda:self.path.setText(str(default_output_directory())))
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status);layout.addStretch()
        buttons=QtWidgets.QDialogButtonBox();layout.addWidget(buttons)
        buttons.addButton(text('开始处理','Start processing'),QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(text('返回','Back'),QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject)

    def browse(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,text('选择输出文件夹','Choose output folder'),self.path.text())
        if path:self.path.setText(path)

    def apply(self):
        value=self.path.text().strip()
        if not value:
            self.status.setText(text('请选择输出文件夹','Choose an output folder'));return
        directory=Path(value).expanduser().resolve()
        if directory.exists() and not directory.is_dir():
            self.status.setText(text('输出位置必须是文件夹','The destination must be a folder'));return
        self.directory=directory;self.accept()
