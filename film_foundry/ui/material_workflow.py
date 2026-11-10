"""Presentation of the original three material families over existing controls.

No material defaults, recipes or stage authority live here. Operations remain
enabled by MaterialLabSession; moving a widget never changes its value.
"""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


FLOWS = {
    'peel': (('曝光', 'Exposure', ('expose', 'light_leak')),
             ('铺药', 'Spreading', ('spread','contaminate','clean')), ('转移', 'Transfer', ('advance','environment')),
             ('分离与整理', 'Separate & finish', ('peel', 'clear_negative', 'clean', 'handling'))),
    'integral': (('曝光与出片', 'Expose & eject', ('expose', 'eject', 'light_leak')),
                 ('铺药与环境', 'Spread & environment', ('spread','contaminate','clean')),
                 ('显影与稳定', 'Develop & stabilize', ('advance','environment','stabilize','finish')),
                 ('表面整理', 'Finishing', ('handling',))),
    'plate': (('备版与抛光', 'Prepare & polish', ('prepare', 'polish', 'sensitize')),
              ('曝光', 'Exposure', ('expose', 'light_leak')), ('显影', 'Development', ('develop',)),
              ('定影与镀金', 'Fix & gild', ('fix', 'load_gilding', 'gild','rinse_gilding','finish')),
              ('表面质感', 'Surface', ('surface', 'weather'))),
}
MATERIAL_SECTIONS = {
    'peel': (('感光与转移', 'Exposure & transfer', ('transfer', 'donor_mode', 'half_exposure', 'response_contrast', 'reaction_seconds')),
             ('接收片', 'Receiver', ('maximum_density', 'paper_reflectance')),
             ('分离与层间强度','Separation & layer strength',('interlayer_adhesion','receiver_cohesion','donor_cohesion','separation_variation'))),
    'integral': (('感光与转移', 'Exposure & transfer', ('transfer', 'half_exposure', 'response_contrast', 'reaction_seconds')),
                 ('接收层与稳定', 'Receiver & stability', ('maximum_density', 'paper_reflectance', 'stabilization_seconds'))),
    'plate': (('银面与颗粒', 'Silver surface & particles', ('roughness', 'metal_reflectance', 'particle_reflectance')),
              ('感光与影调', 'Sensitivity & tone', ('half_exposure', 'reaction_seconds', 'maximum_density', 'response_contrast')),
              ('药浴与金层', 'Baths & gold layer', ('fixing_seconds','gilding_seconds','gold_capacity','gold_adhesion'))),
}
NEXT = {
    'peel': {None:'expose', 'exposed':'spread', 'contact':'advance', 'separated':'clear_negative', 'cleared':'handling'},
    'integral': {None:'expose', 'exposed':'eject', 'ejected':'spread', 'reacting':'advance', 'stable':'handling','finished':'handling'},
    'plate': {None:'prepare', 'blank':'polish', 'polished':'sensitize', 'sensitized':'expose',
              'exposed':'develop', 'developed':'fix', 'fixing':'fix', 'gilding':'gild', 'fixed':'gild', 'gilded':'weather','finished':'weather'},
}


def move_row(control, form):
    """Keep both the original label and field, including tooltips and signals."""
    previous = control.parentWidget().layout()
    if isinstance(previous, QtWidgets.QFormLayout):
        row = previous.takeRow(control)
        label = row.labelItem.widget() if row.labelItem else None
        if label is not None:
            form.addRow(label, control)
        else:
            form.addRow(control)
    else:
        previous.removeWidget(control)
        form.addRow(control)


def form_page():
    page = QtWidgets.QWidget()
    form = QtWidgets.QFormLayout(page)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapLongRows)
    return page, form


def section_box():
    from film_foundry.ui.theme import colors
    palette=colors();box=QtWidgets.QToolBox()
    box.setStyleSheet(f'QToolBox::tab {{background:{palette.surface};border:0;border-bottom:1px solid {palette.border};padding:0 8px;min-height:26px;text-align:left;}} QToolBox::tab:selected {{background:{palette.selected};color:{palette.text};font-weight:600;}}')
    return box


