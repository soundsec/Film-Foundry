"""Compact bath sequence and selected-step inspector over the existing draft."""
from copy import deepcopy
from film_foundry.ui.qt import QtCore, QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.numeric_row import NumericRow, ParameterNumber, polish_form
from film_foundry.contracts.process_steps import CONDITION_RANGES


class BathSequenceView(QtCore.QObject):
    def __init__(self, dialog, layout, fields):
        super().__init__(dialog)
        self.dialog, self.fields = dialog, fields
        self.loading = False
        self.row = -1
        self.custom_draft = None
        self.custom_row = 0
        table = dialog.table
        table.setObjectName('bathSequence')
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        table.setShowGrid(False)
        table.verticalHeader().setDefaultSectionSize(40)
        for column in range(3, table.columnCount()):
            table.hideColumn(column)
        table.setHorizontalHeaderLabels([text('步骤', 'Step'), text('分钟', 'Minutes'), '°C'] +
                                        [text(zh,en) for _,zh,en in fields[2:]] +
                                        [text('显影液','Developer'),text('定影液','Fixer')])
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            table.horizontalHeader().setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(col, 76)
        index = layout.indexOf(table)
        layout.removeWidget(table)
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(table)
        table.setMinimumWidth(330)
        inspector = QtWidgets.QScrollArea()
        inspector.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(content)
        polish_form(form)
        self.title = QtWidgets.QLabel()
        self.title.setObjectName('editorHeading')
        font = self.title.font(); font.setBold(True); self.title.setFont(font)
        form.addRow(self.title)
        actions=QtWidgets.QWidget(); action_layout=QtWidgets.QHBoxLayout(actions)
        action_layout.setContentsMargins(0,0,0,0)
        for button in (dialog.native_button, dialog.row_buttons[-1]):
            button.parentWidget().layout().removeWidget(button)
            action_layout.addWidget(button)
        action_layout.addStretch()
        form.addRow(actions)
        self.action = QtWidgets.QComboBox()
        from film_foundry.ui.roll_process_dialog import ACTIONS
        for key in dialog.actions:
            self.action.addItem(text(*ACTIONS[key]), key)
        form.addRow(text('处理类型','Process type'), self.action)
        self.action.currentIndexChanged.connect(self.action_changed)
        self.numbers = {}
        advanced=QtWidgets.QWidget();advanced_form=QtWidgets.QFormLayout(advanced);polish_form(advanced_form)
        self.advanced_toggle=QtWidgets.QToolButton();self.advanced_toggle.setText(text('搅拌时长、疲劳与入浴条件','Agitation duration, exhaustion & transfer'))
        self.advanced_toggle.setCheckable(True);self.advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.toggled.connect(advanced.setVisible)
        self.advanced_toggle.toggled.connect(lambda checked:self.advanced_toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if checked else QtCore.Qt.ArrowType.RightArrow))
        for column, (key, zh, en) in enumerate(fields, 1):
            number = ParameterNumber()
            number.setRange(*CONDITION_RANGES[key]); number.setDecimals(6)
            number.setSingleStep(.1 if key in ('time_min','temperature_c') else .01)
            self.numbers[key] = number
            target=form if key in ('time_min','temperature_c','concentration','agitation') else advanced_form
            target.addRow(text(zh,en), NumericRow(number))
            number.valueChanged.connect(lambda value, col=column: self.number_changed(col,value))
        form.addRow(self.advanced_toggle);form.addRow(advanced);advanced.hide()
        self.chemistry = {}
        for key, col, zh, en in (('developer_type',len(fields)+1,'显影液','Developer'),
                                 ('fixer_type',len(fields)+2,'定影液','Fixer')):
            combo = QtWidgets.QComboBox(); combo.setEditable(True)
            source = dialog.chemistry[key]
            combo.addItems([source.itemText(i) for i in range(source.count())])
            advanced_form.addRow(text(zh,en), combo); self.chemistry[key] = combo
            combo.currentTextChanged.connect(lambda value,col=col:self.chemistry_changed(col,value))
        self.read_only = QtWidgets.QLabel(text('标准流程','Standard process'))
        self.read_only.setToolTip(text('保留标准顺序；通过“单浴条件”覆盖所选浴，或切换“自定义步骤”自由编排。',
            'Keep the standard order. Use Individual bath to override conditions, or Custom steps to edit the sequence.'))
        form.addRow(self.read_only)
        inspector.setWidget(content)
        panels=QtWidgets.QStackedWidget();panels.setMinimumWidth(420);panels.addWidget(inspector)
        common=dialog.common_conditions
        layout.removeWidget(common)
        dialog.common_conditions_toggle.toggled.disconnect(common.setVisible)
        heading=QtWidgets.QLabel(text('共用药液与条件','Shared chemistry and conditions'));heading.setObjectName('editorHeading')
        common.layout().insertRow(0,heading)
        common_scroll=QtWidgets.QScrollArea();common_scroll.setWidgetResizable(True);common_scroll.setWidget(common);common.show()
        panels.addWidget(common_scroll)
        dialog.common_conditions_toggle.toggled.connect(lambda enabled:panels.setCurrentIndex(int(enabled)))
        split.addWidget(panels);split.setSizes([360,560]);split.setStretchFactor(1,1)
        layout.insertWidget(index, split, 1)
        self.splitter = split
        table.currentCellChanged.connect(self.refresh)
        table.cellChanged.connect(self.cell_changed)
        dialog.custom.toggled.connect(self.mode_changed)
        self.mode_buttons = []
        bar = QtWidgets.QWidget(); buttons = QtWidgets.QHBoxLayout(bar)
        buttons.setContentsMargins(0,0,0,0); buttons.setSpacing(6)
        group = QtWidgets.QButtonGroup(bar); group.setExclusive(True)
        for custom, zh, en in ((False,'标准流程','Standard process'),(True,'自定义步骤','Custom steps')):
            button = QtWidgets.QToolButton(); button.setText(text(zh,en))
            button.setObjectName('editorTab'); button.setCheckable(True)
            button.setToolTip(text('标准流程作为起点；自定义步骤按逐浴条件重新计算，可增删和排序。原生程序的整体完成度等设置不保证逐项等价。',
                'Start from a standard sequence; custom steps recalculate per-bath conditions and allow reordering. Native aggregate completion controls are not guaranteed to map one-to-one.'))
            button.setChecked(dialog.custom.isChecked()==custom)
            button.clicked.connect(lambda checked,custom=custom:dialog.custom.setChecked(custom))
            group.addButton(button); buttons.addWidget(button); self.mode_buttons.append(button)
        buttons.addStretch()
        index = layout.indexOf(dialog.custom); dialog.custom.hide()
        layout.insertWidget(index,bar)
        self.refresh()

    def refresh(self, *_):
        if self.loading:return
        self.loading=True
        try:
            dialog=self.dialog; table=dialog.table
            if self.row!=table.currentRow():dialog.common_conditions_toggle.setChecked(False)
            self.row=table.currentRow()
            valid=self.row>=0 and table.cellWidget(self.row,0) is not None
            if valid:
                action=table.cellWidget(self.row,0)
                self.title.setText(f'{self.row+1:02d} · {action.currentText()}')
                self.action.setCurrentIndex(self.action.findData(action.currentData()))
                self.action.setToolTip(action.toolTip())
                for column,(key,_,_) in enumerate(self.fields,1):
                    item=table.item(self.row,column)
                    if item is not None:
                        try:self.numbers[key].setValue(float(item.text()))
                        except ValueError:pass
                for key,col in (('developer_type',len(self.fields)+1),('fixer_type',len(self.fields)+2)):
                    source=table.cellWidget(self.row,col)
                    if source is not None:self.chemistry[key].setCurrentText(source.currentText())
            else:self.title.setText(text('选择一个步骤','Select a step'))
            editable=valid and dialog.custom.isChecked()
            for widget in (self.action,*self.numbers.values(),*self.chemistry.values()):widget.setEnabled(editable)
            self.read_only.hide()
            self.title.setToolTip(self.read_only.toolTip() if not editable else self.action.toolTip())
            for i,button in enumerate(self.mode_buttons if hasattr(self,'mode_buttons') else ()):
                button.setChecked(dialog.custom.isChecked()==bool(i))
        finally:self.loading=False

    def number_changed(self,column,value):
        if self.loading or self.row<0:return
        self.dialog.table.item(self.row,column).setText(str(value))

    def action_changed(self):
        if self.loading or self.row<0:return
        action=self.dialog.table.cellWidget(self.row,0)
        action.setCurrentIndex(action.findData(self.action.currentData()));self.refresh()

    def chemistry_changed(self,column,value):
        if not self.loading and self.row>=0:self.dialog.table.cellWidget(self.row,column).setCurrentText(value)

    def cell_changed(self,row,column):
        table=self.dialog.table
        action=table.cellWidget(row,0)
        agitation_column=next(i for i,(key,_,_) in enumerate(self.fields,1) if key=='agitation_time_s')
        if action is not None and column==1 and action.property('automatic_agitation'):
            item=table.item(row,agitation_column)
            if item is not None:
                try:seconds=float(table.item(row,1).text())*60
                except ValueError:pass
                else:
                    with QtCore.QSignalBlocker(table):item.setText(str(seconds))
        elif action is not None and column==agitation_column:
            action.setProperty('automatic_agitation',False)
        if row==self.row:self.refresh()

    def mode_changed(self,custom):
        if self.loading:return
        try:
            if not custom:
                self.custom_draft=deepcopy(self.dialog.rows())
                self.custom_row=self.dialog.table.currentRow()
                self.dialog.native_rows()
            elif self.custom_draft is not None:
                self.dialog.table.setRowCount(0)
                for row in self.custom_draft:self.dialog.add_row(row)
                self.dialog.set_row_editability(True)
                self.dialog.table.setCurrentCell(self.custom_row,0)
            else:
                self.dialog.custom_contains_native_damage=any(
                    (self.dialog.table.cellWidget(i,0).property('original_row') or {}).get('label')=='explicit_process_dye_damage'
                    for i in range(self.dialog.table.rowCount()))
            self.refresh()
        except (ValueError,TypeError) as exc:
            self.dialog.status.setText(str(exc))
            with QtCore.QSignalBlocker(self.dialog.custom):self.dialog.custom.setChecked(True)
            self.dialog.program.setEnabled(False)
            self.dialog.native_button.setEnabled(False)
            for button in self.dialog.row_buttons:button.setEnabled(True)
            self.dialog.set_row_editability(True);self.refresh()
