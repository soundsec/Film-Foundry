"""Small transactional date-back typography and position editor."""
from copy import deepcopy
import numpy as np
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text


class PositionPreview(QtWidgets.QLabel):
    moved=QtCore.Signal(float,float)
    def mousePressEvent(self,event):self.move_at(event)
    def mouseMoveEvent(self,event):
        if event.buttons() & QtCore.Qt.MouseButton.LeftButton:self.move_at(event)
    def move_at(self,event):
        self.moved.emit(float(np.clip(event.position().x()/max(1,self.width()),0,1)),float(np.clip(event.position().y()/max(1,self.height()),0,1)))


class ImprintDialog(QtWidgets.QDialog):
    def __init__(self,config,parent=None):
        super().__init__(parent);self.config=deepcopy(config);self.setWindowTitle(text('打印字形与位置','Imprint typeface & position'));self.resize(520,420)
        box=QtWidgets.QVBoxLayout(self);form=QtWidgets.QFormLayout();box.addLayout(form)
        self.font=QtWidgets.QComboBox()
        for key,zh,en in [('seven_segment','七段数码 · 字母点阵','Seven segment · dot matrix letters'),('dot_matrix','点阵显示','Dot matrix'),('legacy','原字形','Legacy')]:self.font.addItem(text(zh,en),key)
        self.font.setCurrentIndex(max(0,self.font.findData(config.imprint_font)));form.addRow(text('字形','Typeface'),self.font)
        self.scale=QtWidgets.QDoubleSpinBox();self.scale.setRange(.25,3);self.scale.setSingleStep(.05);self.scale.setValue(config.imprint_scale);form.addRow(text('字号比例','Type size'),self.scale)
        self.free=QtWidgets.QCheckBox(text('自由位置','Free position'));self.free.setChecked(config.imprint_position_xy is not None);form.addRow(self.free)
        self.x=QtWidgets.QDoubleSpinBox();self.y=QtWidgets.QDoubleSpinBox()
        position=config.imprint_position_xy or (.045,.014 if config.imprint_side=='top' else .986)
        for control,value,zh,en in ((self.x,position[0],'水平位置','Horizontal'),(self.y,position[1],'垂直位置','Vertical')):
            control.setRange(0,100);control.setDecimals(1);control.setSuffix(' %');control.setValue(value*100);form.addRow(text(zh,en),control)
        self.preview=PositionPreview();self.preview.setMinimumSize(460,190);self.preview.setScaledContents(True);self.preview.setToolTip(text('拖动设置位置。示意图显示打印范围，实际色调由曝光与冲洗决定。','Drag to position. The diagram shows placement; exposure and development determine the actual tone.'));box.addWidget(self.preview,1)
        self.preview.moved.connect(self.move)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel|QtWidgets.QDialogButtonBox.StandardButton.Reset);box.addWidget(buttons)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Reset).clicked.connect(self.reset)
        self.font.currentIndexChanged.connect(self.refresh);self.free.toggled.connect(self.refresh)
        for control in (self.scale,self.x,self.y):control.valueChanged.connect(self.refresh)
        from film_foundry.ui.control_reset import form_reset_buttons
        form_reset_buttons(form)
        self.refresh()

    def value(self):
        result=deepcopy(self.config);result.imprint_font=self.font.currentData();result.imprint_scale=self.scale.value()
        result.imprint_position_xy=(self.x.value()/100,self.y.value()/100) if self.free.isChecked() else None
        return result

    def move(self,x,y):
        self.free.setChecked(True);self.x.setValue(x*100);self.y.setValue(y*100)

    def reset(self):
        self.font.setCurrentIndex(self.font.findData(self.config.imprint_font));self.scale.setValue(self.config.imprint_scale)
        position=self.config.imprint_position_xy or (.045,.014 if self.config.imprint_side=='top' else .986)
        self.x.setValue(position[0]*100);self.y.setValue(position[1]*100);self.free.setChecked(self.config.imprint_position_xy is not None)

    def refresh(self,*_):
        self.x.setEnabled(self.free.isChecked());self.y.setEnabled(self.free.isChecked())
        from film_foundry.core.film_border import _render_imprint_mask
        config=self.value()
        if not (config.imprint_text or config.imprint_time):config.imprint_text='2026.11.10'
        if config.imprint_style=='none':config.imprint_style='optical_amber'
        mask=_render_imprint_mask((240,480),config)
        pixels=np.ascontiguousarray(np.clip(40+mask[...,None]*np.array([210,120,30]),0,255),dtype=np.uint8)
        image=QtGui.QImage(pixels.data,480,240,pixels.strides[0],QtGui.QImage.Format.Format_RGB888).copy();self.preview.setPixmap(QtGui.QPixmap.fromImage(image))
