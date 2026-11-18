"""One display preference model shared by histograms and the settings dialog."""
from film_foundry.ui.qt import QtCore, QtGui, QtWidgets
from film_foundry.ui.preferences import load_preferences, save_preferences, preferences_path
from film_foundry.ui.curve_editor import text


DEFAULTS = dict(layout='sidebar', kind='curve', scale='linear', filled=True, fill_style='solid',
                channels=['R','G','B'], shadows=False, highlights=False)
CHOICES = {
    'layout': (('sidebar','侧栏 · 紧凑','Sidebar · compact'),('below','预览下方 · 长图','Below preview · wide')),
    'kind': (('curve','曲线','Curve'),('bars','柱状','Bars')),
    'scale': (('linear','线性','Linear'),('log','对数','Logarithmic')),
    'fill_style': (('outline','镂空 · 仅轮廓','Outline only'),('translucent','半透明填充','Translucent fill'),('solid','实色填充 · RGB 混色','Solid fill · RGB overlap')),
}
LABELS = {'layout':('布局','Layout'),'kind':('类型','Type'),'scale':('纵轴','Vertical scale'),'fill_style':('绘制方式','Drawing')}
WARNINGS = {'shadows':('欠曝 / 阴影裁切提示（蓝）','Shadow clipping overlay (blue)'),
            'highlights':('过曝 / 高光裁切提示（红）','Highlight clipping overlay (red)')}


def normalise(value):
    value = value if isinstance(value,dict) else {}
    result = dict(DEFAULTS)
    for key, choices in CHOICES.items():
        if value.get(key) in [entry[0] for entry in choices]:result[key]=value[key]
    if 'fill_style' not in value and type(value.get('filled')) is bool:
        result['fill_style']='translucent' if value['filled'] else 'outline'
    result['filled']=result['fill_style']!='outline'
    for key in ('shadows','highlights'):
        if type(value.get(key)) is bool:result[key]=value[key]
    channels=value.get('channels')
    result['channels'] = ([c for c in ('R','G','B','L') if c in channels]
        if isinstance(channels,list) and all(c in ('R','G','B','L') for c in channels)
        else list(DEFAULTS['channels']))
    return result


class HistogramSettings(QtCore.QObject):
    changed = QtCore.Signal(object)
    _models = {}

    def __init__(self,path):
        super().__init__(QtWidgets.QApplication.instance())
        self.path=path
        self.values=normalise(load_preferences(path).get('histogram'))

    @classmethod
    def current(cls):
        path=preferences_path()
        if path not in cls._models:cls._models[path]=cls(path)
        return cls._models[path]

    def update(self,**changes):
        if 'filled' in changes and 'fill_style' not in changes:
            changes['fill_style']='translucent' if changes['filled'] else 'outline'
        value=normalise(dict(self.values,**changes))
        if value==self.values:return
        save_preferences(histogram=value,path=self.path)
        self.values=value
        self.changed.emit(dict(value))

    def set_channel(self,channel,enabled):
        channels=set(self.values['channels'])
        if enabled:channels.add(channel)
        else:channels.discard(channel)
        self.update(channels=[c for c in ('R','G','B','L') if c in channels])

    def reset(self):self.update(**DEFAULTS)


def settings_menu(model,parent):
    menu=QtWidgets.QMenu(parent)
    for key,choices in CHOICES.items():
        section=QtWidgets.QMenu(text(*LABELS[key]),menu);menu.addMenu(section);group=QtGui.QActionGroup(section)
        for value,zh,en in choices:
            action=section.addAction(text(zh,en));action.setObjectName(key+'.'+value)
            action.setCheckable(True);action.setChecked(model.values[key]==value);group.addAction(action)
            action.triggered.connect(lambda checked=False,k=key,v=value:model.update(**{k:v}))
    channels=QtWidgets.QMenu(text('显示通道 · 可叠加','Channels · combine freely'),menu);menu.addMenu(channels)
    for channel in ('R','G','B','L'):
        action=channels.addAction(channel if channel!='L' else text('L · 亮度','L · Luma'))
        action.setObjectName('channel.'+channel);action.setCheckable(True);action.setChecked(channel in model.values['channels'])
        action.triggered.connect(lambda checked,c=channel:model.set_channel(c,checked))
    menu.addSeparator()
    for key,label in WARNINGS.items():
        action=menu.addAction(text(*label));action.setObjectName(key);action.setCheckable(True);action.setChecked(model.values[key])
        action.triggered.connect(lambda checked,k=key:model.update(**{k:checked}))
    menu.addSeparator();menu.addAction(text('恢复直方图默认设置','Reset histogram preferences'),model.reset)
    return menu


class HistogramSettingsPanel(QtWidgets.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.model=HistogramSettings.current();self.controls={}
        layout=QtWidgets.QVBoxLayout(self);form=QtWidgets.QFormLayout();layout.addLayout(form)
        for key,choices in CHOICES.items():
            control=QtWidgets.QComboBox();self.controls[key]=control
            for value,zh,en in choices:control.addItem(text(zh,en),value)
            form.addRow(text(*LABELS[key]),control)
            control.currentIndexChanged.connect(lambda _,k=key,c=control:self.model.update(**{k:c.currentData()}))
        row=QtWidgets.QHBoxLayout();self.channels={}
        for channel in ('R','G','B','L'):
            box=QtWidgets.QCheckBox(channel if channel!='L' else text('L 亮度','L Luma'));self.channels[channel]=box;row.addWidget(box)
            box.toggled.connect(lambda checked,c=channel:self.model.set_channel(c,checked))
        form.addRow(text('通道 · 自由叠加','Channels · any combination'),row)
        for key,label in WARNINGS.items():
            control=QtWidgets.QCheckBox(text(*label));self.controls[key]=control;form.addRow(control)
            control.toggled.connect(lambda checked,k=key:self.model.update(**{k:checked}))
        note=QtWidgets.QLabel(text('直方图选项即时应用并自动保存，与右键菜单同步。小三角显示 RGB 通道到达 0 / 255 的裁切；点击可开关蓝 / 红预览标记。L 为显示亮度。提示不代表 RAW 曝光余量，不写入成片。对比时标记结果区，按住查看原图时跟随原图。',
            'Changes apply and save immediately, in sync with the context menu. Triangles indicate RGB channels reaching 0 / 255; click to toggle blue / red preview overlays. L is display luma. Warnings are not RAW headroom and are never exported. Comparisons mark the result area; holding for the source marks the source.'))
        note.setWordWrap(True);layout.addWidget(note)
        reset=QtWidgets.QPushButton(text('恢复直方图默认设置','Reset histogram preferences'));reset.clicked.connect(self.model.reset);layout.addWidget(reset);layout.addStretch()
        self.model.changed.connect(self.sync);self.sync()

    def sync(self,*_):
        value=self.model.values
        for key,control in self.controls.items():
            previous=control.blockSignals(True)
            if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findData(value[key]))
            else:control.setChecked(value[key])
            control.blockSignals(previous)
        for channel,box in self.channels.items():
            box.blockSignals(True);box.setChecked(channel in value['channels']);box.blockSignals(False)
