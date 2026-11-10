"""Reduced scanner editor using the same curve and colour controls as film."""
from copy import deepcopy
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text,CurveEditor
from film_foundry.ui.color_mixer import ColorMixerEditor
from film_foundry.app.material_scan import DEFAULTS,normalize_adjustments


class MaterialScanEditor(QtWidgets.QDialog):
    def __init__(self,settings,parent=None):
        super().__init__(parent)
        self.setWindowTitle(text('扫描与成片','Scan & output'));self.setMinimumSize(540,460)
        self.base=normalize_adjustments(settings);self.result_settings=deepcopy(self.base)
        layout=QtWidgets.QVBoxLayout(self);tabs=QtWidgets.QTabWidget()
        split=QtWidgets.QSplitter();split.addWidget(tabs);layout.addWidget(split,1)
        basic=QtWidgets.QWidget();form=QtWidgets.QFormLayout(basic);tabs.addTab(basic,text('影调','Tone'))
        self.controls={};self.sliders={}
        for key,zh,en,lo,hi in [('print_gamma','反差','Contrast',.01,8),('scan_saturation','饱和度','Saturation',0,4),
                              ('scan_black_percentile','黑位百分位','Black percentile',0,100),
                              ('scan_white_percentile','白位百分位','White percentile',0,100),
                              ('scan_normalize_strength','黑白位校正强度','Endpoint strength',0,1)]:
            control=QtWidgets.QDoubleSpinBox();control.setDecimals(3);control.setRange(lo,hi);control.setKeyboardTracking(False)
            self.controls[key]=control;control.setMaximumWidth(130)
            if key in ('print_gamma','scan_saturation','scan_normalize_strength'):
                row=QtWidgets.QHBoxLayout();slider=QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal);slider.setRange(round(lo*1000),round(hi*1000))
                self.sliders[key]=slider
                slider.valueChanged.connect(lambda v,c=control:c.setValue(v/1000));control.valueChanged.connect(lambda v,s=slider:s.setValue(round(v*1000)))
                row.addWidget(slider,1);row.addWidget(control);form.addRow(text(zh,en),row)
            else:form.addRow(text(zh,en),control)
        self.normalize=QtWidgets.QCheckBox(text('自动黑白位','Auto black and white points'));form.addRow(self.normalize)
        self.normalize.setToolTip(text('只按亮度确定黑白位，白边不参与测量；保留材料色调和用户分色偏好。','Luminance endpoints measured without the border; preserves material tint and colour preferences.'))
        self.normalize.toggled.connect(self.sync)
        color=QtWidgets.QWidget();color_form=QtWidgets.QFormLayout(color);tabs.addTab(color,text('色彩','Colour'))
        self.balance=[]
        for index,name in enumerate(('R','G','B')):
            control=QtWidgets.QDoubleSpinBox();control.setDecimals(3);control.setRange(0,4);control.setKeyboardTracking(False)
            color_form.addRow(text(name+' 通道增益',name+' channel gain'),control);self.balance.append(control)
        self.mixer=ColorMixerEditor();color_form.addRow(self.mixer)
        self.curves={}
        for key,zh,en in [('tone_curve','影调曲线','Tone curve'),('color_curve_rgb','RGB 曲线','RGB curves')]:
            editor=CurveEditor(key);self.curves[key]=editor;tabs.addTab(editor,text(zh,en))
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        reset=QtWidgets.QPushButton(text('恢复中性','Reset to neutral'));reset.clicked.connect(lambda:self.load({}));layout.addWidget(reset)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.setToolTip(text('复用胶片扫描器的正像渲染、黑白位、曲线和分色调整；反射材料保留自身观察方式，不执行负片反相或去色罩。','Uses the film scanner’s positive rendering, endpoints, curves and colour mixer. Reflection media retain their native optics without negative inversion or base-mask removal.'))
        self.load(self.base)
        if parent is not None and hasattr(parent,'material_values'):
            from dataclasses import asdict
            from film_foundry.ui.material_preview import MaterialPreview
            from film_foundry.app.family_preview import FamilyPreviewSession
            self.preview=MaterialPreview(lambda:{'material':parent.material_values(),'view':asdict(parent.conditions()),
                'scan_adjustments':self.read_settings()},renderer=FamilyPreviewSession(parent.kind))
            self.preview.setToolTip(text('当前材料参考流程的标靶预览；扫描调整复用已形成标靶，不推进实际片体。','Target preview of the material reference process. Scan edits reuse its formation and never advance the actual body.'))
            split.addWidget(self.preview);split.setSizes([460,520])
            for control in (*self.controls.values(),*self.balance):control.valueChanged.connect(self.preview.invalidate)
            self.normalize.toggled.connect(self.preview.invalidate);self.mixer.changed.connect(self.preview.invalidate)
            for editor in self.curves.values():editor.changed.connect(self.preview.invalidate)

    def load(self,values):
        self.base=deepcopy(values)
        for key,control in self.controls.items():control.setValue(values.get(key,DEFAULTS[key]))
        for control,value in zip(self.balance,values.get('print_color_bias',(1.,1.,1.))):control.setValue(value)
        self.normalize.setChecked(values.get('scan_normalize',False));self.mixer.load(values.get('color_mixer'))
        for key,editor in self.curves.items():editor.load(values.get(key))
        self.sync()

    def sync(self,*_):
        for key in ('scan_black_percentile','scan_white_percentile','scan_normalize_strength'):self.controls[key].setEnabled(self.normalize.isChecked())
        self.sliders['scan_normalize_strength'].setEnabled(self.normalize.isChecked())

    def read_settings(self):
        values={**self.base,**{key:control.value() for key,control in self.controls.items()},
                'scan_normalize':self.normalize.isChecked(),'print_color_bias':tuple(c.value() for c in self.balance),
                'color_mixer':self.mixer.read(),**{key:editor.read() for key,editor in self.curves.items()}}
        return normalize_adjustments(values)

    def accept(self):
        try:self.result_settings=self.read_settings()
        except (ValueError,TypeError) as exc:self.status.setText(str(exc));return
        super().accept()
