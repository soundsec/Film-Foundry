"""Independent material spectral editor; public profiles own validation."""
from __future__ import annotations

import copy
import math

from film_foundry.contracts.data_kernel import SMITS_EXPOSURE_MODEL_ID, SMITS_BASIS_ORDER
from film_foundry.contracts.spectral_exposure import SpectralExposureProfile, make_smits_exposure_profile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SPECTRAL_COMPONENTS
from film_foundry.core.spectral_exposure import compile_spectral_exposure
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.theme import colors


class SpectrumCanvas(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setMinimumHeight(190)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        theme = colors()
        painter.fillRect(self.rect(), QtGui.QColor(theme.canvas))
        rect = QtCore.QRectF(45, 15, max(1, self.width()-65), max(1, self.height()-45))
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.grid), 1))
        for i in range(5):
            y = rect.top()+rect.height()*i/4
            painter.drawLine(QtCore.QPointF(rect.left(), y), QtCore.QPointF(rect.right(), y))
        wave = self.editor.draft["wavelengths_nm"]
        key, index = self.editor.series.currentData()
        try:
            values = [float(v) for v in self.editor.draft[key][index]]
            if not all(math.isfinite(v) and v >= 0 for v in values):
                return
        except (TypeError, ValueError):
            return
        peak = max(max(values), 1e-6)
        path = QtGui.QPainterPath()
        for i, (nm, value) in enumerate(zip(wave, values)):
            point = QtCore.QPointF(rect.left()+(nm-wave[0])/(wave[-1]-wave[0])*rect.width(),
                                  rect.bottom()-value/peak*rect.height())
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.tone), 2))
        painter.drawPath(path)
        painter.setPen(QtGui.QColor(theme.muted))
        painter.drawText(4, 24, f"{peak:.3g}")
        painter.drawText(4, int(rect.bottom()), "0")
        painter.drawText(int(rect.left()), self.height()-8, f"{wave[0]:g} nm")
        painter.drawText(int(rect.right())-65, self.height()-8, f"{wave[-1]:g} nm")


class SpectralProfileDialog(QtWidgets.QDialog):
    def __init__(self, profile, *, material=False, layers=3, parent=None):
        super().__init__(parent)
        self.material = material
        self.layers = layers
        self.profile = profile
        self.setWindowTitle(text("材料光谱编辑", "Material spectral editor"))
        self.resize(700, 700)
        layout = QtWidgets.QVBoxLayout(self)
        hint = QtWidgets.QLabel(text(
            "光谱定义用于创作。Smits 模板采用本项目解析七基谱，不是实测材料或原论文色度拟合表；HDR 保留大于 1 的曝光。修改仅在应用后生效。",
            "Creative spectral definitions. The Smits template uses this project's analytic seven-basis proxy, not measured stock or the paper's fitted tables. HDR exposure above 1 is retained. Changes take effect on Apply."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel(text("档案标识", "Profile ID")))
        self.profile_id = QtWidgets.QLineEdit()
        top.addWidget(self.profile_id, 1)
        if not material:
            create = QtWidgets.QPushButton(text("新建 Smits 创作模板", "New Smits creative template"))
            create.clicked.connect(lambda: self.load(make_smits_exposure_profile(self.layers)))
            top.addWidget(create)
        layout.addLayout(top)
        self.model_label = QtWidgets.QLabel()
        self.model_label.setWordWrap(True)
        layout.addWidget(self.model_label)
        self.series = QtWidgets.QComboBox()
        self.series.currentIndexChanged.connect(self._show_series)
        layout.addWidget(self.series)
        self.canvas = SpectrumCanvas(self)
        layout.addWidget(self.canvas)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([text("波长 nm", "Wavelength nm"), text("相对值", "Relative value")])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.cellChanged.connect(self._changed)
        layout.addWidget(self.table, 1)
        self.gain_label = QtWidgets.QLabel(text("层增益（分号分隔）", "Layer gains (semicolon separated)"))
        self.gains = QtWidgets.QLineEdit()
        layout.addWidget(self.gain_label)
        layout.addWidget(self.gains)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Apply |
                                            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).setText(text("应用", "Apply"))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text("取消", "Cancel"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if profile is None:
            if material:
                wave = tuple(float(n) for n in range(380, 781, 5))
                profile = SpectralMaterialProfile("creative_blank_absorbance_v1", wave,
                                                  tuple((0.,)*len(wave) for _ in SPECTRAL_COMPONENTS))
            else:
                profile = make_smits_exposure_profile(layers)
        self.load(profile)

    def load(self, profile):
        self.draft = copy.deepcopy(profile.as_dict())
        self.profile_id.setText(profile.profile_id)
        self.model_label.setText(profile.model)
        self.series.blockSignals(True)
        self.series.clear()
        if self.material:
            self.series.addItems(list(SPECTRAL_COMPONENTS))
            for i in range(9):
                self.series.setItemData(i, ("component_absorbance", i))
        else:
            names = SMITS_BASIS_ORDER if profile.model == SMITS_EXPOSURE_MODEL_ID else ("red", "green", "blue")
            for i, name in enumerate(names):
                self.series.addItem(text("输入基谱 · ", "Input basis · ")+name, ("rgb_basis", i))
            for i in range(profile.layer_count):
                self.series.addItem(text("感光层 · ", "Sensitivity · ")+str(i+1), ("layer_sensitivity", i))
            self.gains.setText("; ".join(repr(v) for v in profile.layer_gain))
        self.gain_label.setVisible(not self.material)
        self.gains.setVisible(not self.material)
        self.series.blockSignals(False)
        self.error.clear()
        self._show_series()

    def _show_series(self, *_):
        data = self.series.currentData()
        if data is None:
            return
        key, index = data
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.draft["wavelengths_nm"]))
        for row, (wave, value) in enumerate(zip(self.draft["wavelengths_nm"], self.draft[key][index])):
            label = QtWidgets.QTableWidgetItem(f"{wave:g}")
            label.setFlags(label.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, label)
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(str(value)))
        self.table.blockSignals(False)
        self.canvas.update()

    def _changed(self, row, column):
        if column != 1:
            return
        key, index = self.series.currentData()
        # Preserve invalid text in the draft, so switching rows cannot silently
        # discard it and Apply always uses the public finite/nonnegative checks.
        self.draft[key][index][row] = self.table.item(row, column).text()
        self.canvas.update()

    def apply(self):
        try:
            draft = copy.deepcopy(self.draft)
            draft["profile_id"] = self.profile_id.text().strip()
            keys = ("component_absorbance",) if self.material else ("rgb_basis", "layer_sensitivity")
            for key in keys:
                draft[key] = [[float(v) for v in row] for row in draft[key]]
            if self.material:
                profile = SpectralMaterialProfile.from_value(draft)
            else:
                draft["layer_gain"] = [float(v.strip()) for v in self.gains.text().split(";")]
                profile = SpectralExposureProfile.from_value(draft)
                compile_spectral_exposure(profile)
            self.profile = profile
            self.accept()
        except (ValueError, TypeError) as exc:
            self.error.setText(str(exc))
