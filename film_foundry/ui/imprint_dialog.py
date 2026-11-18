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
        super().__init__(parent);self.config=deepcopy(config);self.setWindowTitle(text('画面打印设置','Picture imprint settings'));self.resize(560,480)
        box=QtWidgets.QVBoxLayout(self);form=QtWidgets.QFormLayout();box.addLayout(form)
        self.font=QtWidgets.QComboBox()
        for key,zh,en in [('seven_segment','七段数码 · 字母点阵','Seven segment · dot matrix letters'),('dot_matrix','点阵显示','Dot matrix'),('legacy','原字形','Legacy')]:self.font.addItem(text(zh,en),key)
        self.font.setCurrentIndex(max(0,self.font.findData(config.imprint_font)));form.addRow(text('字形','Typeface'),self.font)
        self.scale=QtWidgets.QDoubleSpinBox();self.scale.setRange(.25,3);self.scale.setSingleStep(.05);self.scale.setValue(config.imprint_scale);form.addRow(text('字号比例','Type size'),self.scale)
        from film_foundry.ui.exposure_color import ExposureColor
        from film_foundry.core.film_border import imprint_exposure_rgb
        lamp=deepcopy(config);lamp.imprint_light_color_rgb=None;lamp.imprint_exposure_ev=0
        if lamp.imprint_style=='none':lamp.imprint_style='optical_amber'
        color=imprint_exposure_rgb(lamp);color=color/max(float(color.max()),1e-6)
        self.light_color=ExposureColor(config.imprint_light_color_rgb,default=tuple(color),optional=True)
        form.addRow(text('打印曝光颜色','Imprint lamp colour'),self.light_color)
        self.exposure=QtWidgets.QDoubleSpinBox();self.exposure.setRange(-4,4);self.exposure.setDecimals(2);self.exposure.setSingleStep(.25);self.exposure.setSuffix(' EV');self.exposure.setValue(config.imprint_exposure_ev)
        self.exposure.setToolTip(text('每增加1 EV，打印光剂量翻倍；最终明暗取决于材料极性与冲洗。','Each +1 EV doubles imprint exposure; final tone depends on the stock polarity and processing.'))
        form.addRow(text('打印亮度（曝光）','Imprint brightness (exposure)'),self.exposure)
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
        self.light_color.changed.connect(self.refresh)
        for control in (self.scale,self.x,self.y,self.exposure):control.valueChanged.connect(self.refresh)
        from film_foundry.ui.control_reset import form_reset_buttons
        form_reset_buttons(form)
        self.refresh()

    def value(self):
        result=deepcopy(self.config);result.imprint_font=self.font.currentData();result.imprint_scale=self.scale.value()
        result.imprint_position_xy=(self.x.value()/100,self.y.value()/100) if self.free.isChecked() else None
        result.imprint_light_color_rgb=self.light_color.value();result.imprint_exposure_ev=self.exposure.value()
        return result

    def move(self,x,y):
        self.free.setChecked(True);self.x.setValue(x*100);self.y.setValue(y*100)

    def reset(self):
        self.font.setCurrentIndex(self.font.findData(self.config.imprint_font));self.scale.setValue(self.config.imprint_scale)
        position=self.config.imprint_position_xy or (.045,.014 if self.config.imprint_side=='top' else .986)
        self.x.setValue(position[0]*100);self.y.setValue(position[1]*100);self.free.setChecked(self.config.imprint_position_xy is not None)
        self.light_color.set_value(self.config.imprint_light_color_rgb);self.exposure.setValue(self.config.imprint_exposure_ev)

    def refresh(self,*_):
        self.x.setEnabled(self.free.isChecked());self.y.setEnabled(self.free.isChecked())
        from film_foundry.core.film_border import _render_imprint_mask,imprint_exposure_rgb
        from film_foundry.core.color import linear_to_srgb
        config=self.value()
        if not (config.imprint_text or config.imprint_time):config.imprint_text='2026.11.10'
        if config.imprint_style=='none':config.imprint_style='optical_amber'
        mask=_render_imprint_mask((240,480),config)
        pixels=np.ascontiguousarray(np.clip(linear_to_srgb(.02+mask[...,None]*imprint_exposure_rgb(config)*.16)*255,0,255),dtype=np.uint8)
        image=QtGui.QImage(pixels.data,480,240,pixels.strides[0],QtGui.QImage.Format.Format_RGB888).copy();self.preview.setPixmap(QtGui.QPixmap.fromImage(image))
