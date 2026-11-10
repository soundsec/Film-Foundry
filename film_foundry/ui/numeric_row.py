"""Presentation-only slider and precise input pairing for native Qt forms."""
from film_foundry.ui.qt import QtCore, QtWidgets, _ScrollSafeSlider, _ScrollSafeNumber


class ParameterNumber(_ScrollSafeNumber):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        self.setMinimumWidth(108)
        self.setMaximumWidth(132)

    def textFromValue(self, value):
        text = super().textFromValue(value)
        separator = self.locale().decimalPoint()
        return text.rstrip('0').rstrip(separator) if separator in text else text


class NumericRow(QtWidgets.QWidget):
    """The spin box remains authoritative; viewing never rewrites its value."""
    def __init__(self, number, parent=None):
        super().__init__(parent)
        self.number = number
        self.slider = _ScrollSafeSlider(QtCore.Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.slider.setRange(0, 10000)
        self.slider.setMinimumWidth(120)
        number.setKeyboardTracking(False)
        number.setMinimumWidth(108)
        number.setMaximumWidth(132)
        number.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self.slider, 1)
        layout.addWidget(number)
        number.valueChanged.connect(self.sync)
        number.installEventFilter(self)
        self.slider.valueChanged.connect(self.changed)
        self.sync()

    def sync(self, *_):
        span = self.number.maximum() - self.number.minimum()
        fraction = (self.number.value() - self.number.minimum()) / span if span else 0.
        with QtCore.QSignalBlocker(self.slider):
            self.slider.setValue(round(fraction * 10000))
        self.slider.setEnabled(self.number.isEnabled())
        self.slider.setToolTip(self.number.toolTip())

    def changed(self, position):
        span = self.number.maximum() - self.number.minimum()
        self.number.setValue(self.number.minimum() + span * position / 10000)

    def eventFilter(self, watched, event):
        if event.type() in (QtCore.QEvent.Type.EnabledChange, QtCore.QEvent.Type.ToolTipChange):
            self.sync()
        return False


def polish_form(form):
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(12)
