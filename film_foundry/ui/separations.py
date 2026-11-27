"""Desktop separation bench. Sources form once; registration only recomposes the preview."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, SimpleQueue

import numpy as np

from film_foundry.app.control import CancelFlag, WorkflowCancelled, WorkflowControl
from film_foundry.app.preview_budget import Priority, preview_budget
from film_foundry.app.separations import CHANNELS, export_separations, prepare_separations, separation_config
from film_foundry.ui.curve_editor import text
from film_foundry.ui.image_view import ImageView
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets


class SeparationDialog(QtWidgets.QDialog):
    def __init__(self, config, photo="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(text("三色分离与合成", "Three-colour separations"))
        self.resize(900, 530)
        self.config = separation_config(config)
        self.photo = Path(photo) if photo and Path(photo).is_file() else None
        self.paths = [None] * 3
        self.seed = self.config.random_seed if self.config.random_seed is not None else int(np.random.SeedSequence().entropy)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="separation-work")
        self.future = self.plates = None
        self.messages = SimpleQueue()
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(65)
        self.timer.timeout.connect(self._poll)
        layout = QtWidgets.QHBoxLayout(self)
        self.viewer = ImageView()
        layout.addWidget(self.viewer, 1)
        side = QtWidgets.QVBoxLayout()
        layout.addLayout(side)
        self.settings = QtWidgets.QWidget()
        self.settings.setMaximumWidth(325)
        side.addWidget(self.settings)
        controls = QtWidgets.QVBoxLayout(self.settings)
        controls.setContentsMargins(0, 0, 0, 0)
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems([text("一张照片 · 三次曝光", "One photograph · three exposures"),
                            text("三张已冲洗底片", "Three developed plates")])
        controls.addWidget(self.mode)
        self.sources = QtWidgets.QStackedWidget()
        controls.addWidget(self.sources)
        self.photo_button = QtWidgets.QPushButton()
        self.photo_button.clicked.connect(lambda: self._choose(None))
        photo_page = QtWidgets.QWidget()
        photo_box = QtWidgets.QVBoxLayout(photo_page)
        photo_box.setContentsMargins(0, 0, 0, 0)
        photo_box.addWidget(self.photo_button)
        stock = QtWidgets.QLabel(text("感光材料 · ", "Stock · ") + self.config.film.name)
        stock.setWordWrap(True)
        photo_box.addWidget(stock)
        self.compensate = QtWidgets.QCheckBox(text("按滤镜损光补偿曝光", "Compensate filter loss"))
        self.compensate.setChecked(True)
        photo_box.addWidget(self.compensate)
        self.sources.addWidget(photo_page)
        self.file_buttons = []
        archive_page = QtWidgets.QWidget()
        archive_box = QtWidgets.QVBoxLayout(archive_page)
        archive_box.setContentsMargins(0, 0, 0, 0)
        for index in range(3):
            button = QtWidgets.QPushButton()
            button.clicked.connect(lambda _, i=index: self._choose(i))
            self.file_buttons.append(button)
            archive_box.addWidget(button)
        self.sources.addWidget(archive_page)
        self.mode.currentIndexChanged.connect(self.sources.setCurrentIndex)
        self.mode.currentIndexChanged.connect(self._invalidate)
        self.compensate.toggled.connect(self._invalidate)
        self.preview = QtWidgets.QPushButton(text("生成预览", "Generate preview"))
        self.preview.clicked.connect(lambda: self._start())
        controls.addWidget(self.preview)
        controls.addSpacing(8)
        strip = QtWidgets.QHBoxLayout()
        self.view = QtWidgets.QButtonGroup(self)
        for index, title in enumerate((text("合成", "RGB"), "R", "G", "B")):
            button = QtWidgets.QToolButton()
            button.setText(title)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.setMinimumHeight(42)
            button.setIconSize(QtCore.QSize(28, 28))
            button.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
            self.view.addButton(button, index)
            strip.addWidget(button)
        self.view.idClicked.connect(self._display)
        controls.addLayout(strip)
        grid = QtWidgets.QGridLayout()
        controls.addLayout(grid)
        for col, title in enumerate(("", "X (px)", "Y (px)", "EV")):
            grid.addWidget(QtWidgets.QLabel(title), 0, col)
        self.register = []
        for i, band in enumerate("RGB", 1):
            grid.addWidget(QtWidgets.QLabel(band), i, 0)
            row = []
            for j in range(3):
                box = QtWidgets.QDoubleSpinBox() if j == 2 else QtWidgets.QSpinBox()
                if j == 2:
                    box.setRange(-5, 5); box.setSingleStep(.1)
                else:
                    box.setRange(-8192, 8192)
                box.setKeyboardTracking(False)
                box.valueChanged.connect(self._display)
                grid.addWidget(box, i, j+1)
                row.append(box)
            self.register.append(row)
        controls.addWidget(QtWidgets.QLabel(text("位移按原尺寸像素，共同区域出图。", "Offsets in native pixels. Output uses the overlap.")))
        side.addStretch()
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(320)
        side.addWidget(self.status)
        self.save = QtWidgets.QPushButton(text("全尺寸导出到新文件夹…", "Export full size to new folder…"))
        self.save.clicked.connect(self._export)
        side.addWidget(self.save)
        self.stop = QtWidgets.QPushButton(text("停止", "Stop"))
        self.stop.clicked.connect(lambda: self.flag.cancel())
        side.addWidget(self.stop)
        self.stop.hide()
        self.finished.connect(self._shutdown)
        QtWidgets.QApplication.instance().aboutToQuit.connect(self._shutdown)
        self._invalidate()

    def _choose(self, index):
        archive = index is not None
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, text("选择来源", "Choose source"), "",
            "Film Foundry (*.npz)" if archive else "Images (*.png *.jpg *.jpeg *.tif *.tiff *.dng *.webp *.bmp)")
        if path:
            if archive:
                self.paths[index] = Path(path)
            else:
                self.photo = Path(path)
            self._invalidate()

    def _inputs(self):
        return tuple(self.paths) if self.mode.currentIndex() else (self.photo,)

    def _invalidate(self):
        self.plates = None
        for i in range(1, 4):
            self.view.button(i).setIcon(QtGui.QIcon())
        self.viewer.clear_image(text("选择来源，生成预览", "Choose a source and generate a preview"))
        for path, button, label in [(self.photo, self.photo_button, text("选择照片", "Choose photograph")),
                                   *zip(self.paths, self.file_buttons, ("R", "G", "B"))]:
            button.setText(label if path is None else label + " · " + self.fontMetrics().elidedText(
                path.name, QtCore.Qt.TextElideMode.ElideMiddle, 135))
            button.setToolTip(str(path) if path else label)
        ready = all(self._inputs())
        self.preview.setEnabled(ready)
        self.save.setEnabled(ready)
        self.status.clear()

    def _adjustments(self):
        return ([(x.value(), y.value()) for x,y,_ in self.register],
                [g.value() for _,_,g in self.register])

    def _display(self):
        if self.plates is None:
            return
        try:
            i = self.view.checkedId()
            image = (np.repeat(self.plates.planes[i-1][..., None], 3, axis=-1) if i
                     else self.plates.compose(*self._adjustments()))
            self.viewer.show_array(image)
            self.status.setText(f"{image.shape[1]} × {image.shape[0]} px")
        except ValueError as exc:
            self.viewer.clear_image(str(exc)); self.status.setText(str(exc))

    def _export(self):
        base = QtWidgets.QFileDialog.getExistingDirectory(self, text("选择父文件夹", "Choose parent folder"))
        if not base:
            return
        path = Path(base) / "Tricolour"
        number = 1
        while path.exists():
            number += 1
            path = Path(base) / f"Tricolour-{number}"
        self._start(path)

    def _start(self, destination=None):
        if self.future is not None:
            return
        self.flag = CancelFlag()
        control = WorkflowControl(cancel_requested=self.flag.requested)
        paths, comp = self._inputs(), self.compensate.isChecked()
        offsets, gains = self._adjustments()
        self.exporting = destination is not None
        self.status.setText(text("准备处理…", "Preparing…"))
        expected = self.plates.revisions if self.plates is not None else None
        config = self.config
        options = dict(seed=self.seed, compensate=comp, progress=self.messages.put)
        self.settings.setEnabled(False)
        self.save.setEnabled(False)
        self.stop.show()
        self.setProperty("workspaceBusy", True)
        def work():
            with preview_budget.slot(Priority.STRIP, control.check_cancel):
                if destination is None:
                    return prepare_separations(paths, config, preview_edge=640, **options)
                return export_separations(destination, paths, config, offsets=offsets, gains=gains,
                                          expected_revisions=expected, **options)
        self.future = self.executor.submit(work)
        self.timer.start()

    def _poll(self):
        try:
            while True:
                band = self.messages.get_nowait()
                self.status.setText(text("处理分色片 ", "Processing separation ") + "RGB"[CHANNELS.index(band)])
        except Empty:
            pass
        if not self.future.done():
            return
        self.timer.stop()
        try:
            result = self.future.result()
            if self.exporting:
                self.status.setText(text("已保存 ", "Saved ") + str(result))
            else:
                self.plates = result
                for i, plane in enumerate(result.planes, 1):
                    stride = max(1, max(plane.shape) // 70)
                    gray = np.repeat(plane[::stride, ::stride, None], 3, axis=-1)
                    self.view.button(i).setIcon(QtGui.QIcon(self.viewer.pixmap(gray)))
                self._display()
        except WorkflowCancelled:
            self.status.setText(text("已停止", "Stopped"))
        except Exception as exc:
            self.status.setText(str(exc))
        self.future = None
        self.exporting = False
        self.setProperty("workspaceBusy", False)
        self.settings.setEnabled(True)
        self.save.setEnabled(True)
        self.stop.hide()

    def _shutdown(self):
        if self.future is not None:
            self.flag.cancel()
        self.timer.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
