"""Small grid stencil editor; no image-processing or barcode interpretation."""
from film_foundry.contracts.edge_marks import EdgeMarkPattern
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.curve_editor import text


class EdgeMarkGrid(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.paint_value = None
        self.last_cell = None
        self.load(EdgeMarkPattern(32, 8, bytes(32)))

    def load(self, pattern):
        self.columns, self.rows = pattern.width, pattern.height
        self.cells = [pattern.ink(x, y) for y in range(self.rows) for x in range(self.columns)]
        self.setFixedSize(self.columns * 18 + 1, self.rows * 18 + 1)
        self.update()

    def pattern(self):
        bits = bytearray((len(self.cells) + 7) // 8)
        for i, on in enumerate(self.cells):
            if on:
                bits[i // 8] |= 1 << (7 - i % 8)
        return EdgeMarkPattern(self.columns, self.rows, bytes(bits))

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#29241f"))
        for y in range(self.rows):
            for x in range(self.columns):
                if self.cells[y * self.columns + x]:
                    painter.fillRect(x * 18, y * 18, 18, 18, QtGui.QColor("#e0b36f"))
        painter.setPen(QtGui.QColor("#665d53"))
        for x in range(self.columns + 1):
            painter.drawLine(x * 18, 0, x * 18, self.height())
        for y in range(self.rows + 1):
            painter.drawLine(0, y * 18, self.width(), y * 18)

    def _draw(self, position):
        x, y = int(position.x()) // 18, int(position.y()) // 18
        if not (0 <= x < self.columns and 0 <= y < self.rows):
            return
        if self.paint_value is None:
            self.paint_value = not self.cells[y * self.columns + x]
        previous = self.last_cell or (x, y)
        steps = max(abs(x - previous[0]), abs(y - previous[1]), 1)
        for step in range(steps + 1):
            px = round(previous[0] + (x - previous[0]) * step / steps)
            py = round(previous[1] + (y - previous[1]) * step / steps)
            self.cells[py * self.columns + px] = self.paint_value
        self.last_cell = (x, y)
        self.update()
        self.changed.emit()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.paint_value = self.last_cell = None
            self._draw(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self._draw(event.position())

    def mouseReleaseEvent(self, event):
        self.paint_value = self.last_cell = None


class EdgeMarkEditor(QtWidgets.QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(text("片边点阵图案", "Film-edge stencil"))
        self.resize(720, 470)
        self.codes = [config.factory_pattern_top, config.factory_pattern_bottom]
        self.current = 0
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel(text("涂格生成图案编码。图案替换材料名称区域，格号自动追加。桌面目前仅支持布局编辑，正式片边输出暂不支持。", "Draw cells to generate a stencil code. The stencil replaces the stock-name area; frame numbers remain automatic. Desktop currently edits layouts; rendered film edges are not yet supported."))
        note.setWordWrap(True)
        layout.addWidget(note)
        controls = QtWidgets.QHBoxLayout()
        layout.addLayout(controls)
        self.side = QtWidgets.QComboBox()
        self.side.addItems([text("上片边", "Top edge"), text("下片边", "Bottom edge")])
        controls.addWidget(self.side)
        self.linked = QtWidgets.QCheckBox(text("上下共用图案", "Use the same stencil"))
        self.linked.setChecked(config.factory_pattern_linked)
        controls.addWidget(self.linked)
        self.flips = []
        for label, value in ((text("上边翻转 180°", "Top: rotate 180°"), config.factory_flip_top), (text("下边翻转 180°", "Bottom: rotate 180°"), config.factory_flip_bottom)):
            widget = QtWidgets.QCheckBox(label)
            widget.setChecked(value)
            controls.addWidget(widget)
            self.flips.append(widget)
        process = QtWidgets.QHBoxLayout()
        layout.addLayout(process)
        process.addWidget(QtWidgets.QLabel(text("成印方式", "Marking method")))
        self.mark_type = QtWidgets.QComboBox()
        self.mark_type.addItem(text("清晰印字（曝光）", "Hard-edged exposure"), "hard")
        self.mark_type.addItem(text("激光式曝光", "Laser-style exposure"), "laser")
        self.mark_type.setCurrentIndex(max(0, self.mark_type.findData(config.factory_mark_type)))
        process.addWidget(self.mark_type)
        process.addWidget(QtWidgets.QLabel(text("材料扩散倍率", "Material spread")))
        self.spread = QtWidgets.QDoubleSpinBox()
        self.spread.setRange(0, 3)
        self.spread.setSingleStep(.1)
        self.spread.setValue(config.factory_mark_spread)
        process.addWidget(self.spread)
        self.spread.setEnabled(self.mark_type.currentData() == "laser")
        self.mark_type.currentIndexChanged.connect(lambda _: self.spread.setEnabled(self.mark_type.currentData() == "laser"))
        process.addStretch()
        self.grid = EdgeMarkGrid()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(self.grid)
        layout.addWidget(scroll, 1)
        tools = QtWidgets.QHBoxLayout()
        layout.addLayout(tools)
        for title, action in ((text("清空图案", "Clear stencil"), self.clear), (text("恢复材料文字", "Use stock text"), self.reset_text)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(action)
            tools.addWidget(button)
        self.code = QtWidgets.QPlainTextEdit()
        self.code.setPlaceholderText("FFEM1:…")
        self.code.setMaximumHeight(76)
        layout.addWidget(self.code)
        self.error = QtWidgets.QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QtWidgets.QHBoxLayout()
        layout.addLayout(buttons)
        for title, action in ((text("导入编码", "Import code"), self.import_code), (text("生成编码", "Generate code"), self.export_code)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(action)
            buttons.addWidget(button)
        confirmation = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        confirmation.accepted.connect(self.accept)
        confirmation.rejected.connect(self.reject)
        buttons.addWidget(confirmation)
        self.grid.changed.connect(self.store)
        self.side.currentIndexChanged.connect(self.select)
        self.linked.toggled.connect(self._link_changed)
        self._link_changed()

    def store(self):
        self.codes[self.current] = self.grid.pattern().encode()
        self.code.setPlainText(self.codes[self.current])

    def select(self, index):
        self.current = index
        code = self.codes[index]
        try:
            pattern = EdgeMarkPattern.decode(code) if code else EdgeMarkPattern(32, 8, bytes(32))
        except ValueError:
            self.error.setText(text("已保存的编码无效，请重新导入或清空。", "Saved code is invalid; import a code or clear the stencil."))
            self.grid.load(EdgeMarkPattern(32, 8, bytes(32)))
            self.code.setPlainText(code)
            return
        self.grid.load(pattern)
        self.code.setPlainText(code)
        self.error.clear()

    def _link_changed(self, *_):
        if self.linked.isChecked():
            self.side.setCurrentIndex(0)
        self.side.setEnabled(not self.linked.isChecked())
        self.select(self.side.currentIndex())

    def clear(self):
        self.grid.cells[:] = [False] * len(self.grid.cells)
        self.grid.update()
        self.store()

    def reset_text(self):
        self.codes[self.current] = ""
        self.select(self.current)

    def import_code(self):
        try:
            pattern = EdgeMarkPattern.decode(self.code.toPlainText())
        except ValueError:
            self.error.setText(text("编码无效；尺寸上限为 128×32 格。", "Invalid code; maximum size is 128×32 cells."))
            return
        self.grid.load(pattern)
        self.store()
        self.error.clear()

    def export_code(self):
        self.code.setPlainText(self.grid.pattern().encode())
        self.code.selectAll()

    def apply_to(self, config):
        from film_foundry.contracts.edge_marks import normalize_edge_pattern
        config.factory_pattern_top, config.factory_pattern_bottom = [normalize_edge_pattern(code) for code in self.codes]
        config.factory_pattern_linked = self.linked.isChecked()
        config.factory_flip_top, config.factory_flip_bottom = [box.isChecked() for box in self.flips]
        config.factory_mark_type = self.mark_type.currentData()
        config.factory_mark_spread = self.spread.value()
