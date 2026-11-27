"""Dismissible desktop hint for a deliberate RAW import or processing action."""
from pathlib import Path

from film_foundry.core.io_utils import RAW_EXTENSIONS
from film_foundry.ui.curve_editor import text
from film_foundry.ui.preferences import load_preferences, save_preferences
from film_foundry.ui.qt import QtCore, QtWidgets


class RawImportNotice(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.message = QtWidgets.QLabel()
        self.message.setWordWrap(True)
        row.addWidget(self.message, 1)
        self.dismiss = QtWidgets.QPushButton(text('关闭提示', 'Dismiss'))
        self.suppress = QtWidgets.QPushButton(text('不再显示', 'Don’t show again'))
        row.addWidget(self.dismiss)
        row.addWidget(self.suppress)
        self.dismiss.clicked.connect(self.hide)
        self.suppress.clicked.connect(self._suppress)
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def offer(self, paths):
        source = next((Path(p) for p in paths if Path(p).suffix.lower() in RAW_EXTENSIONS), None)
        if source is None or load_preferences().get('hide_raw_hint'):
            self.hide()
            return
        app = QtWidgets.QApplication.instance()
        key = str(source.absolute())
        if app.property('lastRawHintSource') == key:
            return
        app.setProperty('lastRawHintSource', key)
        lead = (text('DNG 由 LibRaw 解码。', 'LibRaw decodes DNG files. ')
                if source.suffix.lower() == '.dng' else
                text('当前 RAW 输入支持 DNG。', 'DNG is the supported RAW format. '))
        self.message.setText(lead + text(
            '要保留相机滤镜或原厂显色，请从原厂软件导出带 sRGB 配置的 16 位 TIFF。',
            'To keep the camera filter or manufacturer’s rendering, export a 16-bit TIFF with an embedded sRGB profile from the camera software.'))
        self.show()
        self.timer.start(10000)

    def _suppress(self):
        save_preferences(hide_raw_hint=True)
        for notice in QtWidgets.QApplication.topLevelWidgets():
            for banner in notice.findChildren(RawImportNotice):
                banner.hide()
        self.hide()
