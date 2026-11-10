"""Eight-band color controls for the shared scanner contract."""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.model.config import normalize_color_mixer


class ColorMixerEditor(QtWidgets.QWidget):
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = [[0., 0., 0.] for _ in range(8)]
        self.loading = False
        layout = QtWidgets.QVBoxLayout(self)
        self.channel = QtWidgets.QComboBox()
        self.channel.addItems([text(a,b) for a,b in zip(
            ("红", "橙", "黄", "绿", "青", "蓝", "紫", "洋红"),
            ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta"))])
        layout.addWidget(self.channel)
        self.controls = []
        for c, (zh,en,limit) in enumerate((("色相 · °","Hue · °",30),("饱和度","Saturation",100),("明度","Lightness",100))):
            row = QtWidgets.QHBoxLayout();layout.addLayout(row)
            row.addWidget(QtWidgets.QLabel(text(zh,en)))
            slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal);slider.setRange(-limit,limit)
            number = QtWidgets.QSpinBox();number.setRange(-limit,limit)
            row.addWidget(slider,1);row.addWidget(number)
            from film_foundry.ui.control_reset import reset_button
            reset_axis=reset_button();reset_axis.setToolTip(text('归零此项','Reset this adjustment to zero'));row.addWidget(reset_axis)
            reset_axis.clicked.connect(lambda checked=False,target=number:target.setValue(0))
            slider.valueChanged.connect(number.setValue)
            number.valueChanged.connect(slider.setValue)
            number.valueChanged.connect(lambda value, axis=c:self.change(axis,value))
            self.controls.append(number)
        reset = QtWidgets.QPushButton(text("重置当前颜色", "Reset selected color"))
        reset.clicked.connect(self.reset);layout.addWidget(reset)
        all_reset = QtWidgets.QPushButton(text("重置全部颜色", "Reset all colors"))
        all_reset.clicked.connect(lambda: self.reset(True));layout.addWidget(all_reset)
        self.channel.currentIndexChanged.connect(self.refresh)

    def refresh(self, *_):
        self.loading = True
        for control, value in zip(self.controls, self.rows[self.channel.currentIndex()]):control.setValue(round(value))
        self.loading = False

    def change(self, axis, value):
        if not self.loading:
            self.rows[self.channel.currentIndex()][axis] = value
            self.changed.emit()

    def reset(self, all_colors=False):
        if all_colors:self.rows = [[0.,0.,0.] for _ in range(8)]
        else:self.rows[self.channel.currentIndex()] = [0.,0.,0.]
        self.refresh();self.changed.emit()

    def load(self, value):
        self.rows = [list(row) for row in (normalize_color_mixer(value) or [(0.,0.,0.)]*8)]
        self.refresh()

    def read(self):
        return normalize_color_mixer(self.rows)
