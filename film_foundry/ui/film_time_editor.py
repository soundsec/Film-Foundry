"""Film clocks and material response only; all calculations live in core."""
from copy import deepcopy
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.contracts.film_time import MODELS,RESPONSE_MODEL,RESPONSE_MODEL_V2,VECTORS,SCALARS,validate_history,validate_response

FIELDS={
 'storage':(('duration_days','时长 / 天','Duration / days',365.),('temperature_c','温度 °C','Temperature °C',20.),('relative_humidity','相对湿度 0–1','Relative humidity 0–1',.5)),
 'latent':(('duration_hours','时长 / 小时','Duration / hours',24.),('temperature_c','温度 °C','Temperature °C',20.),('relative_humidity','相对湿度 0–1','Relative humidity 0–1',.5)),
 'light':(('duration_hours','时长 / 小时','Duration / hours',1.),('relative_illuminance','相对光强','Relative illuminance',1.),('r','光源 R','Light R',1.),('g','光源 G','Light G',1.),('b','光源 B','Light B',1.)),
}
LABELS={
 'storage_speed_loss_stops_per_year':('储存失速 stop/年','Storage speed loss stops/year'),
 'storage_fog_rate_per_year':('储存灰雾率 /年','Storage fog rate/year'),
 'latent_fade_rate_per_day':('潜影衰退率 /天','Latent fading rate/day'),
 'latent_fog_rate_per_day':('潜影等待生雾率 /天','Latent storage fog rate/day'),
 'printout_rate_per_light_hour':('残盐光转化率 /光小时','Residual print-out rate/light-hour'),
 'temperature_q10':('升温10°C速率倍数','Rate factor per 10°C'),
 'humidity_sensitivity':('湿度响应','Humidity response'),
 'bleached_printout_ratio':('漂白盐受光相对响应','Bleached salt light response'),
 'printout_grain_factor':('光转化银颗粒增益','Print-out silver grain gain'),
 'printout_grain_radius_factor':('光转化银颗粒半径','Print-out silver grain radius'),
 'printout_grain_clump_factor':('光转化银团聚响应','Print-out silver clumping'),
}

TARGET_LABELS = {
 'storage': ('感度损失 / 档', 'Speed loss / stops'),
 'latent': ('原始潜影反差损失', 'Original latent contrast loss'),
 'light': ('残留原始银盐转化', 'Residual original salt conversion'),
}
TARGET_HELP = {
 'storage': ('目标是本段各层平均感度损失；更长储存也增加灰雾，各层差异可偏色。只影响新曝光，不重写已有潜影。',
             'Target is this segment\'s mean layer speed loss. Longer storage also increases fog; unequal layers can shift colour. Applies to new exposures only.'),
 'latent': ('目标是本段对原始潜影层间平均记忆的损失：原有激活差异按该比例缩小，同时向材料生雾平衡移动。不是成片反差或单纯变淡。',
            'Target is mean layer loss of original latent memory in this segment: initial activation differences contract while fog moves activation toward equilibrium. This is not final image contrast or simple fading.'),
 'light': ('目标是本段剩余原始银盐的各层平均转银比例。漂白盐和表面盐按各自响应同时变化；无残盐就无成像，已移除的盐不恢复，不生成染料。',
           'Target is mean layer conversion of surviving original halide in this segment. Bleached and surface salt respond to the same light. No residual salt means no formation; removed salt cannot return and no dye is added.'),
}


