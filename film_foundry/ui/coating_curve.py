"""Visible-wavelength control points for a single photosensitive or filter coating."""
from film_foundry.contracts.coating_stack import validate_curve
from film_foundry.ui.curve_editor import text
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.theme import colors


class CurvePlot(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setMinimumHeight(125)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        theme = colors()
        p.fillRect(self.rect(), QtGui.QColor(theme.canvas))
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        w, h = self.width()-54, self.height()-38
        pts = [QtCore.QPointF(44+(nm-380)*w/400, 12+(1-v)*h) for nm,v in self.editor.points()]
        for pct in (0,50,100):
            y = round(12+(1-pct/100)*h)
            p.setPen(QtGui.QColor(theme.grid))
            p.drawLine(43,y,self.width()-10,y)
            p.setPen(QtGui.QColor(theme.muted))
            p.drawText(4,y+4,f"{pct}%")
        p.setPen(QtGui.QPen(QtGui.QColor(theme.tone), 2))
        if pts:
            p.drawPolyline(QtGui.QPolygonF(pts))
        p.setPen(QtGui.QColor(theme.muted))
        p.drawText(43,self.height()-4,"380 nm")
        p.drawText(self.width()-58,self.height()-4,"780 nm")


class CoatingCurveDialog(QtWidgets.QDialog):
    def __init__(self, points, emulsion, parent=None):
        super().__init__(parent)
        self.setWindowTitle(text("乳剂光谱响应","Emulsion response") if emulsion else text("夹层光谱透射","Filter transmission"))
        layout = QtWidgets.QVBoxLayout(self)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([text("波长 / nm","Wavelength / nm"),
            text("相对响应 / %","Relative response / %") if emulsion else text("透射率 / %","Transmission / %")])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.plot = CurvePlot(self)
        layout.addWidget(self.plot)
        layout.addWidget(self.table)
        for nm,v in points:
            self.insert(self.table.rowCount(), nm, v)
        line = QtWidgets.QHBoxLayout()
        layout.addLayout(line)
        add = QtWidgets.QPushButton(text("插入采样点","Insert point"))
        remove = QtWidgets.QPushButton(text("移除采样点","Remove point"))
        line.addWidget(add)
        line.addWidget(remove)
        self.actions = add,remove
        add.clicked.connect(self.add_point)
        remove.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()))
        self.table.currentCellChanged.connect(self.refresh)
        self.table.model().rowsRemoved.connect(self.refresh)
        self.notice = QtWidgets.QLabel()
        layout.addWidget(self.notice)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.ok.setText(text("应用","Apply"))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text("取消","Cancel"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.selectRow(0)
        self.refresh()
        self.resize(450,420)

    def points(self):
        return [[self.table.cellWidget(i,0).value(), self.table.cellWidget(i,1).value()/100]
                for i in range(self.table.rowCount()) if self.table.cellWidget(i,1) is not None]

    def insert(self, i, nm, v):
        self.table.insertRow(i)
        for j, low, high, value in ((0,380.,780.,nm), (1,0.,100.,100*v)):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(low,high)
            spin.setDecimals(4)
            spin.setValue(value)
            spin.setEnabled(j == 1 or nm not in (380,780))
            self.table.setCellWidget(i,j,spin)
            spin.valueChanged.connect(self.refresh)

    def add_point(self):
        pts = self.points()
        i = min(max(0,self.table.currentRow()), len(pts)-2)
        self.insert(i+1, (pts[i][0]+pts[i+1][0])/2, (pts[i][1]+pts[i+1][1])/2)
        self.table.selectRow(i+1)
        self.refresh()

    def refresh(self):
        self.plot.update()
        self.actions[0].setEnabled(self.table.rowCount()<81)
        self.actions[1].setEnabled(0 < self.table.currentRow() < self.table.rowCount()-1)
        try:
            validate_curve(self.points())
            problem = ""
        except ValueError:
            problem = text("请按波长由小到大排列采样点。","Arrange points in increasing wavelength order.")
        self.ok.setEnabled(not problem)
        self.notice.setText(problem or text("采样点之间线性插值。", "Linear interpolation between points."))
