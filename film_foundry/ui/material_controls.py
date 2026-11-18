"""UI-only numeric reset and observation controls; no material defaults live here."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


class SaveFooter(QtWidgets.QWidget):
    """Keep exits in one row when they fit, and two rows in narrow previews."""
    def __init__(self):
        super().__init__();self._columns=0
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,QtWidgets.QSizePolicy.Policy.Fixed)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        grid=self.layout()
        if grid is None:return
        buttons=[
            item.widget() for i in range(grid.count()) if (item:=grid.itemAt(i)).widget() is not None and not item.widget().isHidden()]
        if not buttons:return
        needed=sum(button.minimumSizeHint().width() for button in buttons)+grid.horizontalSpacing()*(len(buttons)-1)
        columns=len(buttons) if self.width()>=needed else 2
        if columns==self._columns:return
        self._columns=columns
        for button in buttons:grid.removeWidget(button)
        for i,button in enumerate(buttons):
            grid.addWidget(button,i//columns,i%columns,1,2 if columns==2 and len(buttons)==3 and i==2 else 1)
        self.updateGeometry()


class ResetNumber(QtWidgets.QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QtWidgets.QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._reset_value=0.
        from film_foundry.ui.control_reset import reset_button
        self.reset_button=reset_button(self)
        self.reset_button.clicked.connect(lambda:self.setValue(self._reset_value))
        self.valueChanged.connect(self._sync_reset)
        self.setMinimumWidth(115)
        self.lineEdit().setTextMargins(0,0,24,0)

    def set_reset_value(self, value):
        self._reset_value=float(value);self._sync_reset()

    def _sync_reset(self, *_):
        changed=abs(self.value()-self._reset_value)>=.5*10**-self.decimals()
        self.reset_button.setEnabled(changed)
        self.reset_button.setToolTip(text('恢复本项基准：','Reset this value: ')+f'{self._reset_value:g}')
        self.reset_button.setAccessibleName(self.reset_button.toolTip())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.reset_button.move(max(0,self.width()-25),max(0,(self.height()-24)//2))
        self.reset_button.raise_()


class _ViewSlider(QtWidgets.QSlider):
    reset_requested=QtCore.Signal()
    def mouseDoubleClickEvent(self,event):
        self.reset_requested.emit();event.accept()


class ViewingSlider(QtWidgets.QWidget):
    valueChanged=QtCore.Signal(float)
    def __init__(self,value,low,high,parent=None):
        super().__init__(parent)
        row=QtWidgets.QHBoxLayout(self);row.setContentsMargins(0,0,0,0);row.setSpacing(5)
        self.slider=_ViewSlider(QtCore.Qt.Orientation.Horizontal);self.slider.setRange(0,1000)
        self.slider.setMinimumWidth(60);row.addWidget(self.slider,1)
        self.spin=ResetNumber();self.spin.setRange(low,high);self.spin.setDecimals(3)
        self.spin.setSingleStep(.05 if high<=10 else 1);self.spin.setKeyboardTracking(False)
        self.spin.setValue(value);self.spin.set_reset_value(value);self.spin.setMaximumWidth(125);row.addWidget(self.spin)
        self.slider.valueChanged.connect(lambda position:self.spin.setValue(low+(high-low)*position/1000))
        self.slider.reset_requested.connect(lambda:self.setValue(value))
        self.spin.valueChanged.connect(self._changed);self._changed(value)
        self.slider.setToolTip(text('拖动调整观看；双击复位，不改变材料。','Drag to adjust viewing; double-click to reset. Material is unchanged.'))

    def _changed(self,value):
        blocked=self.slider.blockSignals(True)
        self.slider.setValue(round((value-self.minimum())/(self.maximum()-self.minimum())*1000))
        self.slider.blockSignals(blocked);self.valueChanged.emit(value)
    def value(self):return self.spin.value()
    def setValue(self,value):self.spin.setValue(value)
    def minimum(self):return self.spin.minimum()
    def maximum(self):return self.spin.maximum()


def viewing_control(form,label,value,low,high):
    from film_foundry.ui.numeric_units import apply_unit
    control=ViewingSlider(value,low,high);form.addRow(apply_unit(control.spin,label),control)
    control.slider.setAccessibleName(label);control.spin.setAccessibleName(label)
    return control


def sync_source_preview(window):
    session=window.session
    fresh=session.image is not None and session.state is None and session.group is None
    if not hasattr(window,'source_preview_badge'):
        # Parent the badge to the view, not its scrolling viewport, so fitting/panning cannot move it.
        badge=QtWidgets.QLabel(text('来源预览 · 尚未曝光','Source preview · not exposed'),window.viewer)
        badge.setObjectName('sourcePreviewNote')
        badge.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        badge.move(10,10);badge.adjustSize();window.source_preview_badge=badge
    window.source_preview_badge.setVisible(fresh);window.source_preview_badge.raise_()
    key=(id(session.image),session.scan_adjustments.get('rotation_quarters',0)) if fresh else None
    if fresh and key!=getattr(window,'_unexposed_preview_key',None):
        window.edit_tools.sync_source(as_result=True)
    window._unexposed_preview_key=key