class TimeHistoryEditor(QtWidgets.QWidget):
    def __init__(self,kind,value=None,parent=None,*,config_provider=None):
        super().__init__(parent);self.kind=kind;self.config_provider=config_provider;value=validate_history(value,kind)
        layout=QtWidgets.QVBoxLayout(self)
        self.enabled=QtWidgets.QCheckBox(text('启用此阶段','Enable this stage'));self.enabled.setChecked(value is not None);layout.addWidget(self.enabled)
        self.table=QtWidgets.QTableWidget(0,len(FIELDS[kind]));self.table.setHorizontalHeaderLabels([text(zh,en) for _,zh,en,_ in FIELDS[kind]])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch);layout.addWidget(self.table)
        buttons=QtWidgets.QHBoxLayout();layout.addLayout(buttons)
        add=QtWidgets.QPushButton(text('添加一段','Add segment'));remove=QtWidgets.QPushButton(text('移除选中','Remove selected'));buttons.addWidget(add);buttons.addWidget(remove);buttons.addStretch()
        add.clicked.connect(lambda:self.add_row());remove.clicked.connect(self.remove_row)
        for row in value['segments'] if value else []:self.add_row(row)
        if not self.table.rowCount():self.add_row()
        self.table.setEnabled(self.enabled.isChecked());self.enabled.toggled.connect(self.table.setEnabled)
        self.setToolTip(text('按行顺序执行明确时长，不读取系统时钟。储存和等待以20°C、湿度0.5为参考；受光使用相对光强，不等于实测照度。','Segments run in row order with explicit durations, never the wall clock. Storage uses 20°C and humidity 0.5 as reference; light intensity is relative, not calibrated lux.'))
        if config_provider is not None:
            target=QtWidgets.QHBoxLayout();layout.addLayout(target)
            target.addWidget(QtWidgets.QLabel(text(*TARGET_LABELS[kind])))
            self.target=QtWidgets.QDoubleSpinBox();self.target.setDecimals(6)
            self.target.setRange(0.,100. if kind=='storage' else 1.);self.target.setValue(1. if kind=='storage' else .25);target.addWidget(self.target)
            self.solve=QtWidgets.QPushButton(text('换算选中段时长','Resolve selected duration'));target.addWidget(self.solve);target.addStretch()
            self.target_status=QtWidgets.QLabel();self.target_status.setWordWrap(True);layout.addWidget(self.target_status)
            self.solve.clicked.connect(self.resolve_target)
            self.table.currentCellChanged.connect(self.refresh_target);self.table.itemChanged.connect(self.refresh_target)
            self.enabled.toggled.connect(self.refresh_target)
            self.table.setCurrentCell(0,0);self.refresh_target()

    def add_row(self,value=None):
        if self.table.rowCount()>=32:return
        value=dict(value or {})
        if 'light_rgb' in value:value.update(zip(('r','g','b'),value['light_rgb']))
        row=self.table.rowCount();self.table.insertRow(row)
        for j,(key,_,_,default) in enumerate(FIELDS[self.kind]):self.table.setItem(row,j,QtWidgets.QTableWidgetItem(str(value.get(key,default))))
        self.table.setCurrentCell(row,0)

    def remove_row(self):
        row=self.table.currentRow()
        if row>=0:self.table.removeRow(row)
        self.refresh_target()

    def read_row(self,i):
        row={key:float(self.table.item(i,j).text()) for j,(key,*_) in enumerate(FIELDS[self.kind])}
        if self.kind=='light':row['light_rgb']=[row.pop(k) for k in ('r','g','b')]
        return row

    def target_conditions(self):
        row=self.table.currentRow()
        if row<0:raise ValueError(text('请选择一个时间段','Select a time segment'))
        conditions=self.read_row(row)
        conditions.pop('duration_days' if self.kind=='storage' else 'duration_hours')
        return conditions

    def refresh_target(self,*_):
        if not hasattr(self,'solve'):return
        from film_foundry.app.film_time_targets import describe_targets
        hint=text(*TARGET_HELP[self.kind])+' '+text('只改选中段时长，其他段与条件保留。关闭此阶段可旁路；六项事故总开关不控制已换算的时间段。',
            'Only the selected duration changes; other segments and conditions stay. Disable this stage to bypass it; the six-incident switch does not control resolved time histories.')
        try:
            report=describe_targets(self.config_provider(),self.kind,conditions=self.target_conditions())
            maximum=report['reachable']['maximum'];operator='≤' if report['reachable']['maximum_inclusive'] else '<'
            hint+=' '+text('当前条件范围：0 ≤ 目标 ','Current range: 0 ≤ target ')+f'{operator} {maximum:.8g}'
            if not report['available']:
                hint+=' '+text('需启用材料时间响应、使用物质池程序，且当前条件下响应非零。','Requires enabled stock time response, a material-pool program, and nonzero response at these conditions.')
            enabled=self.enabled.isChecked() and report['available']
        except (ValueError,TypeError,AttributeError) as exc:
            enabled=False;hint+=' '+str(exc)
        self.solve.setEnabled(enabled);self.solve.setToolTip(hint);self.target.setToolTip(hint)

    def resolve_target(self):
        from film_foundry.app.film_time_targets import resolve_target
        try:
            result=resolve_target(self.config_provider(),self.kind,self.target.value(),conditions=self.target_conditions())
            duration=result['segment']['duration_days' if self.kind=='storage' else 'duration_hours']
            self.table.item(self.table.currentRow(),0).setText(repr(duration));self.target_status.clear()
        except (ValueError,TypeError,AttributeError) as exc:self.target_status.setText(str(exc))

    def read(self):
        if not self.enabled.isChecked():return None
        rows=[]
        for i in range(self.table.rowCount()):
            rows.append(self.read_row(i))
        return validate_history({'model':MODELS[self.kind],'segments':rows},self.kind)


