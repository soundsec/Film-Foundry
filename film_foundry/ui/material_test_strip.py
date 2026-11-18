"""Material trials read the same controls as the normal operation buttons."""
from copy import deepcopy
from dataclasses import asdict
import numpy as np
from film_foundry.ui.qt import QtCore,QtWidgets
from film_foundry.ui.curve_editor import text
from film_foundry.ui.material_workspace_state import controls,capture as capture_draft
from film_foundry.app.material_history import capture,changed
from film_foundry.app.film_test_strip import StripAxis
from film_foundry.app.material_test_strip import MaterialStripSession
from film_foundry.app.material_reference import reference_operations


def material_trial_config(w, fresh, operation):
    material={k:c.currentData() if isinstance(c,QtWidgets.QComboBox) else c.value() for k,c in w.material_controls.items()}
    if fresh:
        steps=reference_operations(w.kind,w.session.module,w.session.material_type(**material))
        # Exactly the same overrides as each window's standard button.
        operations=({op:w._values(w.operation_controls.get(op,{})) for op,_ in steps} if hasattr(w,'_values') else
            {op:w._operation_parameters(op) for op,_ in steps})
    else:operations={operation:w._operation_parameters(operation)} if operation in w.session.operations else {}
    return dict(material=material,operations=operations,seed=int(w.seed.value()),view=asdict(w.conditions()),
        scan=deepcopy(w.session.scan_adjustments),body=max(0,w.body_choice.currentIndex()),operation=operation if not fresh else None)


class MaterialTrials:
    def __init__(self,owner):
        self.owner=owner;self.snapshot=capture(owner.session);self.draft=capture_draft(owner)
        self.fresh=owner.session.state is None and owner.session.group is None
        self.operation=(owner.workflow.choice if hasattr(owner,'workflow') else owner.operation_choice).currentData()
        self.targets=controls(owner);self.axes=[]
        baseline=self.read()
        for name,control in self.targets.items():
            numeric=getattr(control,'spin',control)
            if name in ('seed','work_size') or not isinstance(numeric,(QtWidgets.QSpinBox,QtWidgets.QDoubleSpinBox)) or not control.isEnabled():continue
            if not self.fresh and name.startswith('material_controls.'):continue
            view=name in ('view_ev','view_angle','light_angle','background')
            if not self.fresh and not view and self.operation not in owner.session.operations:continue
            value=control.value();probe=min(numeric.maximum(),value+max(numeric.singleStep(),.01))
            if probe==value:probe=max(numeric.minimum(),value-max(numeric.singleStep(),.01))
            with QtCore.QSignalBlocker(control):
                try:control.setValue(probe);different=self.read()!=baseline
                finally:control.setValue(value)
            if different:self.axes.append(StripAxis((name,), 'scan' if view else 'develop',value,isinstance(control,QtWidgets.QSpinBox)))
        self.note=text('材料试条：只在副本上计算，采用只更新设置，不推进当前材料。新材料缩图执行标准步骤；已有材料保持形成尺寸，只缩小显示。',
            'Material trials run on a copy. Adoption changes settings only. Fresh sources use smaller reference processing; existing material keeps its formation size and only the display is resized.')

    def read(self):
        return material_trial_config(self.owner,self.fresh,self.operation)

    def label(self,axis):
        control=self.targets[axis.path[0]];parent=control.parentWidget();label=None
        if parent and isinstance(parent.layout(),QtWidgets.QFormLayout):label=parent.layout().labelForField(control)
        name=label.text() if isinstance(label,QtWidgets.QLabel) else getattr(control,'spin',control).accessibleName() or axis.path[0].split('.')[-1].replace('_',' ')
        category=text('仅观看','View only') if axis.stage=='scan' else text('材料 / 工艺','Material / process')
        if axis.path[0].startswith('operation.'):
            from film_foundry.ui.sheet_lab_window import OPS
            from film_foundry.ui.material_lab_window import OPERATIONS
            operation=axis.path[0].split('.')[1];category=text(*(OPS|OPERATIONS).get(operation,(operation,operation)))
        return category+' · '+name

    def bounds(self,axis):
        c=self.targets[axis.path[0]];c=getattr(c,'spin',c);return c.minimum(),c.maximum()

    def scope(self,axis):
        if self.fresh:return text('按当前标准步骤试算，采用后仍需执行。','Trials follow the reference steps; adoption does not execute them.')
        if axis.stage=='scan':return text('只观察当前材料，不推进工序。','Observes current material without processing.')
        return text('只试当前选中工序，不自动完成后续步骤。','Trials execute only the selected operation, not later steps.')

    def check(self):
        if self.owner.busy or changed(self.snapshot,capture(self.owner.session)) or self.draft['values']!=capture_draft(self.owner)['values']:
            raise ValueError(text('材料或设置已改变，请重新打开试条。','Material or settings changed; reopen the trials.'))

    def prepare(self,axis,start,stop,count,edge,*,values=None):
        self.check()
        if axis not in self.axes or (values is None and start==stop):raise ValueError(text('请选择有效参数和不同的起止值。','Choose a valid axis and distinct endpoints.'))
        target=self.targets[axis.path[0]];old=target.value();variants=[]
        def read(value):
            low,high=self.bounds(axis)
            if not low<=value<=high:raise ValueError('Value outside control range')
            target.setValue(value);config=self.read()
            if not self.fresh and axis.stage=='scan':config['operation']=None
            config['adopt_value']=target.value();return config
        with QtCore.QSignalBlocker(target):
            try:
                reference=read(old)
                for value in (np.linspace(start,stop,count) if values is None else values):
                    value=int(round(value)) if axis.integer else float(value)
                    config=read(value);variants.append((config['adopt_value'],config))
            finally:target.setValue(old)
        if len({v for v,_ in variants})!=len(variants):raise ValueError(text('范围过小，数值精度下试条重复。','Range is too small; trial values repeat at this precision.'))
        return variants,reference,MaterialStripSession(self.owner.session,long_edge=edge)

    def adopt(self,config,axis):
        self.check();self.owner.edit_tools.record_draft()
        self.targets[axis.path[0]].setValue(config['adopt_value'])
        self.owner.edit_tools.record_draft()


def open_material_strip(owner):
    if owner.busy:return
    existing=getattr(owner,'_test_strip_dialog',None)
    if existing:existing.raise_();return existing
    try:
        if not owner.processing_bar.source_ready():return
        backend=MaterialTrials(owner)
        if not backend.axes:raise ValueError(text('当前无可试算数值；先载入来源或选择可执行工序。','Load a source or select an available operation with numeric settings.'))
        from film_foundry.ui.film_test_strip import FilmTestStripDialog
        dialog=FilmTestStripDialog(owner.session.source_name,'material',{}, {},backend.adopt,owner,backend=backend)
    except Exception as exc:owner.status.setText(str(exc));return
    owner.preview_timer.stop();owner.processing_bar.reload_timer.stop();owner._test_strip_dialog=dialog
    if getattr(owner,'_planning_batch',False):
        dialog.scope_note.setText(text('批量工作只比较当前来源；采用更新整批共用设置。','Trials use the current source; adoption changes shared batch settings.'));dialog.scope_note.show()
    def finished(*_):
        owner._test_strip_dialog=None
        if owner._closing:owner.close()
        else:owner.request_preview()
    dialog.finished.connect(finished)
    from film_foundry.ui.inline_editor import present_inline_editor
    present_inline_editor(owner,dialog)
    return dialog
