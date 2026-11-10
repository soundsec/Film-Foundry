"""Edit explicit solution capability; no frontend-owned kinetics."""
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.numeric_row import NumericRow, ParameterNumber, polish_form
from film_foundry.ui.curve_editor import text
from film_foundry.contracts.bath_solution import (MODEL,MODEL_V2,MODEL_V3,COUPLED_ACTIONS,RANGES,
    validate_solution,DEFAULTS,DEVELOPING_ACTIONS,FORMATION_RANGES,FORMATION_DEFAULTS)
from film_foundry.contracts.bath_domains import BATH_DOMAINS

LABELS={'rate_per_minute':('反应速率常数 1/min','Reaction rate 1/min'),
    'q10':('温度系数 Q10','Temperature coefficient Q10'),
    'reference_temperature_c':('参考温度 °C','Reference temperature °C'),
    'activity_remaining':('药液剩余活性','Remaining solution activity'),
    'hardener_g_l':('坚膜剂等效浓度 g/L','Hardener equivalent g/L')}
FORMATION_LABELS={
    'granularity_factor':('颗粒度系数','Granularity factor'),
    'grain_radius_factor':('颗粒相关半径系数','Grain correlation radius factor'),
    'grain_clumping_factor':('颗粒团聚系数','Grain clumping factor'),
    'chemical_fog_factor':('化学灰雾系数','Chemical fog factor')}
FORMATION_TIPS={
    'granularity_factor':('控制该浴新形成产物的密度起伏幅度，1 沿用显影液类型与工艺条件。0 不产生该浴的颗粒起伏。不是实测 RMS 颗粒度。','Scales density fluctuations in products formed in this bath. 1 retains the developer/condition response; 0 disables their grain amplitude. Not a measured RMS granularity.'),
    'grain_radius_factor':('控制该浴新形成产物的颗粒相关长度；不是微观银晶粒直径。1 为原响应。','Scales the grain correlation length of newly formed products, not microscopic silver crystal diameter. 1 retains the original response.'),
    'grain_clumping_factor':('控制该浴新形成产物的粗尺度颗粒分量。只影响现有多尺度模型，不模拟晶粒生长。','Scales coarse grain components for newly formed products in the existing multiscale model; it does not simulate crystal growth.'),
    'chemical_fog_factor':('控制该浴中未曝光银盐的非成像显影；消耗真实银盐。0 抑制该浴化学灰雾，不消除已有灰雾、片基密度或曝光。','Scales non-image development of unexposed halide in this bath, consuming actual halide. 0 suppresses new chemical fog; existing fog, support density and exposure remain.')}