class FilmTimeDialog(QtWidgets.QDialog):
    def __init__(self,config,parent=None,*,material=False):
        super().__init__(parent);self.material=material;self.base_config=deepcopy(config);self.setWindowTitle(text('胶片时间过程','Film time history'));self.resize(780,560)
        outer=QtWidgets.QVBoxLayout(self);tabs=QtWidgets.QTabWidget();outer.addWidget(tabs);self.controls={}
        if material:
            page=QtWidgets.QWidget();form=QtWidgets.QFormLayout(page);scroll=QtWidgets.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(page);tabs.addTab(scroll,text('材料时间响应','Material time response'))
            self.enabled=QtWidgets.QCheckBox(text('启用材料时间响应','Enable material time response'));self.enabled.setChecked(config.film.time_response is not None);form.addRow(self.enabled)
            values=validate_response(config.film.time_response or {'model':RESPONSE_MODEL_V2})
            self.surface_light=QtWidgets.QCheckBox(text('表面残盐也参与受光','Include surface salt in light exposure'))
            self.surface_light.setChecked(values['model']==RESPONSE_MODEL_V2);form.addRow(self.surface_light)
            self.surface_light.setEnabled(self.enabled.isChecked());self.enabled.toggled.connect(self.surface_light.setEnabled)
            self.surface_light.setToolTip(text('表面漂白盐沿同一末段受光条件转为等量银，保留原附着位置与乳剂归属。使用各层光剂量的均值及漂白盐相对响应；已定影或移出的盐不再反应，不额外生成染料。旧v1材料需明确开启。','Surface bleached salt converts to equal silver-equivalent mass in the same final light stage, preserving attachment. Uses mean layer dose and the bleached-salt response; fixed or removed salt cannot react, and no dye is generated. Enable explicitly for legacy v1 stocks.'))
            for key in (*VECTORS,*SCALARS):
                container=QtWidgets.QWidget();line=QtWidgets.QHBoxLayout(container);line.setContentsMargins(0,0,0,0)
                fields=values[key] if key in VECTORS else [values[key]];widgets=[]
                for value in fields:
                    control=QtWidgets.QDoubleSpinBox();control.setDecimals(6);control.setRange(0.,10.) if key in VECTORS else control.setRange(*SCALARS[key][1:]);control.setValue(value);line.addWidget(control);widgets.append(control)
                self.controls[key]=widgets;form.addRow(text(*LABELS[key]),container)
                container.setEnabled(self.enabled.isChecked());self.enabled.toggled.connect(container.setEnabled)
            page.setToolTip(text('三列依次为材料R/G/B感光层，黑白材料取均值。系数是创作型降阶响应，不是品牌材料标定。曝光后随材料冻结。','Columns are material R/G/B-sensitive layers; monochrome uses their mean. These are creative reduced responses, not calibrated stock constants; they freeze at exposure.'))
            self.storage=TimeHistoryEditor('storage',config.film.storage_history,config_provider=self.target_config);tabs.addTab(self.storage,text('曝光前储存','Storage before exposure'))
            self.enabled.toggled.connect(self.storage.refresh_target);self.surface_light.toggled.connect(self.storage.refresh_target)
            for controls in self.controls.values():
                for control in controls:control.valueChanged.connect(self.storage.refresh_target)
            from film_foundry.ui.film_exposure_editor import ReciprocityEditor
            self.reciprocity=ReciprocityEditor(config.film.reciprocity_response)
            tabs.addTab(self.reciprocity,text('互易律响应','Reciprocity response'))
        else:
            self.waiting=TimeHistoryEditor('latent',config.chemistry.latent_wait,config_provider=self.target_config);tabs.addTab(self.waiting,text('潜影等待','Latent waiting'))
            self.light=TimeHistoryEditor('light',config.chemistry.post_process_light,config_provider=self.target_config);tabs.addTab(self.light,text('冲洗后受光','Light after processing'))
        self.status=QtWidgets.QLabel();outer.addWidget(self.status)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);outer.addWidget(buttons)

    def target_config(self):
        config=deepcopy(self.base_config)
        if self.material:
            config.film.time_response=self.read_response()
        return config

    def read_response(self):
        if not self.enabled.isChecked():return None
        return validate_response({'model':RESPONSE_MODEL_V2 if self.surface_light.isChecked() else RESPONSE_MODEL,
            **{k:([w.value() for w in ws] if k in VECTORS else ws[0].value()) for k,ws in self.controls.items()}})

    def apply(self):
        try:
            if self.material:
                self.response=self.read_response()
                self.storage_value=self.storage.read()
                self.reciprocity_value=self.reciprocity.read()
                if self.storage_value is not None and self.response is None:raise ValueError(text('请先启用材料时间响应','Enable material time response first'))
            else:self.wait_value=self.waiting.read();self.light_value=self.light.read()
            self.accept()
        except (ValueError,TypeError,AttributeError) as exc:self.status.setText(str(exc))
