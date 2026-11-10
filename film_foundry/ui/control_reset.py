"""Frontend-only reset baselines for a loaded recipe, with grouped actions."""
from copy import deepcopy
from film_foundry.ui.qt import QtCore,QtGui,QtWidgets
from film_foundry.ui.curve_editor import text


class ResetButton(QtWidgets.QToolButton):
    """Theme-coloured vector glyph, including after a live theme switch."""
    def paintEvent(self,event):
        super().paintEvent(event)
        from film_foundry.ui.theme import colors
        theme=colors()
        color=QtGui.QColor(theme.disabled if not self.isEnabled() else theme.accent if self.underMouse() else theme.muted)
        painter=QtGui.QPainter(self);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setPen(QtGui.QPen(color,1.6,QtCore.Qt.PenStyle.SolidLine,QtCore.Qt.PenCapStyle.RoundCap,QtCore.Qt.PenJoinStyle.RoundJoin))
        painter.translate(self.width()/2-12,self.height()/2-12)
        painter.drawArc(QtCore.QRectF(6,6,12,12),50*16,285*16)
        painter.drawPolyline(QtGui.QPolygonF([QtCore.QPointF(12.5,8),QtCore.QPointF(16.5,8),QtCore.QPointF(16.5,4)]))


def reset_button(parent=None):
    button=ResetButton(parent)
    button.setFixedSize(24,24)
    button.setStyleSheet('QToolButton { padding: 0px; min-width: 0px; }')
    button.setAutoRaise(True)
    return button


def form_reset_buttons(form):
    """Add per-field restore actions to a small transactional Qt form."""
    for row in range(form.rowCount()):
        role=QtWidgets.QFormLayout.ItemRole.FieldRole
        item=form.itemAt(row,role)
        if item is None:
            role=QtWidgets.QFormLayout.ItemRole.SpanningRole;item=form.itemAt(row,role)
        widget=item.widget() if item is not None else None
        if isinstance(widget,QtWidgets.QComboBox):getter,setter=widget.currentIndex,widget.setCurrentIndex
        elif isinstance(widget,(QtWidgets.QSpinBox,QtWidgets.QDoubleSpinBox)):getter,setter=widget.value,widget.setValue
        elif isinstance(widget,QtWidgets.QLineEdit):getter,setter=widget.text,widget.setText
        elif isinstance(widget,QtWidgets.QCheckBox):getter,setter=widget.isChecked,widget.setChecked
        else:continue
        baseline=deepcopy(getter());form.removeWidget(widget)
        wrapper=QtWidgets.QWidget();layout=QtWidgets.QHBoxLayout(wrapper);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        layout.addWidget(widget,1);button=reset_button(wrapper);layout.addWidget(button)
        button.setToolTip(text('恢复打开窗口时的值','Restore the value when this dialog opened'))
        button.clicked.connect(lambda checked=False,write=setter,value=baseline:write(deepcopy(value)))
        form.setWidget(row,role,wrapper)


class ControlResets:
    def __init__(self,changed):
        self.changed=changed;self.entries=[];self.baselines={}

    def clear_widgets(self):
        self.entries=[]

    def add(self,owner,key,getter,setter):
        self.baselines.setdefault(key,deepcopy(getter()))
        self.entries.append((owner,key,getter,setter))

    def button(self,parent,key,getter,setter):
        self.add(parent,key,getter,setter)
        button=reset_button(parent)
        button.setToolTip(text('恢复当前预设 / 载入时的值','Restore the current preset / loaded value'))
        button.setAccessibleName(text('重置此项','Reset control'))
        button.clicked.connect(lambda:self.reset_key(key))
        return button

    def reset_key(self,key):
        entry=next((entry for entry in self.entries if entry[1]==key),None)
        if entry:
            entry[3](deepcopy(self.baselines[key]));self.changed()

    def reset_group(self,group):
        seen=set()
        for owner,key,_,setter in self.entries:
            if key not in seen and (owner is group or group.isAncestorOf(owner)):
                seen.add(key);setter(deepcopy(self.baselines[key]))
        self.changed()

    def capture(self,group=None):
        for owner,key,getter,_ in self.entries:
            if group is None or owner is group or group.isAncestorOf(owner):
                self.baselines[key]=deepcopy(getter())
