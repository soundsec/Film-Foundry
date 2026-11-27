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

FILTER_LABELS = {
    None: ('无滤镜', 'Clear'), 'yellow': ('黄滤镜', 'Yellow'), 'orange': ('橙滤镜', 'Orange'),
    'red': ('红滤镜', 'Red'), 'green': ('绿滤镜', 'Green'), 'blue': ('蓝滤镜', 'Blue'),
    'skylight': ('天光镜', 'Skylight'), 'custom': ('自定义透射率', 'Custom transmission'),
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
        super().__init__(parent)
        self.setWindowTitle(text('镜前滤镜与曝光条件', 'Lens filter and exposure'))
        self.response = config.film.reciprocity_response
        form = QtWidgets.QFormLayout(self)
        self.film = config.formation_film()
        self.facing = QtWidgets.QComboBox()
        self.facing.addItem(text('乳剂朝镜头', 'Emulsion toward lens'), 'emulsion')
        self.facing.addItem(text('片基朝镜头（反装）', 'Base toward lens (reverse loading)'), 'base')
        self.facing.setCurrentIndex(self.facing.findData(config.exposure.film_side))
        form.addRow(text('装片方向', 'Film orientation'), self.facing)
        from film_foundry.contracts.film_exposure import rear_optics
        values = rear_optics(config.exposure.rear_optics)
        group = QtWidgets.QGroupBox(text('反装光路', 'Reverse exposure'))
        optics = QtWidgets.QGridLayout(group)
        self.rear_controls = {}
        for row, (key, title, choices) in enumerate((
            ('profile', text('彩色响应', 'Color response'), [('generic',text('通用','Generic')),('harman_red_125',text('HARMAN RED 125 曲线参考','HARMAN RED 125 wedge reference'))]),
            ('mirror', text('画面方向','Scene orientation'), [('horizontal',text('左右翻转','Mirror horizontally')),('vertical',text('上下翻转','Mirror vertically')),('none',text('关闭镜像（保持相机构图）','Off (camera orientation)'))]),
        )):
            combo = QtWidgets.QComboBox()
            for code, label in choices:
                combo.addItem(label, code)
            combo.setCurrentIndex(combo.findData(values[key]))
            self.rear_controls[key] = combo
            optics.addWidget(QtWidgets.QLabel(title),row,0)
            optics.addWidget(combo,row,1,1,3)
        for i, (key, title, lo, hi) in enumerate((
            ('thickness_um',text('片基厚度 / µm','Support / µm'),10,350),
            ('index',text('折射率','Refractive index'),1,2),
            ('f_number',text('拍摄光圈 f/','Taking aperture f/'),.7,128),
            ('focus_shift_um',text('调焦补偿 / µm','Refocus / µm'),-500,500),
        )):
            control = spin(values[key],lo,hi,2)
            self.rear_controls[key] = control
            optics.addWidget(QtWidgets.QLabel(title),2+i//2,2*(i%2))
            optics.addWidget(control,2+i//2,2*(i%2)+1)
        form.addRow(group)
        self.auto_refocus = QtWidgets.QCheckBox(text('自动补偿焦面偏移','Automatically compensate focus shift'))
        self.auto_refocus.setChecked(values['auto_refocus'])
        optics.addWidget(self.auto_refocus,4,0,1,4)
        focus = self.rear_controls['focus_shift_um']
        focus.setDisabled(self.auto_refocus.isChecked())
        self.auto_refocus.toggled.connect(focus.setDisabled)
        def show_refocus():
            shift = self.rear_controls['thickness_um'].value()/self.rear_controls['index'].value()
            self.auto_refocus.setText(text('自动补偿焦面偏移','Automatically compensate focus shift') + f' · {shift:.2f} µm')
        for key in ('thickness_um','index'):
            self.rear_controls[key].valueChanged.connect(show_refocus)
        show_refocus()
        self.facing.currentIndexChanged.connect(lambda: group.setVisible(self.facing.currentData()=='base'))
        group.setVisible(self.facing.currentData()=='base')
        self.filters = QtWidgets.QComboBox()
        for key, label in FILTER_LABELS.items():
            self.filters.addItem(text(*label), key)
        setting = config.exposure.lens_filter or {}
        self.filters.setCurrentIndex(max(0, self.filters.findData(setting.get('key'))))
        form.addRow(text('镜前滤镜','Lens filter'), self.filters)
        self.filter_rgb = QtWidgets.QWidget()
        line = QtWidgets.QHBoxLayout(self.filter_rgb)
        line.setContentsMargins(0,0,0,0)
        self.rgb_fields = []
        for channel, fraction in zip("RGB", setting.get("transmission", (1.,1.,1.))):
            line.addWidget(QtWidgets.QLabel(channel))
            field = spin(100*fraction, 0., 100., 3)
            field.setSuffix(" %")
            line.addWidget(field)
            self.rgb_fields.append(field)
        form.addRow(self.filter_rgb)
        self.filter_rgb.setToolTip(text("镜前透射先作用于线性 RGB，再由材料换算曝光。", "Transmission acts on linear RGB before material exposure mapping."))
        self.compensation = QtWidgets.QCheckBox(text('补偿中性光损失','Compensate neutral-light loss'))
        self.compensation.setChecked(setting.get('compensate', False))
        self.meter = QtWidgets.QLabel()
        form.addRow(text('当前材料', 'Stock'), QtWidgets.QLabel(self.film.name))
        self.rear_controls["profile"].setEnabled(not self.film.physical_coating_optics)
        self.rear_controls["profile"].setToolTip(text("沿用材料校准时可选择反装响应。独立光路按片基和涂层顺序计算。",
                                                     "Select a base-first response when using stock calibration. Independent optics use the support and coating order."))

        form.addRow(text('镜前滤镜测光', 'Lens filter metering'), self.meter)
        form.addRow('', self.compensation)
        self.filters.currentIndexChanged.connect(self.update_meter)
        for field in self.rgb_fields:
            field.valueChanged.connect(self.update_meter)
        self.facing.currentIndexChanged.connect(self.update_meter)
        self.update_meter()
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
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText(text('应用', 'Apply'))
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText(text('取消', 'Cancel'))
        buttons.accepted.connect(self.apply); buttons.rejected.connect(self.reject); form.addRow(buttons)
        self.resize(max(480, self.sizeHint().width()), self.sizeHint().height())

    def update_meter(self):
        from film_foundry.core.lens_filters import filter_loss_ev
        key = self.filters.currentData()
        self.filter_rgb.setVisible(key == "custom")
        if key == "custom":
            from film_foundry.contracts.lens_filters import lens_filter
            values = [field.value()/100 for field in self.rgb_fields]
            key = lens_filter(key, False, values)
            if not any(values):
                self.compensation.setChecked(False)
                self.compensation.setEnabled(False)
                self.meter.setText(text("完全遮光，无法补偿。", "Opaque filter; exposure compensation unavailable."))
                return
        self.compensation.setEnabled(key is not None)
        loss = filter_loss_ev(self.film, key, self.facing.currentData()) if key else 0.
        if loss == float("inf"):
            self.compensation.setChecked(False)
            self.compensation.setEnabled(False)
            self.meter.setText(text("材料无有效受光，无法补偿。","No effective exposure; compensation unavailable."))
            return
        self.meter.setText(text('预计损光 ', 'Estimated loss ') + f'{loss:.2f} EV'
                           + text('   ·   曝光倍率 ', '   ·   Exposure factor ') + f'{2. ** loss:.2f}×')

    def resolve(self):
        from film_foundry.core.film_process.reciprocity import duration_for_loss
        try:
            value = duration_for_loss(self.response, self.loss.value(), branch=self.branch.currentData())
            self.seconds.setValue(value); self.enabled.setChecked(True); self.status.clear()
        except ValueError as exc: self.status.setText(str(exc))

    def apply(self):
        try:
            self.rear = ({key: w.currentData() if key in ('profile','mirror') else w.value()
                          for key,w in self.rear_controls.items()} if self.facing.currentData()=='base' else None)
            if self.rear is not None:
                self.rear['auto_refocus'] = self.auto_refocus.isChecked()
            self.duration = validate_duration(self.seconds.value()) if self.enabled.isChecked() else None
            from film_foundry.contracts.lens_filters import lens_filter
            key = self.filters.currentData()
            values = [field.value()/100 for field in self.rgb_fields] if key == "custom" else None
            self.lens_filter = lens_filter(key, self.compensation.isChecked(), values) if key else None
            self.accept()
        except ValueError as exc: self.status.setText(str(exc))
