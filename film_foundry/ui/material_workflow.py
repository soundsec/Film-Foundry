"""Presentation of the original three material families over existing controls.

No material defaults, recipes or stage authority live here. Operations remain
enabled by MaterialLabSession; moving a widget never changes its value.
"""
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text


FLOWS = {
    'peel': (('曝光', 'Exposure', ('expose',)),
             ('铺药与接触', 'Spread & contact', ('spread',)), ('推进时间', 'Advance time', ('advance',)),
             ('撕开成片', 'Separate', ('peel',))),
    'integral': (('曝光与出片', 'Expose & eject', ('expose', 'eject')),
                 ('铺药与接触', 'Spread & contact', ('spread',)),
                 ('显影与中和', 'Develop & neutralize', ('advance','stabilize'))),
    'plate': (('备版与抛光', 'Prepare & polish', ('prepare', 'polish', 'sensitize')),
              ('曝光', 'Exposure', ('expose',)), ('显影', 'Development', ('develop',)),
              ('定影', 'Fixing', ('fix',))),
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


def step_requirement(session,operation,flows):
    if operation in session.operations:
        return text('● 当前可执行。下划线标出正在查看的步骤。','● Available now. The underline marks the step being inspected.')
    if session.state is None and session.group is None:
        if session.image is None:return text('先载入图像或光学来源。','Load an image or optical source first.')
        index=next((i for i,(_,ops) in enumerate(flows) if operation in ops),None)
        if index:
            prior=' → '.join(f'{i+1} {label}' for i,(label,_) in enumerate(flows[:index]))
            return text('正在预设参数；执行前需先完成：','Preconfiguring; complete these steps first: ')+prior
    available=[name for name,ops in flows if any(op in session.operations for op in ops)]
    return (text('当前阶段不能执行此步。可先进行：','Unavailable at this stage. Available steps: ')+' / '.join(available)) if available else text(
        '此操作在当前阶段不可执行；请查看材料状态或可选干预。','This action is unavailable at the current stage. Review the material state or optional actions.')


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
        owner.material_sections = QtWidgets.QToolBox();material_layout.addWidget(owner.material_sections)
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
        self.action_scope = QtWidgets.QLabel();self.action_scope.setWordWrap(True)
        self.action_scope.setObjectName('sectionNote');process_layout.addWidget(self.action_scope)
        from film_foundry.ui.current_page_stack import CurrentPageStack
        self.pages = CurrentPageStack();process_layout.addWidget(self.pages)
        self.forms = {};self.indices = {};self.flow_buttons = []
        self.main_operations = tuple(op for _,_,ops in FLOWS[owner.kind] for op in ops)
        self.optional_operations = tuple(op for op in owner.operation_buttons if op not in self.main_operations)
        for operation in (*self.main_operations, *self.optional_operations):
            page, form = form_page();self.forms[operation] = form
            self.indices[operation] = self.pages.addWidget(page)
            if operation==self.optional_operations[0]:self.choice.insertSeparator(self.choice.count())
            prefix=text('可选 · ', 'Optional · ') if operation in self.optional_operations else ''
            self.choice.addItem(prefix+text(*labels[operation]), operation)
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
        self.flow = QtWidgets.QWidget();flow_layout=QtWidgets.QVBoxLayout(self.flow);flow_layout.setContentsMargins(0,0,0,0)
        start=QtWidgets.QHBoxLayout();flow_layout.addLayout(start)
        owner.standard_button=QtWidgets.QPushButton(text('按当前参数执行标准步骤','Run reference steps'))
        owner.standard_button.setObjectName('primaryAction');owner.standard_button.clicked.connect(owner.run_standard)
        owner.standard_button.setToolTip(text('仅用于尚未开始的片张。按主干顺序使用各步当前参数，不自动清理负片、镀金或施加事故；处理不足时保留真实阶段，可继续单步操作。',
            'For a fresh source. Uses current controls in the reference sequence, excluding negative clearing, gilding and accidents. Incomplete processing retains its actual stage for further steps.'))
        start.addWidget(owner.standard_button)
        self.progress_note=QtWidgets.QLabel();self.progress_note.setWordWrap(True);self.progress_note.setObjectName('sectionNote')
        start.addWidget(self.progress_note,1)
        bar=QtWidgets.QHBoxLayout();flow_layout.addLayout(bar)
        from film_foundry.ui.stage_button import StageButton,StageStrip
        self.stage_strip=StageStrip();bar.addWidget(self.stage_strip,1)
        for index, (zh, en, operations) in enumerate(FLOWS[owner.kind]):
            button = StageButton(index+1,compact=True);button.set_stage(text(zh,en),'')
            button.clicked.connect(lambda checked=False, ops=operations:self.select_flow(ops))
            self.stage_strip.add_stage(button);self.flow_buttons.append((button, operations))
        self.optional_button=QtWidgets.QToolButton();self.optional_button.setText(text('可选干预…','Optional actions…'))
        self.optional_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QtWidgets.QMenu(self.optional_button);self.optional_actions={}
        for operation in self.optional_operations:
            action=menu.addAction(text(*labels[operation]),lambda op=operation:self.select_flow((op,)))
            self.optional_actions[operation]=action
        self.optional_button.setMenu(menu);bar.addWidget(self.optional_button)
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
            sections=QtWidgets.QToolBox();self.forms['spread'].addRow(sections)
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
        if operation not in self.indices:return
        self.pages.setCurrentIndex(self.indices[operation])
        for button, operations in self.flow_buttons:button.setChecked(operation in operations)
        self.action_scope.setText(text('可选干预；只在材料允许的阶段执行。', 'Optional action; execute only when the material permits it.')
            if operation in self.optional_operations else step_requirement(self.owner.session,operation,[(text(zh,en),ops) for zh,en,ops in FLOWS[self.owner.kind]]))

    def select_flow(self, operations):
        available = self.owner.session.operations
        chosen = next((op for op in operations if op in available), operations[0])
        self.choice.setCurrentIndex(self.choice.findData(chosen))
        self.selected()
        if self.owner._workspace_layout == 'compact':self.owner.config_tabs.setCurrentWidget(self.owner.process_group)

    def sync(self):
        w = self.owner;session = w.session
        w.process_group.setEnabled(not w.busy);self.flow.setEnabled(not w.busy)
        fresh=session.state is None and session.group is None and session.image is not None
        w.standard_button.setEnabled(not w.busy and fresh)
        for operation,action in self.optional_actions.items():action.setEnabled(operation in session.operations and not w.busy)
        state=session.state
        if state is None:
            note=text('来源已就绪。可先设置各步参数，再执行标准步骤或逐步操作。','Source ready. Configure the steps, then run the reference sequence or work step by step.') if fresh else text(
                '先载入图像或光学来源，再执行标准步骤或逐步操作。','Load an image or optical source, then run the reference steps or work step by step.') if session.group is None else text(
                '已载入最终介质；观看只读。重新形成需另从原始来源开始。','Final medium loaded; viewing is read-only. Start from an original source to form another medium.')
        elif w.kind=='peel' and state.stage=='contact':
            note=text(f'已推进 {state.elapsed_seconds:g} s · 可继续推进或撕开；撕开后停止转移。',
                f'{state.elapsed_seconds:g} s elapsed · advance further or separate; separation stops transfer.')
        elif w.kind=='integral' and state.stage=='reacting':
            note=text(f'已推进 {state.elapsed_seconds:g} s · 尚未稳定。可继续推进 / 中和，或在可选干预中明确保留当前成片。',
                f'{state.elapsed_seconds:g} s elapsed · still reacting. Advance / neutralize, or explicitly keep the current print under optional actions.')
        elif w.kind=='plate' and state.stage in ('fixing','gilding'):
            note=text('药浴尚未完成，可继续本浴；延长时间不会补充已耗尽的药剂。','Bath incomplete; continue this bath. More time does not replenish exhausted reagents.')
        elif session.group is not None:
            note=text('已形成可观察介质；可选干预仍按材料阶段开放。观看与导出不推进反应。','A viewable medium is formed. Optional actions follow its stage; viewing and export never advance chemistry.')
        else:
            note=text('按编号查看主干工序；当前可执行的步骤已标出，材料开始后冻结。','Browse the numbered process. Available steps are marked; material freezes once started.')
        self.progress_note.setText(note)
        from film_foundry.ui.stage_button import visited_operations, operation_status
        visited=visited_operations(session)
        for index,((button,operations),(zh,en,_)) in enumerate(zip(self.flow_buttons,FLOWS[w.kind]),1):
            available=any(op in session.operations for op in operations)
            button.set_stage(text(zh,en), operation_status(visited,operations,available), available=available, visited=bool(visited and visited.intersection(operations)))
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
            w.workspace_splitter.setSizes([350,500,350])
        else:
            w.right_layout.removeWidget(w.process_group)
            if w.config_tabs.indexOf(w.process_group)<0:w.config_tabs.addTab(w.process_group,text('工艺','Process'))
            w.right_scroll.hide();w.workspace_splitter.setSizes([440,720,0])
        w.workspace_splitter.setStretchFactor(0,0);w.workspace_splitter.setStretchFactor(1,1);w.workspace_splitter.setStretchFactor(2,0)
