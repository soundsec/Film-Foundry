"""Native point editor for the public scanner curve contracts."""
from __future__ import annotations

import json
from pathlib import Path

from film_foundry.app.config_assets import load_curve_file, load_reference_tone
from film_foundry.app.preset_catalog import REFERENCE_TONE_PRESETS
from film_foundry.model.config import normalize_scanner_color_curve_rgb, normalize_scanner_tone_curve
from film_foundry.ui.i18n import current_language
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.theme import colors


def text(zh, en):
    return zh if current_language().startswith("zh") else en


IDENTITY = ((0., 0.), (1., 1.))


class CurveCanvas(QtWidgets.QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setMinimumSize(240, 180)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.dragging = False
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setCursor(QtCore.Qt.CursorShape.CrossCursor)

    def nearest_point(self, position):
        points = self.editor.current_points()
        distances = [(self.position(point)-position).manhattanLength() for point in points]
        closest = min(range(len(points)), key=distances.__getitem__)
        return closest if distances[closest] < 16 else None

    def plot_rect(self):
        return QtCore.QRectF(30, 12, self.width()-44, self.height()-40)

    def position(self, point):
        rect = self.plot_rect()
        lo, hi = self.editor.y_range()
        return QtCore.QPointF(rect.left()+point[0]*rect.width(), rect.bottom()-(point[1]-lo)/(hi-lo)*rect.height())

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        theme=colors()
        p.fillRect(self.rect(), QtGui.QColor(theme.canvas))
        rect = self.plot_rect()
        p.setPen(QtGui.QPen(QtGui.QColor(theme.grid), 1))
        for i in range(5):
            f=i/4
            p.drawLine(QtCore.QPointF(rect.left()+f*rect.width(), rect.top()), QtCore.QPointF(rect.left()+f*rect.width(), rect.bottom()))
            p.drawLine(QtCore.QPointF(rect.left(), rect.top()+f*rect.height()), QtCore.QPointF(rect.right(), rect.top()+f*rect.height()))
        p.setPen(QtGui.QColor(theme.muted))
        p.drawText(4, int(rect.top())+10, "out")
        p.drawText(int(rect.right())-12, self.height()-6, "in")
        p.drawText(int(rect.left()), self.height()-6, "0")
        p.setPen(QtGui.QPen(QtGui.QColor(theme.diagonal), 1, QtCore.Qt.PenStyle.DashLine))
        p.drawLine(self.position((0, 0)), self.position((1, 1)))
        curve_colors = [theme.red, theme.green, theme.blue] if self.editor.rgb else [theme.tone]
        for i, points in enumerate(self.editor.points):
            path=QtGui.QPainterPath(self.position((0.,points[0][1])))
            for point in points:
                path.lineTo(self.position(point))
            path.lineTo(self.position((1.,points[-1][1])))
            p.setPen(QtGui.QPen(QtGui.QColor(curve_colors[i]), 2))
            p.drawPath(path)
        points = self.editor.current_points()
        for i, point in enumerate(points):
            p.setBrush(QtGui.QColor(theme.point if i == self.editor.selected else theme.muted))
            p.setPen(QtGui.QColor(theme.canvas))
            p.drawEllipse(self.position(point), 5, 5)

    def mousePressEvent(self, event):
        self.setFocus()
        closest = self.nearest_point(event.position())
        if closest is not None:
            self.editor.select_point(closest)
            if event.button() == QtCore.Qt.MouseButton.RightButton:
                self.editor.remove_point()
            elif event.button() == QtCore.Qt.MouseButton.LeftButton:
                self.dragging=True

    def mouseMoveEvent(self, event):
        if self.dragging:
            r=self.plot_rect(); lo, hi=self.editor.y_range()
            self.editor.move_point((event.position().x()-r.left())/r.width(), lo+(r.bottom()-event.position().y())/r.height()*(hi-lo))

    def mouseReleaseEvent(self, event):
        self.dragging=False

    def mouseDoubleClickEvent(self, event):
        self.dragging=False
        if event.button() != QtCore.Qt.MouseButton.LeftButton or not self.plot_rect().contains(event.position()):
            return
        self.setFocus()
        closest = self.nearest_point(event.position())
        if closest is not None:
            self.editor.select_point(closest)
            return
        r=self.plot_rect();lo,hi=self.editor.y_range()
        x=(event.position().x()-r.left())/r.width()
        click_y=lo+(r.bottom()-event.position().y())/r.height()*(hi-lo)
        # Clicking near a segment inserts on that segment, with no accidental
        # tone jump from a few pixels of mouse imprecision. Else use click y.
        curve_y=self.editor.value_at(x)
        y=curve_y if abs(self.position((x,curve_y)).y()-event.position().y()) <= 12 else click_y
        self.editor.add_point(x,y)

    def keyPressEvent(self, event):
        if event.key() in (QtCore.Qt.Key.Key_Delete, QtCore.Qt.Key.Key_Backspace):
            self.editor.remove_point();event.accept()
        else:
            super().keyPressEvent(event)


class CurveEditor(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key=key
        self.rgb=key != "tone_curve"
        self.points=[list(IDENTITY) for _ in range(3 if self.rgb else 1)]
        self.selected=0
        self._loading=False
        layout=QtWidgets.QVBoxLayout(self);layout.setContentsMargins(8, 8, 8, 8)
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar)
        titles={"tone_curve":text("影调曲线 · 亮度", "Tone curve · brightness"), "color_curve_rgb":text("成片 RGB 曲线", "Output RGB curves"), "device_response_rgb":text("设备采集响应", "Device capture response")}
        self.enabled=QtWidgets.QCheckBox(titles[key]);bar.addWidget(self.enabled)
        self.channel=QtWidgets.QComboBox();self.channel.addItems([text("RGB 联动", "RGB linked"), "R", "G", "B"] if self.rgb else [text("亮度", "Brightness")]);bar.addWidget(self.channel)
        self.channel.setMaximumWidth(110)
        self.reference_buttons={}
        if not self.rgb:
            references=QtWidgets.QHBoxLayout();layout.addLayout(references)
            for key,zh,en in zip(REFERENCE_TONE_PRESETS,("实拍柔和","实拍浓厚"),("Photo soft","Photo deep")):
                button=QtWidgets.QPushButton(text(zh,en))
                button.setToolTip(text("单场景相对影调，未作胶卷／扫描器标定。只替换当前影调曲线。", "Relative tone from one scene, not film/scanner calibration. Replaces only the current tone curve."))
                button.clicked.connect(lambda _checked=False,k=key:self.apply_reference_tone(k))
                references.addWidget(button);self.reference_buttons[key]=button
        self.canvas=CurveCanvas(self);layout.addWidget(self.canvas)
        row=QtWidgets.QHBoxLayout();layout.addLayout(row)
        self.point=QtWidgets.QComboBox();row.addWidget(self.point)
        self.x=QtWidgets.QDoubleSpinBox();self.x.setRange(0, 1);self.x.setDecimals(5);self.x.setSingleStep(.01)
        self.y=QtWidgets.QDoubleSpinBox();self.y.setRange(-1e9, 1e9);self.y.setDecimals(5);self.y.setSingleStep(.01)
        self.point.setMaximumWidth(48)
        self.x.setFixedWidth(82);self.y.setFixedWidth(82)
        for label, control in [("x", self.x), ("y", self.y)]:
            row.addWidget(QtWidgets.QLabel(label));row.addWidget(control);control.setKeyboardTracking(False)
        actions=QtWidgets.QGridLayout();layout.addLayout(actions)
        self.buttons={}
        for index,(label, callback) in enumerate([(text("恒等", "Identity"), self.reset_curve), (text("导入", "Import"), self.import_curve), (text("导出", "Export"), self.export_curve), (text("加点", "Add"), lambda: self.add_point()), (text("删点", "Remove"), self.remove_point)]):
            button=QtWidgets.QPushButton(label);button.clicked.connect(callback);actions.addWidget(button,index//3,index%3);self.buttons[label]=button
            button.setStyleSheet("padding:5px 7px;")
        self.note=QtWidgets.QLabel(text("双击曲线加点，拖动调整；右键点或 Delete 删点。关闭保留编辑值。", "Double-click to add; drag to adjust. Right-click a point or Delete to remove. Disabling retains edits."));self.note.setWordWrap(True);layout.addWidget(self.note)
        self.enabled.toggled.connect(self._enable)
        self.channel.currentIndexChanged.connect(lambda _: self.select_point(0))
        self.point.currentIndexChanged.connect(self.select_point)
        self.x.valueChanged.connect(lambda _: self.move_point(self.x.value(), self.y.value()))
        self.y.valueChanged.connect(lambda _: self.move_point(self.x.value(), self.y.value()))
        self.refresh()

    def active_channel(self):
        return max(0, self.channel.currentIndex()-1) if self.rgb else 0

    def current_points(self):
        return self.points[self.active_channel()]

    def value_at(self, x):
        points=self.current_points()
        for (ax,ay),(bx,by) in zip(points,points[1:]):
            if ax <= x <= bx:
                return ay+(by-ay)*(x-ax)/(bx-ax)
        return points[0][1] if x < points[0][0] else points[-1][1]

    def y_range(self):
        values=[y for points in self.points for _, y in points]
        return min(0., min(values)), max(1., max(values))

    def select_point(self, index):
        if self._loading:
            return
        self.selected=max(0, min(index, len(self.current_points())-1));self.refresh()

    def refresh(self):
        self._loading=True
        points=self.current_points();self.selected=min(self.selected, len(points)-1)
        self.point.clear();self.point.addItems([str(i+1) for i in range(len(points))]);self.point.setCurrentIndex(self.selected)
        x,y=points[self.selected];self.x.setValue(x);self.y.setValue(y)
        self._loading=False
        self.canvas.update()

    def _enable(self, enabled):
        if not self._loading:
            self.changed.emit()

    def _commit(self, points):
        index=self.active_channel()
        if self.rgb and self.channel.currentIndex() == 0:
            self.points=[list(points) for _ in range(3)]
        else:
            self.points[index]=list(points)
        self._loading=True;self.enabled.setChecked(True);self._loading=False
        self.refresh();self.changed.emit()

    def move_point(self, x, y):
        if self._loading:
            return
        points=list(self.current_points());i=self.selected
        low=0 if i == 0 else points[i-1][0]+1e-6
        high=1 if i == len(points)-1 else points[i+1][0]-1e-6
        points[i]=(min(high, max(low, x)), y)
        self._commit(points)

    def add_point(self, x=None, y=None):
        points=list(self.current_points())
        if x is None:
            i=max(range(len(points)-1), key=lambda j: points[j+1][0]-points[j][0]);x=(points[i][0]+points[i+1][0])/2;y=(points[i][1]+points[i+1][1])/2
        x=min(1., max(0., x))
        if any(abs(px-x) < 2e-6 for px,_ in points):
            return
        points.append((x,y));points.sort();self.selected=next(i for i,p in enumerate(points) if p[0] == x);self._commit(points)

    def remove_point(self):
        points=list(self.current_points())
        if 0 < self.selected < len(points)-1:
            points.pop(self.selected);self.selected-=1;self._commit(points)

    def reset_curve(self):
        self.selected=0;self._commit(list(IDENTITY))

    def apply_reference_tone(self,key):
        try:
            self.load(load_reference_tone(key));self.changed.emit()
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, text("曲线错误", "Curve error"), str(exc))

    def load(self, value):
        normalize=normalize_scanner_color_curve_rgb if self.rgb else normalize_scanner_tone_curve
        value=normalize(value, field_name=f"scanner.{self.key}")
        self._loading=True;self.enabled.setChecked(value is not None);self._loading=False
        self.points=[list(p) for p in value] if self.rgb and value is not None else [list(value or IDENTITY) for _ in range(3 if self.rgb else 1)]
        self.selected=0;self.refresh()

    def read(self):
        if not self.enabled.isChecked():
            return None
        normalize=normalize_scanner_color_curve_rgb if self.rgb else normalize_scanner_tone_curve
        return normalize(self.points if self.rgb else self.points[0], field_name=f"scanner.{self.key}")

    def import_curve(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self, text("导入曲线", "Import curve"), "", "JSON (*.json)")
        if path:
            try:
                self.load(load_curve_file(Path(path), self.key));self.changed.emit()
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, text("曲线错误", "Curve error"), str(exc))

    def export_curve(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self, text("导出曲线", "Export curve"), self.key+".json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps({self.key:self.read()}, indent=2, allow_nan=False), encoding="utf-8")
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, text("曲线错误", "Curve error"), str(exc))