class MaterialWorkflow:
    def __init__(self, owner, root_layout, old_operations, labels):
        self.owner = owner
        self._identity = None
        old_material = owner.material_group
        old_process = owner.process_group
        while owner.config_tabs.count():owner.config_tabs.removeTab(0)
        owner.left.removeWidget(old_operations)
        old_operations.hide()
        # Material definitions have their own short, material-specific sections.
        owner.material_group = QtWidgets.QGroupBox(text('材料', 'Material'))
        material_layout = QtWidgets.QVBoxLayout(owner.material_group)
        owner.material_sections = section_box();material_layout.addWidget(owner.material_sections)
        used = set()
        sections=list(MATERIAL_SECTIONS[owner.kind])
        if owner.kind!='plate':sections.append(('药剂与中和层','Reagent & neutralization',
            ('developer_capacity','alkali_capacity','neutralizer_capacity','neutralization_delay_seconds','neutralization_seconds')))
        sections.append(('材料尺度','Material scale',('structure_model','material_long_edge_mm','texture_scale_mm','process_scale_mm')))
        if owner.kind!='plate':sections.append(('环境与污染响应','Environment & contamination',
            ('light_sensitivity','temperature_sensitivity','inhibitor_sensitivity','initial_contamination')))
        sections.append(('批次与身份','Batch & identity',('batch_variation','batch_seed')))
        for zh,en,keys in sections:
            page, form = form_page()
            for key in keys:
                if key in owner.material_controls:
                    move_row(owner.material_controls[key], form);used.add(key)
            if en == 'Batch & identity':move_row(owner.seed, form)
            owner.material_sections.addItem(page, text(zh, en))
        remaining = set(owner.material_controls) - used
        if remaining:
            page, form = form_page()
            for key in owner.material_controls:
                if key in remaining:move_row(owner.material_controls[key], form)
            owner.material_sections.addItem(page, text('其他材料参数', 'Other material properties'))
        owner.material_group.setToolTip(text('材料在开始形成后冻结；修改新材料须重新开始。', 'Material freezes when formation starts. Restart to define a new material.'))
        owner.config_tabs.addTab(owner.material_group, text('材料', 'Material'))

        owner.process_group = QtWidgets.QGroupBox(text('处理步骤', 'Processing'))
        process_layout = QtWidgets.QVBoxLayout(owner.process_group)
        self.choice = QtWidgets.QComboBox();process_layout.addWidget(self.choice)
        self.pages = QtWidgets.QStackedWidget();process_layout.addWidget(self.pages)
        self.forms = {};self.indices = {};self.flow_buttons = []
        for operation, button in owner.operation_buttons.items():
            page, form = form_page();self.forms[operation] = form
            self.indices[operation] = self.pages.addWidget(page)
            self.choice.addItem(text(*labels[operation]), operation)
        self._populate()
        for operation, button in owner.operation_buttons.items():
            button.setParent(self.pages.widget(self.indices[operation]))
            button.setObjectName('primaryAction')
            self.forms[operation].addRow(button)
        process_layout.addStretch()
        owner.config_tabs.addTab(owner.process_group, text('工艺', 'Process'))
        for old in (old_material, old_process, owner.defect_group, owner.finish_group):old.hide()
        # The forms keep the authoritative field widgets, so the old enable and
        # restoration paths remain valid, including schema-1 restrictions.
        if owner.kind != 'plate':owner.process_form = self.spread_form
        self.choice.currentIndexChanged.connect(self.selected)
        self.flow = QtWidgets.QWidget();bar = QtWidgets.QHBoxLayout(self.flow);bar.setContentsMargins(0,0,0,0)
        for index, (zh, en, operations) in enumerate(FLOWS[owner.kind]):
            button = QtWidgets.QPushButton(f'{index+1}  '+text(zh,en));button.setCheckable(True)
            button.setToolTip(text('查看本工序参数；执行按钮只在材料允许时启用。', 'View this stage. Execute only when the material permits it.'))
            button.clicked.connect(lambda checked=False, ops=operations:self.select_flow(ops))
            bar.addWidget(button);self.flow_buttons.append((button, operations))
        root_layout.insertWidget(root_layout.indexOf(owner.workspace_splitter), self.flow)
        owner.right_scroll = QtWidgets.QScrollArea();owner.right_scroll.setWidgetResizable(True)
        owner.right_scroll.setMinimumWidth(300)
        owner.right_controls = QtWidgets.QWidget();owner.right_layout = QtWidgets.QVBoxLayout(owner.right_controls)
        owner.right_scroll.setWidget(owner.right_controls);owner.workspace_splitter.addWidget(owner.right_scroll)
        owner.workspace_splitter.setChildrenCollapsible(False)
        self.selected()

    def _populate(self):
        w = self.owner
        def add(op, controls):
            for control in controls:move_row(control, self.forms[op])
        add('light_leak', (w.leak_strength, w.leak_edge))
        if w.kind == 'plate':
            add('polish', (w.process_controls['streaks'], w.process_controls['polish_angle']))
            add('sensitize', (w.process_controls['strength'],))
            add('develop', (w.seconds,))
            for operation,controls in w.operation_controls.items():
                add(operation,controls.values())
                self.forms[operation].parentWidget().setToolTip(text('按本次时长、温度、活性及有效接触推进；未完成可继续，预览与保存中间态不替代处理。旧材料保留原规则。',
                    'Advance by this duration, temperature, activity and contact. Resume incomplete baths; preview/save never completes processing. Legacy materials retain their rules.'))
            add('surface', [w.process_controls[key] for key in ('damage', 'tarnish')])
            add('weather', w.defect_controls.values())
            add('load_gilding',w.gold_intent.values())
            self.forms['finish'].parentWidget().setToolTip(text('保留当前银版，明确记录欠定影或欠镀金；不自动补完处理。观察不推进反应，继续化学处理须另存结束前的中间态。',
                'Keep this plate, including incomplete fixing or gilding. Never completes chemistry automatically. Viewing is read-only; save an intermediate state before finishing to resume chemistry.'))
            self.forms['gild'].parentWidget().setToolTip(text('新材料先载入金浴；此步骤只消耗已加入的金液。延长时间不补金，降低目标不移除已沉积金。',
                'New plates require a loaded gold bath. This step only consumes supplied gold. Time adds no gold; lowering the target does not remove deposits.'))
        else:
            for operation,controls in w.operation_controls.items():add(operation,controls.values())
            add('environment',w.intent_controls.values())
            self.forms['environment'].parentWidget().setToolTip(text('应用条件后，再推进时间才发生后续反应；不会重算前段。旧v1/v2材料不开放此操作。',
                'Apply conditions, then advance time. Earlier formation is retained. Unavailable on v1/v2 materials.'))
            self.forms['contaminate'].parentWidget().setToolTip(text('铺药封合前的局部污染：既遮挡后续见光，也抑制局部转移。密封即显片不允许中途注入污染。',
                'Contamination before spreading both filters later light and inhibits local transfer. Sealed instant prints cannot receive internal contamination.'))
            self.forms['clean'].parentWidget().setToolTip(text('清除可达表面的污染，不恢复已经曝光、转移或损失的成像物。',
                'Remove accessible contamination without restoring exposed, transferred or lost image material.'))
            if w.kind=='peel':
                self.forms['peel'].parentWidget().setToolTip(text('v4按速度、角度、片温和冻结的层间强度分配两片的成像层。撕开后停止转移，缺失层可能留在另一片；旧材料不执行机械分离。',
                    'V4 partitions image layers using speed, angle, temperature and frozen layer strengths. Transfer stops; lifted material can remain on the other sheet. Legacy materials retain simple separation.'))
                self.forms['clean'].parentWidget().setToolTip(text('v4撕开后按片体清残留；机械磨损会移除该片现有成像层，不会影响另一片。移除不可用再次清洗恢复。曝光前仅可清污染。',
                    'After v4 separation, clean the selected sheet. Abrasion removes its present image layers without changing the other sheet; removed material cannot be restored. Before contact, only residue cleaning is available.'))
                self.forms['clear_negative'].parentWidget().setToolTip(text('v4负片清理可以累积；刷痕同时使清理不匀并磨掉当地成像层，接收片保持原状。残片能否作负片仍取决于材料声明。',
                    'V4 negative clearing accumulates. Brush marks make removal uneven and abrade local image layers, leaving the receiver unchanged. A usable negative still requires a declared material.'))
            sections=section_box();self.forms['spread'].addRow(sections)
            for zh,en,controls in (
                ('铺展与接触','Spread & contact',[w.direction,*[w.process_controls[key] for key in ('coverage','contact','spread_seconds','roller_streaks','edge_loss')]]),
                ('药剂与瑕疵','Reagent & defects',[w.process_controls['reagent_load'],w.shortage_enabled,w.shortage_strength,*[w.defect_controls[key] for key in ('reagent_decay','contact_defects','reagent_leak','spread_unevenness')]]),
                ('显影环境','Development environment',[w.process_controls['temperature_c'],*[w.defect_controls[key] for key in ('light_protection','ambient_light','solar_warming') if key in w.defect_controls]])):
                page,form=form_page();sections.addItem(page,text(zh,en))
                for control in controls:move_row(control,form)
                if en=='Spread & contact':self.spread_form=form
            self.spread_sections=sections
            add('advance', (w.seconds,))
            add('handling', w.finish_controls.values())
            if w.kind == 'peel':add('clear_negative', w.clearing_controls.values())
            self.forms['spread'].parentWidget().setToolTip(w.defect_group.toolTip())
            if w.kind=='integral':
                self.forms['stabilize'].parentWidget().setToolTip(text('推进剩余中和层释放时间；中和容量不足或没有接触时仍保持反应中。旧材料沿用固定稳定时间。',
                    'Advance the remaining acid release window. Insufficient capacity or contact leaves the sheet reacting. Legacy sheets retain a fixed cutoff.'))
                self.forms['finish'].parentWidget().setToolTip(text('明确保留当前材料状态；可能仍有活性碱和遮光层。观察与导出不继续反应，可另存中间态后再决定。',
                    'Freeze the current print, which may retain active alkali and opacity. Viewing and export do not advance chemistry. Save an intermediate state to resume separately.'))

    def selected(self):
        operation = self.choice.currentData()
        self.pages.setCurrentIndex(self.indices[operation])
        for button, operations in self.flow_buttons:button.setChecked(operation in operations)

    def select_flow(self, operations):
        available = self.owner.session.operations
        chosen = next((op for op in operations if op in available), operations[0])
        self.choice.setCurrentIndex(self.choice.findData(chosen))
        self.selected()
        if self.owner._workspace_layout == 'compact':self.owner.config_tabs.setCurrentWidget(self.owner.process_group)

    def sync(self):
        w = self.owner;session = w.session
        w.process_group.setEnabled(not w.busy);self.flow.setEnabled(not w.busy)
        identity = session.state if session.state is not None else session.group if session.group is not None else session.image
        if identity is not self._identity:
            self._identity = identity
            stage = session.state.stage if session.state else None
            available = session.operations
            chosen = NEXT[w.kind].get(stage)
            if w.kind=='plate' and stage=='fixed' and session.state.schema_version>=4:chosen='load_gilding'
            if chosen not in available:chosen = next(iter(available), None)
            if chosen:self.choice.setCurrentIndex(self.choice.findData(chosen))
        self.selected()

    def set_layout(self, mode):
        w = self.owner
        # Compact: material/process tabs on one side. Studio: material, image,
        # process from left to right. No copies or alternate parameter models.
        if mode == 'studio':
            index = w.config_tabs.indexOf(w.process_group)
            if index >= 0:w.config_tabs.removeTab(index)
            w.right_layout.addWidget(w.process_group);w.process_group.show();w.right_scroll.show()
            w.workspace_splitter.setSizes([310,700,320])
        else:
            w.right_layout.removeWidget(w.process_group)
            if w.config_tabs.indexOf(w.process_group)<0:w.config_tabs.addTab(w.process_group,text('工艺','Process'))
            w.right_scroll.hide();w.workspace_splitter.setSizes([340,850,0])
        w.workspace_splitter.setStretchFactor(0,0);w.workspace_splitter.setStretchFactor(1,1);w.workspace_splitter.setStretchFactor(2,0)
