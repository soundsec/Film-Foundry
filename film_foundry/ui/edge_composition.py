"""Edit both sides of a registered factory plate before applying loading offset."""
from film_foundry.contracts.edge_composition import DEFAULT_PLATE, KINDS, normalize_composition
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtWidgets


class EdgeCompositionEditor(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, initial):
        super().__init__()
        plate = initial if initial is not None else DEFAULT_PLATE
        box = QtWidgets.QVBoxLayout(self)
        self.active = QtWidgets.QCheckBox(text("自定义上下片边", "Customize both edges"))
        self.active.setChecked(initial is not None)
        box.addWidget(self.active)
        self.font = QtWidgets.QComboBox()
        self.font.addItem(text("方格体", "Square grid"), "grid")
        self.font.addItem(text("圆笔黑体", "Rounded sans"), "rounded")
        self.font.addItem(text("细线印刷体", "Light sans"), "sans")
        self.font.setCurrentIndex(self.font.findData(plate["font"]))
        form = QtWidgets.QFormLayout()
        form.addRow(text("文字与片号字体", "Text and frame font"), self.font)
        box.addLayout(form)
        self.sides = QtWidgets.QTabWidget()
        box.addWidget(self.sides)
        names = [text("文字 / 自绘厂标", "Text / stencil"), text("片号", "Frame number"),
                 text("过片方向", "Direction"), text("编码", "Code")]
        self.controls = {}
        defaults = {"text": (4, 45), "number": (48, 55), "direction": (58, 64), "barcode": (66, 99)}
        for side, title in (("top", text("上边", "Top")), ("bottom", text("下边", "Bottom"))):
            panel = QtWidgets.QWidget()
            self.sides.addTab(panel, title)
            grid = QtWidgets.QGridLayout(panel)
            for col, head in enumerate((text("打印项目", "Print"), text("起点", "Start"), text("终点", "End"))):
                grid.addWidget(QtWidgets.QLabel(head), 0, col)
            values = {kind: (a, b) for kind, a, b in plate[side]}
            self.controls[side] = []
            for row, (kind, name) in enumerate(zip(KINDS, names), 1):
                check = QtWidgets.QCheckBox(name)
                check.setChecked(kind in values)
                grid.addWidget(check, row, 0)
                limits = []
                for col, value in enumerate(values.get(kind, defaults[kind]), 1):
                    spin = QtWidgets.QDoubleSpinBox()
                    spin.setRange(0, 100)
                    spin.setDecimals(2)
                    spin.setSuffix(" %")
                    spin.setValue(value)
                    spin.setEnabled(check.isChecked())
                    check.toggled.connect(spin.setEnabled)
                    spin.valueChanged.connect(lambda *_: self.changed.emit())
                    grid.addWidget(spin, row, col)
                    limits.append(spin)
                check.toggled.connect(lambda *_: self.changed.emit())
                self.controls[side].append((kind, check, *limits))
        self.hint = QtWidgets.QLabel()
        self.hint.setWordWrap(True)
        box.addWidget(self.hint)
        box.addStretch()
        self.active.toggled.connect(lambda *_: self.changed.emit())
        self.font.currentIndexChanged.connect(lambda *_: self.changed.emit())

    def value(self):
        active = self.active.isChecked()
        self.sides.setEnabled(active)
        self.font.setEnabled(active)
        self.hint.setText(text("按片宽百分比定位。起点越小，项目越靠左。", "Positions are percentages of frame width. Earlier starts print farther left."))
        if not active:
            return None
        result = {"font": self.font.currentData()}
        for side, controls in self.controls.items():
            result[side] = [[kind, a.value(), b.value()] for kind, check, a, b in controls if check.isChecked()]
        try:
            return normalize_composition(result)
        except ValueError:
            self.hint.setText(text("请检查上下边的区间。终点须大于起点，同侧区间须错开。",
                                   "Check both edges. Each slot needs a positive width and its own space."))
            raise
