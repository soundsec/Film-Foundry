"""Lamp colour picker storing linear RGB, independent of its exposure control."""
import numpy as np
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.control_reset import reset_button
from film_foundry.core.color import linear_to_srgb,srgb_to_linear


class ExposureColor(QtWidgets.QWidget):
    changed=QtCore.Signal()

    def __init__(self,value,*,default=(1.,1.,1.),optional=False,parent=None,ink=False):
        super().__init__(parent)
        self._value=None if value is None else tuple(value)
        self.ink=ink
        self._baseline=self._value;self.default=default;self.optional=optional
        line=QtWidgets.QHBoxLayout(self);line.setContentsMargins(0,0,0,0);line.setSpacing(4)
        self.button=QtWidgets.QPushButton();line.addWidget(self.button,1);self.button.clicked.connect(self.choose)
        self.follow=None
        if optional:
            self.follow=QtWidgets.QCheckBox(text('随光源','From lamp'));line.addWidget(self.follow)
            self.follow.setChecked(value is None);self.follow.toggled.connect(self.follow_lamp)
        self.reset=reset_button(self);line.addWidget(self.reset)
        self.reset.setToolTip(text('恢复打开窗口时的颜色','Restore the original colour'))
        self.reset.clicked.connect(lambda:self.set_value(self._baseline))
        self.setToolTip(text('设置曝光光源的颜色，亮度另调。最终字色与明暗由材料、冲洗和扫描共同决定；黑白材料不会产生彩色字。','Sets the exposure lamp colour; brightness is separate. Stock, processing and scanning determine the final colour and tone. Monochrome stock stays monochrome.'))
        self.refresh()
        if ink:
            self.setToolTip(text('透过满版油墨的光色与亮度，白色为透明。','Colour and brightness transmitted through solid ink. White is transparent.'))

    def value(self):return self._value

    def set_value(self,value):
        self._value=None if value is None else tuple(value)
        if self.follow is not None:
            with QtCore.QSignalBlocker(self.follow):self.follow.setChecked(value is None)
        self.refresh();self.changed.emit()

    def follow_lamp(self,checked):self.set_value(None if checked else self.default)

    def refresh(self):
        rgb=linear_to_srgb(np.asarray(self._value if self._value is not None else self.default,np.float32))
        color=QtGui.QColor.fromRgbF(*[float(v) for v in np.clip(rgb,0,1)])
        pixmap=QtGui.QPixmap(24,16);pixmap.fill(color);self.button.setIcon(QtGui.QIcon(pixmap))
        self.button.setText(color.name().upper())
        self.button.setAccessibleName(text('油墨透射色','Ink transmission colour') if self.ink else text('选择曝光颜色','Choose exposure colour'))

    def choose(self):
        rgb=linear_to_srgb(np.asarray(self._value if self._value is not None else self.default,np.float32))
        title=text('油墨透射色','Ink transmission colour') if self.ink else text('曝光颜色','Exposure colour')
        color=QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*[float(v) for v in np.clip(rgb,0,1)]),self,title)
        if color.isValid():
            linear=srgb_to_linear(np.array([color.redF(),color.greenF(),color.blueF()],np.float32))
            self.set_value(tuple(float(v) for v in linear))
