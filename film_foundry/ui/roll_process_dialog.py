"""Bath conditions editor for the strict roll workflow."""
from copy import deepcopy
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.numeric_row import NumericRow, ParameterNumber, polish_form
from film_foundry.ui.curve_editor import text
from film_foundry.app.darkroom_process import condition_driven_config,RAW_CONDITIONS,BATH_ACTIONS,editable_bath_rows
from film_foundry.contracts.process_steps import CONDITION_RANGES,validate_process_steps
from film_foundry.contracts.bath_domains import BATH_DOMAINS

FIELDS=(('time_min','反应时间 min','Reaction time min'),('temperature_c','温度 °C','Temperature °C'),
        ('concentration','浓度倍数','Concentration'),('agitation','搅拌速率','Agitation rate'),('agitation_time_s','累计搅拌秒数','Total agitation seconds'),
        ('developer_exhaustion','显影液疲劳','Developer exhaustion'),('fixer_exhaustion','定影液疲劳','Fixer exhaustion'),('bath_transfer_s','入浴变温时间 s','Bath temperature transition s'))
ACTIONS={'develop_silver':('银显影','Silver development'),'develop_color':('彩色显影','Color development'),
         'activate_remaining_halide':('二次曝光／活化','Re-exposure / activation'),'bleach_silver':('漂白银','Silver bleach'),
         'fix_halide':('定影','Fix'),'remove_silver':('去银','Silver removal'),'remove_auxiliary':('清除附加层','Auxiliary clearing'),
         'bleach_mask_dye':('色罩漂白','Mask bleach'),'destroy_dye':('染料损伤','Dye destruction')}