class BathSolutionDialog(QtWidgets.QDialog):
    def __init__(self,action,value=None,parent=None,*,formation_available=True):
        super().__init__(parent);self.action=action;self.value=value
        self.setWindowTitle(text('药液特性','Solution properties'));self.resize(740,580)
        layout=QtWidgets.QVBoxLayout(self)
        library=QtWidgets.QHBoxLayout();layout.addLayout(library)
        self.library=QtWidgets.QComboBox();library.addWidget(self.library,1)
        load=QtWidgets.QPushButton(text('载入药水','Load solution'));library.addWidget(load);load.clicked.connect(self.load_preset)
        save=QtWidgets.QPushButton(text('保存药水…','Save solution…'));library.addWidget(save);save.clicked.connect(self.save_preset)
        self.refresh_presets()
        self.enabled=QtWidgets.QCheckBox(text('使用独立药液能力','Use independent solution capability'))
        self.enabled.setChecked(value is not None);layout.addWidget(self.enabled)
        self.body=QtWidgets.QTabWidget();layout.addWidget(self.body,1)
        reaction=QtWidgets.QWidget();form=QtWidgets.QFormLayout(reaction)
        reaction_scroll=QtWidgets.QScrollArea();reaction_scroll.setWidgetResizable(True);reaction_scroll.setWidget(reaction)
        self.body.addTab(reaction_scroll,text('反应动力学','Reaction kinetics'))
        values=validate_solution(value or {'model':MODEL,'action':action},action);self.controls={}
        self.original_values=values
        for key,(low,high) in RANGES.items():
            spin=ParameterNumber();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.05);spin.setValue(values[key])
            self.controls[key]=spin;form.addRow(text(*LABELS[key]),NumericRow(spin))
        self.controls['q10'].setToolTip(text('温度每升高 10 °C，反应速率乘以此系数。','Reaction rate multiplier for each 10 °C temperature rise.'))
        self.coupled=QtWidgets.QCheckBox(text('同浴定影','Fixing in the same bath'))
        form.addRow(self.coupled);self.secondary={}
        self.secondary_body=QtWidgets.QWidget();secondary_form=QtWidgets.QFormLayout(self.secondary_body)
        for key,(low,high) in RANGES.items():
            if key=='hardener_g_l':continue
            spin=ParameterNumber();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.05)
            spin.setValue(values.get('secondary',{}).get(key,DEFAULTS[key]));self.secondary[key]=spin
            secondary_form.addRow(text(*LABELS[key]),NumericRow(spin))
        form.addRow(self.secondary_body)
        self.display_values={key:control.value() for key,control in self.controls.items()}
        self.secondary_display={key:control.value() for key,control in self.secondary.items()}
        self.coupled.setChecked('secondary' in values)
        self.coupled.setVisible(action in COUPLED_ACTIONS)
        self.secondary_body.setVisible(self.coupled.isChecked())
        self.coupled.toggled.connect(self.secondary_body.setVisible)
        self.coupled.setToolTip(text('漂白银＋定影用于漂定同浴；银显影＋定影用于显定同浴。两种反应在同一时钟内分8段交错计算，增加计算量，不增加实际工艺时间。定影组分独立设置速率和剩余活性。',
            'Bleach/fix or silver development/fix share one bath clock in 8 bounded reaction intervals. This adds computation, not bath duration. Fixing has its own rate and activity.'))
        formation=QtWidgets.QWidget();formation_form=QtWidgets.QFormLayout(formation)
        self.formation_enabled=QtWidgets.QCheckBox(text('自定义颗粒与灰雾响应','Custom grain and fog response'))
        self.formation_enabled.setChecked('formation_response' in values);formation_form.addRow(self.formation_enabled)
        self.formation_controls={}
        for key,(low,high) in FORMATION_RANGES.items():
            spin=ParameterNumber();spin.setDecimals(6);spin.setRange(low,high);spin.setSingleStep(.05)
            spin.setValue(values.get('formation_response',FORMATION_DEFAULTS)[key]);spin.setToolTip(text(*FORMATION_TIPS[key]))
            label=QtWidgets.QLabel(text(*FORMATION_LABELS[key]));label.setToolTip(spin.toolTip())
            formation_form.addRow(label,NumericRow(spin));self.formation_controls[key]=spin
            spin.setEnabled(self.formation_enabled.isChecked());self.formation_enabled.toggled.connect(spin.setEnabled)
        self.formation_display={key:control.value() for key,control in self.formation_controls.items()}
        if action in DEVELOPING_ACTIONS:self.body.addTab(formation,text('颗粒与灰雾','Grain and fog'))
        else:formation.setParent(self);formation.hide()
        formation.setEnabled(formation_available)
        formation.setToolTip(text('需选用逐浴形成模型。响应仅随该浴新生成的银像／染料保存，按实际保留量参与最终颗粒；不重绘已有产物。1 沿用原响应。',
            'Requires a bath-product model. Responses follow silver/dye formed in this bath and their retained mass; existing products are not rewritten. 1 retains the original response.'))
        for layout_form in (form,secondary_form,formation_form):polish_form(layout_form)
        self.body.setEnabled(self.enabled.isChecked());self.enabled.toggled.connect(self.body.setEnabled)
        self.setToolTip(text(*BATH_DOMAINS[action][2])+text(' 启用后由本药液特性和逐浴条件计算完成度；原显影液／定影液类型不再决定该浴反应速率。显影曲线仍保留；颗粒与灰雾页可调整原类型及条件产生的相对响应。取消勾选恢复原路径。',
            ' Explicit kinetics and bath conditions determine completion, replacing the developer/fixer rate profile. Development curves remain; Grain and fog adjusts relative responses from the original type and conditions. Uncheck to restore the original path.'))
        self.status=QtWidgets.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        from film_foundry.ui.control_reset import reset_button
        for group,baseline in ((self.controls,lambda:self.original_values),(self.secondary,lambda:self.original_values.get('secondary',DEFAULTS)),(self.formation_controls,lambda:self.original_values.get('formation_response',FORMATION_DEFAULTS))):
            for key,control in group.items():
                button=reset_button();control.parentWidget().layout().addWidget(button)
                button.setToolTip(text('恢复载入药水时的值','Restore loaded solution value'))
                button.clicked.connect(lambda checked=False,key=key,control=control,baseline=baseline:control.setValue(baseline()[key]))
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        layout.addStretch();layout.addWidget(buttons);buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject)

    def read_value(self):
        payload={'model':MODEL,'action':self.action,**{key:self.original_values[key] if control.value()==self.display_values[key] else control.value() for key,control in self.controls.items()}}
        if self.coupled.isChecked():
            payload.update(model=MODEL_V2,secondary={'action':'fix_halide',**{key:self.original_values.get('secondary',{}).get(key,DEFAULTS[key]) if control.value()==self.secondary_display[key] else control.value() for key,control in self.secondary.items()}})
        if self.action in DEVELOPING_ACTIONS and self.formation_enabled.isChecked():
            payload.update(model=MODEL_V3,formation_response={key:self.original_values.get('formation_response',FORMATION_DEFAULTS)[key]
                if control.value()==self.formation_display[key] else control.value() for key,control in self.formation_controls.items()})
        return validate_solution(payload,self.action) if self.enabled.isChecked() else None

    def apply(self):
        try:self.value=self.read_value();self.accept()
        except (ValueError,TypeError) as exc:self.status.setText(str(exc))

    def refresh_presets(self):
        from film_foundry.app.preset_exchange import library_root,read_preset
        self.library.clear();self.library.addItem(text('我的药水','My solutions'),None)
        for path in sorted((library_root()/'solution').glob('*.json')):
            try:item=read_preset(path,'solution')
            except (OSError,ValueError,TypeError):continue
            if item['payload']['action']==self.action:self.library.addItem(item['name'],item['payload'])

    def load_preset(self):
        value=self.library.currentData()
        if value is None:return
        values=validate_solution(value,self.action);self.original_values=values;self.enabled.setChecked(True)
        for key,control in self.controls.items():control.setValue(values[key])
        for key,control in self.secondary.items():control.setValue(values.get('secondary',{}).get(key,DEFAULTS[key]))
        for key,control in self.formation_controls.items():control.setValue(values.get('formation_response',FORMATION_DEFAULTS)[key])
        self.coupled.setChecked('secondary' in values);self.formation_enabled.setChecked('formation_response' in values)
        self.display_values={key:c.value() for key,c in self.controls.items()}
        self.secondary_display={key:c.value() for key,c in self.secondary.items()}
        self.formation_display={key:c.value() for key,c in self.formation_controls.items()}

    def save_preset(self):
        from film_foundry.app.preset_exchange import save_items
        try:
            value=self.read_value()
            if value is None:raise ValueError(text('请先启用药液能力','Enable solution capability first'))
            name,ok=QtWidgets.QInputDialog.getText(self,text('保存药水','Save solution'),text('名称','Name'))
            if not ok:return
            paths=save_items([{'kind':'solution','name':name,'payload':value}]);self.refresh_presets()
            self.status.setText(text('已保存：','Saved: ')+paths[0].name)
        except (ValueError,TypeError,OSError) as exc:self.status.setText(str(exc))
