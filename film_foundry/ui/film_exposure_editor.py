"""Thin editors for stock response and explicit film capture conditions."""
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.contracts.film_exposure import MODEL, SCALARS, VECTORS, validate_response, validate_duration

LABELS = {
    'short_limit_seconds': ('正常区间下限 / 秒', 'Normal range minimum / s'),
    'long_limit_seconds': ('正常区间上限 / 秒', 'Normal range maximum / s'),
    'short_loss_per_stop': ('短曝光损失斜率 R/G/B', 'Short-exposure loss slope R/G/B'),
    'long_loss_per_stop': ('长曝光损失斜率 R/G/B', 'Long-exposure loss slope R/G/B'),
}


def spin(value, low, high, decimals=6):
    widget = QtWidgets.QDoubleSpinBox()
    widget.setDecimals(decimals); widget.setRange(low, high); widget.setValue(value)
    return widget


class ReciprocityEditor(QtWidgets.QWidget):
    def __init__(self, response=None, parent=None):
        super().__init__(parent)
        form = QtWidgets.QFormLayout(self)
        self.enabled = QtWidgets.QCheckBox(text('启用互易律响应', 'Enable reciprocity response'))
        self.enabled.setChecked(response is not None); form.addRow(self.enabled)
        values = validate_response(response or {'model': MODEL}); self.controls = {}
        for key in (*SCALARS, *VECTORS):
            row = QtWidgets.QWidget(); line = QtWidgets.QHBoxLayout(row); line.setContentsMargins(0, 0, 0, 0)
            widgets = ([spin(v, 0., .95) for v in values[key]] if key in VECTORS else
                       [spin(values[key], *SCALARS[key][1:], decimals=9)])
            for widget in widgets: line.addWidget(widget)
            self.controls[key] = widgets; form.addRow(text(*LABELS[key]), row)
            row.setEnabled(self.enabled.isChecked()); self.enabled.toggled.connect(row.setEnabled)
        self.setToolTip(text(
            '区间内不改变潜影。越界每一档时长对应各感光层多少档有效曝光损失；0关闭该分支。黑白取均值。系数随材料冻结，不是品牌实测。',
            'No change within the interval. Slopes specify effective exposure stops lost per stop beyond each limit; zero disables that branch. Monochrome uses the mean. Coefficients freeze with the stock and are not brand measurements.'))

    def read(self):
        if not self.enabled.isChecked(): return None
        return validate_response({'model': MODEL, **{
            key: [w.value() for w in widgets] if key in VECTORS else widgets[0].value()
            for key, widgets in self.controls.items()}})


class FilmExposureDialog(QtWidgets.QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent); self.setWindowTitle(text('曝光时间条件', 'Exposure time conditions')); self.resize(570, 250)
        self.response = config.film.reciprocity_response
        form = QtWidgets.QFormLayout(self)
        self.enabled = QtWidgets.QCheckBox(text('使用明确曝光时长', 'Use explicit exposure duration'))
        self.enabled.setChecked(config.exposure.duration_seconds is not None); form.addRow(self.enabled)
        self.seconds = spin(config.exposure.duration_seconds or 1., 1e-9, 86400., decimals=9)
        self.seconds.setEnabled(self.enabled.isChecked()); self.enabled.toggled.connect(self.seconds.setEnabled)
        form.addRow(text('曝光时长 / 秒', 'Exposure duration / s'), self.seconds)
        self.seconds.setToolTip(text(
            '图像已是累计曝光量，不再乘时长。当前模型假设潜影前全部光量处于同一均匀时间窗；不区分闪光、打印灯和漏光的独立时长，也不模拟运动模糊。关闭后不猜EXIF。已有潜影的条件不能在冲洗时改写。',
            'Image values already represent integrated dose; duration is not multiplied again. All pre-latent light shares one uniform window, without separate flash, imprint or leak timing or motion blur. Disabled means no EXIF inference. Frozen latent capture conditions cannot be changed at development.'))
        target = QtWidgets.QGroupBox(text('按期望损失求时长', 'Resolve duration from desired loss')); line = QtWidgets.QHBoxLayout(target)
        self.loss = spin(1., 0., 32.); self.loss.setSuffix(text(' 档', ' stops')); line.addWidget(self.loss)
        self.branch = QtWidgets.QComboBox(); self.branch.addItem(text('长曝光', 'Long exposure'), 'long'); self.branch.addItem(text('极短曝光', 'Very short exposure'), 'short'); line.addWidget(self.branch)
        solve = QtWidgets.QPushButton(text('换算时长', 'Resolve duration')); solve.clicked.connect(self.resolve); line.addWidget(solve); form.addRow(target)
        target.setToolTip(text('目标按各层平均感度损失（档）线性换算为实际时长。各层不一致会伴随偏色；不保证成片亮度线性，不自动加曝光补偿或改药浴。需先在材料编辑器启用互易律响应。', 'The target is mean layer speed loss in stops, resolved to an actual duration. Unequal layers can shift colour. Display brightness is not linear; no EV compensation or bath change is added. Enable stock reciprocity in the material editor first.'))
        self.status = QtWidgets.QLabel(); self.status.setWordWrap(True); form.addRow(self.status)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply); buttons.rejected.connect(self.reject); form.addRow(buttons)

    def resolve(self):
        from film_foundry.core.film_process.reciprocity import duration_for_loss
        try:
            value = duration_for_loss(self.response, self.loss.value(), branch=self.branch.currentData())
            self.seconds.setValue(value); self.enabled.setChecked(True); self.status.clear()
        except ValueError as exc: self.status.setText(str(exc))

    def apply(self):
        try:
            self.duration = validate_duration(self.seconds.value()) if self.enabled.isChecked() else None
            self.accept()
        except ValueError as exc: self.status.setText(str(exc))
