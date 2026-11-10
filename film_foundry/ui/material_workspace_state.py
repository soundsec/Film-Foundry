"""Explicit material-page drafts, separate from numeric material archives."""
import math
import json
from dataclasses import asdict
from film_foundry.ui.qt import QtWidgets
from film_foundry.ui.curve_editor import text

GROUPS=('material_controls','process_controls','defect_controls','finish_controls','clearing_controls','intent_controls','enhancement_intent','gold_intent')
SCALARS=('seed','work_size','seconds','direction','leak_strength','leak_edge','view_ev',
         'view_angle','light_angle','background','border_choice','inspection','shortage_enabled','shortage_strength')


def controls(window):
    result={}
    for group in GROUPS:
        result.update((group+'.'+key,control) for key,control in getattr(window,group,{}).items())
    for operation,fields in getattr(window,'operation_controls',{}).items():
        result.update(('operation.'+operation+'.'+key,control) for key,control in fields.items())
    for name in SCALARS:
        control=getattr(window,name,None)
        if control is not None:result[name]=control
    return result


def capture(window):
    choice=window.workflow.choice if hasattr(window,'workflow') else window.operation_choice
    panel=getattr(window,'optical_panel',None)
    pending=None
    if panel is not None and not panel.isHidden():
        body,relay=panel.selection()
        pending={'path':panel.source_path,'choices':panel.choices,'body':body,'relay':relay.as_dict()}
    return {'model':'material_page_draft_v1','pending_optical':pending,'view':json.loads(json.dumps(asdict(window.conditions()))),
        'values':{name:control.currentData() if isinstance(control,QtWidgets.QComboBox) else control.isChecked() if isinstance(control,QtWidgets.QCheckBox) else control.value() for name,control in controls(window).items()},
        'layout':window._workspace_layout,'operation':choice.currentData(),
        'body_index':max(0,window.body_choice.currentIndex()),
        'material_section':window.material_sections.currentIndex(),
        'process_tab':window.config_tabs.currentWidget() is window.process_group,
        'spread_section':window.workflow.spread_sections.currentIndex() if hasattr(window,'workflow') and hasattr(window.workflow,'spread_sections') else 0,
        'histogram':window.inspector.histogram_button.isChecked(),'history':window.inspector.history_button.isChecked(),
        'brightness':window.inspector.histogram.brightness,'logarithmic':window.inspector.histogram.logarithmic}


def restore(window,draft):
    if not isinstance(draft,dict) or draft.get('model')!='material_page_draft_v1' or not isinstance(draft.get('values'),dict):
        raise ValueError('Unsupported material-page draft')
    targets=controls(window)
    values=dict(draft['values'])
    # Drafts also own unformed/batch material settings. Keep their historical
    # generator instead of silently selecting the new material default.
    values.setdefault('material_controls.structure_model','relative_v1')
    for name,value in values.items():
        if name not in targets:raise ValueError('Unknown material draft control: '+name)
        control=targets[name]
        if isinstance(control,QtWidgets.QComboBox):
            if control.findData(value)<0:raise ValueError('Unsupported material draft option: '+name)
        elif isinstance(control,QtWidgets.QCheckBox):
            if type(value) is not bool:raise ValueError('Invalid material draft switch: '+name)
        elif isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not control.minimum()<=value<=control.maximum():
            raise ValueError('Invalid material draft value: '+name)
        elif isinstance(control,QtWidgets.QSpinBox) and type(value) is not int:raise ValueError('Invalid integer draft value: '+name)
    if draft.get('layout') not in ('compact','studio'):raise ValueError('Unknown material draft layout')
    for key in ('body_index','material_section','spread_section'):
        if type(draft.get(key)) is not int or draft[key]<0:raise ValueError('Invalid draft selection')
    for key in ('process_tab','histogram','history','brightness','logarithmic'):
        if type(draft.get(key)) is not bool:raise ValueError('Invalid draft display setting')
    if draft['material_section']>=window.material_sections.count():raise ValueError('Invalid material section')
    spread=getattr(getattr(window,'workflow',None),'spread_sections',None)
    if spread is not None and draft['spread_section']>=spread.count():raise ValueError('Invalid spread section')
    choice=window.workflow.choice if hasattr(window,'workflow') else window.operation_choice
    if choice.findData(draft.get('operation'))<0:raise ValueError('Unknown draft operation')
    for name,value in values.items():
        control=targets[name];previous=control.blockSignals(True)
        try:
            if isinstance(control,QtWidgets.QComboBox):control.setCurrentIndex(control.findData(value))
            elif isinstance(control,QtWidgets.QCheckBox):control.setChecked(value)
            else:control.setValue(value)
        finally:control.blockSignals(previous)
    window._sync()
    if window.body_choice.count() and draft['body_index']>=window.body_choice.count():raise ValueError('Invalid selected material body')
    window.body_choice.blockSignals(True);window.body_choice.setCurrentIndex(draft['body_index']);window.body_choice.blockSignals(False)
    pending=draft.get('pending_optical')
    if pending is not None:
        from film_foundry.contracts.optical_cascade import OpticalRelay
        if not isinstance(pending,dict) or not isinstance(pending.get('path'),str) or not isinstance(pending.get('choices'),list):raise ValueError('Invalid pending optical source')
        relay=OpticalRelay.from_value(pending.get('relay'))
        if not window.choose_optical_source(pending['path'],choices=pending['choices']):raise ValueError('Invalid pending optical source choices')
        panel=window.optical_panel;index=panel.body.findData(pending.get('body'))
        if index<0:raise ValueError('Invalid pending source body')
        panel.body.setCurrentIndex(index)
        if pending['body'] is not None and panel.mode.currentData()!=relay.mode:raise ValueError('Invalid pending source mode')
        panel.mode.setCurrentIndex(panel.mode.findData(relay.mode));panel.ev.setValue(relay.exposure_ev);panel.seconds.setValue(relay.exposure_seconds)
        for control,value in zip(panel.filters,relay.filter_density_rgb):control.setValue(value)
        panel.setToolTip(panel.toolTip()+'\n'+text('此来源尚未载入，应用时仍需原介质文件。','This pending source still requires the original material file when applied.'))
    restore_presentation(window,draft)
    if draft.get('view') is not None:
        from film_foundry.core.material_family import ViewingConditions
        window._apply_result_view(ViewingConditions(**draft['view']))


def restore_presentation(window,draft):
    window.set_workspace_layout(draft['layout'])
    window.material_sections.setCurrentIndex(draft['material_section'])
    choice=window.workflow.choice if hasattr(window,'workflow') else window.operation_choice
    choice.setCurrentIndex(choice.findData(draft['operation']))
    if draft['layout']=='compact':window.config_tabs.setCurrentWidget(window.process_group if draft['process_tab'] else window.material_group)
    if hasattr(window,'workflow') and hasattr(window.workflow,'spread_sections'):
        window.workflow.spread_sections.setCurrentIndex(draft['spread_section'])
    inspector=window.inspector
    inspector.histogram.brightness=draft['brightness'];inspector.histogram.logarithmic=draft['logarithmic'];inspector.histogram.update()
    inspector.histogram_button.setChecked(draft['histogram']);inspector.history_button.setChecked(draft['history'])
