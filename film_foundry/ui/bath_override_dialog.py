"""Edit one native bath without converting its development program."""
from copy import deepcopy
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.numeric_row import NumericRow, ParameterNumber, polish_form
from film_foundry.ui.curve_editor import text
from film_foundry.contracts.bath_overrides import CONDITIONS,validate_bath_overrides
from film_foundry.contracts.bath_solution import MODEL,validate_solution
from film_foundry.contracts.process_steps import CONDITION_RANGES


class BathOverrideDialog(QtWidgets.QDialog):
    def __init__(self,bath,row,recipe,parent=None):
        super().__init__(parent);self.setWindowTitle(text('单浴条件','Individual bath conditions'));self.resize(680,540)
        from film_foundry.ui.roll_process_dialog import FIELDS
        labels={key:(zh,en) for key,zh,en in FIELDS}
        self.bath=bath;self.row=deepcopy(row);self.value=None
        from film_foundry.contracts.formation_history import enabled
        self.formation_available=enabled(recipe)
        self.solution=deepcopy(row.get('solution')) or validate_solution({'model':MODEL,'action':row['action']},row['action'])
        layout=QtWidgets.QVBoxLayout(self);form=QtWidgets.QFormLayout();layout.addLayout(form)
        self.enabled=QtWidgets.QCheckBox(text('使用独立药液条件','Use independent solution conditions'));self.enabled.setChecked(True);form.addRow(self.enabled)
        self.controls={};self.original={};self.display={}
        for key in CONDITIONS:
            value=row.get(key,getattr(recipe,key))
            self.original[key]=value
            if value is None:value=row.get('time_min',recipe.time_min)*60
            control=ParameterNumber();control.setDecimals(4);control.setRange(*CONDITION_RANGES[key]);control.setValue(value)
            self.controls[key]=control;self.display[key]=control.value();form.addRow(text(*labels[key]),NumericRow(control))
        polish_form(form)
        self.controls['time_min'].valueChanged.connect(self._sync_automatic_agitation)
        if self.original['agitation_time_s'] is None:
            self.controls['agitation_time_s'].setToolTip(text('默认随药浴时长计算；手动修改后使用指定的摇晃时长。',
                'Follows bath duration by default; editing this value sets an explicit agitation duration.'))
        button=QtWidgets.QPushButton(text('药液反应能力…','Solution capability…'));button.clicked.connect(self.edit_solution);layout.addWidget(button)
        self.setToolTip(text('保留原生显影阶段与顺序，仅此浴使用独立反应能力。关闭后恢复此浴原始条件。','Keeps native development stages and order; only this bath uses explicit kinetics. Disabling restores its original conditions.'))
        self.status=QtWidgets.QLabel();layout.addWidget(self.status)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def _sync_automatic_agitation(self,minutes):
        control=self.controls['agitation_time_s']
        if self.original['agitation_time_s'] is None and control.value()==self.display['agitation_time_s']:
            control.setValue(minutes*60)
            self.display['agitation_time_s']=control.value()

    def edit_solution(self):
        from film_foundry.ui.bath_solution_dialog import BathSolutionDialog
        dialog=BathSolutionDialog(self.row['action'],self.solution,self,formation_available=self.formation_available)
        def apply():
            self.solution=dialog.value
            if self.solution is None:self.enabled.setChecked(False)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self,dialog,apply)

    def apply(self):
        try:
            if self.enabled.isChecked():
                value={'bath':self.bath,'action':self.row['action'],'solution':self.solution}
                value.update({key:self.original[key] if control.value()==self.display[key] else control.value() for key,control in self.controls.items()})
                self.value=validate_bath_overrides((value,))[0]
            else:self.value=None
            self.accept()
        except (TypeError,ValueError) as exc:self.status.setText(str(exc))