class RollProcessDialog(QtWidgets.QDialog):
    def __init__(self,config,parent=None,*,principle_only=True):
        super().__init__(parent);self.setWindowTitle(text('折腾模式 · 药浴与步骤','Darkroom · Baths & steps'));self.resize(1080,720);self.setMinimumSize(900,620)
        self.principle_only=principle_only
        self.custom_contains_native_damage=False
        self.actions=BATH_ACTIONS if principle_only else (*BATH_ACTIONS,'destroy_dye')
        self.config=condition_driven_config(config) if principle_only else deepcopy(config);recipe=self.config.chemistry
        if not principle_only:self.setWindowTitle(text('流程编辑器','Process editor'))
        outer=QtWidgets.QVBoxLayout(self);self.tabs=QtWidgets.QTabWidget();outer.addWidget(self.tabs)
        baths=QtWidgets.QWidget();self.tabs.addTab(baths,text('药浴','Baths'));layout=QtWidgets.QVBoxLayout(baths);form=QtWidgets.QFormLayout();layout.addLayout(form)
        from film_foundry.ui.process_operations_editor import ProcessOperationsEditor
        self.operations=ProcessOperationsEditor(recipe.process_events);self.tabs.addTab(self.operations,text('操作与事故','Operations & incidents'))
        from film_foundry.ui.film_time_editor import TimeHistoryEditor
        self.latent_wait=TimeHistoryEditor('latent',recipe.latent_wait,config_provider=lambda:self.config);self.tabs.addTab(self.latent_wait,text('潜影等待','Latent waiting'))
        self.post_light=TimeHistoryEditor('light',recipe.post_process_light,config_provider=lambda:self.config);self.tabs.addTab(self.post_light,text('冲洗后受光','Light after processing'))
        self.program=QtWidgets.QComboBox()
        for key,zh,en in (('auto','材料原生程序','Native process'),('bw_negative','黑白负片','B&W negative'),('color_negative','彩色负片','Color negative'),
            ('color_negative_bleach_bypass','彩负跳过漂白','Color bleach bypass'),('bw_reversal','黑白反转','B&W reversal'),('color_reversal','彩色反转','Color reversal')):
            self.program.addItem(text(zh,en),key)
        self.program.setCurrentIndex(max(0,self.program.findData(recipe.program_key)));form.addRow(text('标准流程','Standard process'),self.program)
        self.formation_history=QtWidgets.QComboBox()
        self.formation_history.addItem(text('逐浴局部颗粒响应','Local retained bath response'),'bath_products_local_v2')
        self.formation_history.addItem(text('逐浴保留产物','Retained bath products'),'bath_products_v1')
        self.formation_history.addItem(text('旧版整体近似','Legacy global approximation'),'legacy_global_v1')
        self.formation_history.setCurrentIndex(self.formation_history.findData(recipe.formation_history_model))
        self.formation_history.setToolTip(text('局部模型保留各位置的银像／染料颗粒强度、尺度与团聚响应，最后生成一次颗粒。全局模型只汇总全片。旧配方不会自动升级；切换会改变结果。','The local model retains silver/dye grain gain, scale and clumping per position for one final realization. The global model averages the frame. Old recipes do not upgrade automatically.'))
        form.addRow(text('颗粒与化学灰雾','Grain and chemical fog'),self.formation_history)
        polish_form(form)
        common_toggle=QtWidgets.QToolButton();common_toggle.setText(text('共用药液与条件','Shared chemistry and conditions'))
        common_toggle.setCheckable(True);common_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        common_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow);layout.addWidget(common_toggle)
        common=QtWidgets.QWidget();form=QtWidgets.QFormLayout(common);polish_form(form);layout.addWidget(common);common.hide()
        common_toggle.toggled.connect(common.setVisible)
        common_toggle.toggled.connect(lambda on:common_toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if on else QtCore.Qt.ArrowType.RightArrow))
        self.common_conditions=common;self.common_conditions_toggle=common_toggle
        self.chemistry={}
        for key,zh,en,values in (('developer_type','显影液','Developer',('standard','fine_grain','compensating','high_acutance','high_contrast','push','exhausted','monobath','experimental_reversal')),
            ('fixer_type','定影液','Fixer',('standard','rapid','hardening','monobath','experimental_mask_bleach'))):
            combo=QtWidgets.QComboBox();combo.setEditable(True);combo.addItems(values);combo.setCurrentText(getattr(recipe,key));form.addRow(text(zh,en),combo);self.chemistry[key]=combo
        conditions=QtWidgets.QWidget();grid=QtWidgets.QGridLayout(conditions);form.addRow(conditions);self.controls={}
        for i,(key,zh,en) in enumerate(FIELDS):
            number=ParameterNumber();number.setRange(*CONDITION_RANGES[key]);number.setDecimals(2)
            value=getattr(recipe,key);number.setValue(recipe.time_min*60 if value is None and key=='agitation_time_s' else value)
            grid.addWidget(QtWidgets.QLabel(text(zh,en)),i,0);grid.addWidget(NumericRow(number),i,1);self.controls[key]=number
        grid.setHorizontalSpacing(18);grid.setVerticalSpacing(12);grid.setColumnStretch(1,1)
        self.original_conditions={key:getattr(recipe,key) for key in self.controls}
        self.display_conditions={key:control.value() for key,control in self.controls.items()}
        self.controls['agitation_time_s'].setMaximum(recipe.time_min*60)
        self.controls['agitation_time_s'].parentWidget().sync()
        self.controls['time_min'].valueChanged.connect(self._sync_automatic_agitation)
        conditions.setToolTip(text('真实工艺输入直接交给降阶反应算子，自定义时下表逐浴条件优先。逐浴模型按产物形成与保留计算灰雾和颗粒，旧版近似仍取首次显影环境。单个温度不代表冷热冲击。','Inputs feed reduced operators; custom rows take precedence. The bath model uses product formation and retention for fog/grain; legacy approximation uses the first developer. One temperature is not thermal shock.'))
        self.custom=QtWidgets.QCheckBox(text('自定义药浴顺序与逐浴条件','Custom bath sequence and per-bath conditions'));layout.addWidget(self.custom)
        self.table=QtWidgets.QTableWidget(0,len(FIELDS)+3);self.table.setHorizontalHeaderLabels([text('步骤','Step')]+[text(zh,en) for _,zh,en in FIELDS]+[text('显影液','Developer'),text('定影液','Fixer')]);layout.addWidget(self.table,1)
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        bar=QtWidgets.QHBoxLayout();layout.addLayout(bar);self.row_buttons=[]
        for zh,en,callback in (('添加药浴','Add bath',self.add_row),('移除','Remove',self.remove_row),('上移','Move up',lambda:self.move(-1)),('下移','Move down',lambda:self.move(1))):
            button=QtWidgets.QPushButton(text(zh,en));button.clicked.connect(callback);bar.addWidget(button);self.row_buttons.append(button)
        capability=QtWidgets.QPushButton(text('药液能力…','Solution capability…'));capability.clicked.connect(self.edit_solution);bar.addWidget(capability);self.row_buttons.append(capability)
        capability.setToolTip(text('编辑所选药浴的独立反应能力和硬化组分；未启用时沿用旧配方。','Edit the selected bath reaction and hardener capabilities; disabled baths retain legacy kinetics.'))
        self.native_button=QtWidgets.QPushButton(text('单浴条件…','Individual bath…'));self.native_button.clicked.connect(self.edit_native_bath);bar.addWidget(self.native_button)
        bar.addStretch();self.status=QtWidgets.QLabel();self.status.setWordWrap(True);outer.addWidget(self.status)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel);buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);outer.addWidget(buttons)
        from film_foundry.core.film_process.recipe import program_from_develop_recipe
        native=program_from_develop_recipe(recipe,mode=self.config.mode,material_process=self.config.film.medium_process)
        from film_foundry.core.process_operations import source_bath_rows
        rows=editable_bath_rows(self.config,native)
        for index,row in enumerate(rows,1):
            if row['action'] in self.actions:
                self.add_row(row);self.table.cellWidget(self.table.rowCount()-1,0).setProperty('source_bath',index)
                self.table.setVerticalHeaderItem(self.table.rowCount()-1,QtWidgets.QTableWidgetItem(str(index)))
        self.custom.setChecked(recipe.process_steps is not None);self.table.setEnabled(self.custom.isChecked());self.custom.toggled.connect(self.table.setEnabled)
        self.custom.toggled.connect(lambda enabled:self.program.setEnabled(not enabled));self.program.setEnabled(not self.custom.isChecked())
        for button in self.row_buttons:
            button.setEnabled(self.custom.isChecked());self.custom.toggled.connect(button.setEnabled)
        self.program.currentIndexChanged.connect(self.native_rows)
        self.native_button.setEnabled(not self.custom.isChecked());self.custom.toggled.connect(lambda enabled:self.native_button.setEnabled(not enabled))
        # Native rows remain read-only, but selecting a bath must be possible.
        self.table.setEnabled(True);self.custom.toggled.disconnect(self.table.setEnabled)
        self.custom.toggled.connect(self.set_row_editability);self.set_row_editability(self.custom.isChecked())
        from film_foundry.ui.bath_sequence_view import BathSequenceView
        self.sequence_view=BathSequenceView(self,layout,FIELDS)
        self.table.setCurrentCell(0,0)
        for control in self.controls.values():control.valueChanged.connect(self._shared_conditions_changed)
        for control in self.chemistry.values():control.currentTextChanged.connect(self._shared_conditions_changed)

    def _shared_conditions_changed(self,*_):
        if not self.custom.isChecked():self.native_rows()

    def _sync_automatic_agitation(self,minutes):
        control=self.controls['agitation_time_s']
        automatic=self.original_conditions['agitation_time_s'] is None and control.value()==self.display_conditions['agitation_time_s']
        with QtCore.QSignalBlocker(control):
            control.setMaximum(minutes*60)
            if automatic:
                control.setValue(minutes*60)
                self.display_conditions['agitation_time_s']=control.value()
        control.parentWidget().sync()

    def set_row_editability(self,enabled):
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.AllEditTriggers if enabled else QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for i in range(self.table.rowCount()):
            for j in (0,len(FIELDS)+1,len(FIELDS)+2):self.table.cellWidget(i,j).setEnabled(enabled)

    def condition_value(self,key):
        value=self.controls[key].value()
        return self.original_conditions[key] if value==self.display_conditions[key] else value

    def edit_native_bath(self):
        index=self.table.currentRow()
        if index<0:return
        from film_foundry.ui.bath_override_dialog import BathOverrideDialog
        from film_foundry.contracts.bath_solution import ACTIONS
        from film_foundry.core.process_operations import source_bath_rows
        from film_foundry.core.film_process.recipe import program_from_develop_recipe
        config=deepcopy(self.config);recipe=config.chemistry;recipe.process_steps=None;recipe.program_key=self.program.currentData()
        recipe.formation_history_model=self.formation_history.currentData()
        for key in self.controls:setattr(recipe,key,self.condition_value(key))
        for key,control in self.chemistry.items():setattr(recipe,key,control.currentText())
        bath=self.table.cellWidget(index,0).property('source_bath')
        row=source_bath_rows(config,program_from_develop_recipe(recipe,mode=config.mode,material_process=config.film.medium_process))[bath-1]
        if row['action'] not in ACTIONS:return
        dialog=BathOverrideDialog(bath,row,recipe,self)
        def apply():
            overrides={entry['bath']:entry for entry in recipe.bath_overrides or ()}
            if dialog.value is None:overrides.pop(bath,None)
            else:overrides[bath]=dialog.value
            self.config.chemistry.bath_overrides=tuple(overrides[key] for key in sorted(overrides)) or None
            self.native_rows();self.table.setCurrentCell(index,0)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self,dialog,apply)

    def native_rows(self):
        if self.custom.isChecked():return
        from film_foundry.core.film_process.recipe import program_from_develop_recipe
        previous_row=self.table.currentRow()
        recipe=deepcopy(self.config.chemistry);recipe.process_steps=None;recipe.program_key=self.program.currentData()
        for key in self.controls:setattr(recipe,key,self.condition_value(key))
        for key,control in self.chemistry.items():setattr(recipe,key,control.currentText())
        if recipe.program_key!=self.config.chemistry.program_key:
            recipe.bath_overrides=None;self.config.chemistry.bath_overrides=None;self.config.chemistry.program_key=recipe.program_key
        program=program_from_develop_recipe(recipe,mode=self.config.mode,material_process=self.config.film.medium_process)
        self.table.setRowCount(0)
        from film_foundry.core.process_operations import source_bath_rows
        config=deepcopy(self.config);config.chemistry=recipe
        for index,row in enumerate(editable_bath_rows(config,program),1):
            if row['action'] in self.actions and not (recipe.program_key=='color_negative_bleach_bypass' and not recipe.bath_overrides and row['action']=='bleach_silver'):
                self.add_row(row);self.table.cellWidget(self.table.rowCount()-1,0).setProperty('source_bath',index)
                self.table.setVerticalHeaderItem(self.table.rowCount()-1,QtWidgets.QTableWidgetItem(str(index)))
        self.set_row_editability(False)
        self.table.setCurrentCell(max(0,min(previous_row,self.table.rowCount()-1)),0)

    def add_row(self,row=None):
        row=row if isinstance(row,dict) else {'action':'develop_silver'}
        index=self.table.rowCount();self.table.insertRow(index);action=QtWidgets.QComboBox()
        for key in self.actions:action.addItem(text(*ACTIONS[key]),key)
        action.setCurrentIndex(action.findData(row['action']));self.table.setCellWidget(index,0,action)
        action.setProperty('solution',deepcopy(row.get('solution')))
        action.setProperty('original_row',deepcopy(row))
        def describe_action(*_):
            self.describe_solution(action)
        action.currentIndexChanged.connect(describe_action);describe_action()
        action.currentIndexChanged.connect(lambda *_:self.sequence_view.refresh() if hasattr(self,'sequence_view') else None)
        for column,(key,_,_) in enumerate(FIELDS,1):
            value=row.get(key,self.condition_value(key));self.table.setItem(index,column,QtWidgets.QTableWidgetItem(str(value if value is not None else row.get('time_min',self.condition_value('time_min'))*60)))
        for column,key in ((len(FIELDS)+1,'developer_type'),(len(FIELDS)+2,'fixer_type')):
            combo=QtWidgets.QComboBox();source=self.chemistry[key]
            combo.addItems([source.itemText(i) for i in range(source.count())]);combo.setEditable(True)
            combo.setCurrentText(row.get(key,source.currentText()));self.table.setCellWidget(index,column,combo)
            combo.currentTextChanged.connect(lambda *_:self.sequence_view.refresh() if hasattr(self,'sequence_view') else None)
        action.setProperty('automatic_agitation',row.get('agitation_time_s',self.condition_value('agitation_time_s')) is None)
        self.table.setCurrentCell(index,0)

    def describe_solution(self,action):
        value=action.property('solution')
        hint=text(*BATH_DOMAINS[action.currentData()][2])
        if value:
            hint+=text(' 已启用独立药液特性：完成度由反应速率、剩余活性和工艺条件决定。颗粒与灰雾可在药液特性页另设相对响应，按实际产物保留。',' Explicit solution properties: rate, remaining activity and bath conditions determine completion. Grain and fog factors can be set separately and follow retained products.')
            if value['action']!=action.currentData():hint+=text(' 步骤已改变，请重新配置药液能力。',' Action changed; reconfigure the solution capability.')
        action.setToolTip(hint)

    def rows(self):
        result=[]
        for i in range(self.table.rowCount()):
            action=self.table.cellWidget(i,0);row=deepcopy(action.property('original_row') or {})
            row['action']=action.currentData()
            if row['action']!='destroy_dye':row.pop('dye_destruction_basis',None)
            for j,(key,_,_) in enumerate(FIELDS):
                value=float(self.table.item(i,j+1).text())
                # Keep automatic agitation instead of turning an unchanged
                # display value into an explicit duration.
                if key=='agitation_time_s' and action.property('automatic_agitation'):value=None
                row[key]=value
            row.pop('solution',None)
            if action.property('solution'):row['solution']=deepcopy(action.property('solution'))
            row.update(developer_type=self.table.cellWidget(i,len(FIELDS)+1).currentText(),fixer_type=self.table.cellWidget(i,len(FIELDS)+2).currentText())
            result.append(row)
        return result

    def edit_solution(self):
        index=self.table.currentRow()
        if index<0:return
        action=self.table.cellWidget(index,0);key=action.currentData()
        from film_foundry.contracts.bath_solution import ACTIONS
        if key not in ACTIONS:
            self.status.setText(text('此步骤不是药液反应','This step is not a solution reaction'));return
        from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
        value=action.property('solution')
        if value and value['action']!=key:value=None
        dialog=BathSolutionDialog(key,value,self,formation_available=self.formation_history.currentData()!='legacy_global_v1')
        def apply():
            action.setProperty('solution',dialog.value);self.describe_solution(action)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self,dialog,apply)

    def remove_row(self):
        if self.table.currentRow()>=0:self.table.removeRow(self.table.currentRow())

    def move(self,delta):
        current=self.table.currentRow();target=current+delta
        if not 0<=target<self.table.rowCount():return
        try:rows=self.rows()
        except ValueError:self.status.setText(text('先修正无效数字','Correct invalid numbers first'));return
        rows[current],rows[target]=rows[target],rows[current];self.table.setRowCount(0)
        for row in rows:self.add_row(row)
        self.table.setCurrentCell(target,0)

    def apply(self):
        try:
            recipe=self.config.chemistry
            recipe.formation_history_model=self.formation_history.currentData()
            recipe.program_key=self.program.currentData()
            for key in self.controls:setattr(recipe,key,self.condition_value(key))
            for key,control in self.chemistry.items():setattr(recipe,key,control.currentText())
            recipe.process_steps=validate_process_steps(self.rows()) if self.custom.isChecked() else None
            if self.custom.isChecked() and self.custom_contains_native_damage:
                recipe.dye_damage_strength=0.  # The converted native stage now owns this request.
            if self.custom.isChecked():recipe.bath_overrides=None  # Resolved conditions are now owned by the custom rows.
            recipe.process_events=self.operations.read()
            recipe.latent_wait=self.latent_wait.read();recipe.post_process_light=self.post_light.read()
            from film_foundry.contracts.film_time import validate_config as validate_time_config
            validate_time_config(self.config)
            if self.principle_only:self.config=condition_driven_config(self.config)
            from film_foundry.core.film_process.recipe import program_from_develop_recipe
            program_from_develop_recipe(self.config.chemistry,mode=self.config.mode,material_process=self.config.film.medium_process)
            from film_foundry.core.process_operations import BathContactRate
            BathContactRate(self.config,(1,1))  # Validate bath targets without rendering.
            self.accept()
        except (ValueError,TypeError) as exc:self.status.setText(str(exc))
