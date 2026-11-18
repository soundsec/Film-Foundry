"""Bind display history to existing film/material parameter and undo contracts."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from film_foundry.core.file_cache import file_revision
from film_foundry.ui.preview_history import PreviewHistory
from film_foundry.ui.curve_editor import text


def source_revision(path):
    path=Path(path);sidecar=path.with_suffix(path.suffix+'.json')
    return file_revision(path),file_revision(sidecar) if sidecar.is_file() else None


def film_context(owner):
    mode=owner.pipeline_mode.get()
    source=owner.negative_path.get() if mode in ('scan','develop_latent') else owner.input_path.get()
    return mode,source,tuple(owner._selected_photos),str(owner._active_roll.path) if owner._active_roll else None


def install_film(owner):
    def allowed(entry):
        if owner._processing or owner._preview_running or owner._workspace_io_busy:return text('等待当前任务完成后恢复参数。','Wait for the active task before restoring settings.')
        if owner._active_roll is not None or film_context(owner)!=entry.context:return text('来源或执行终点已改变。','Source or endpoint changed.')
        return ''
    def restore(entry):
        if source_revision(entry.payload['path'])!=entry.guard:raise ValueError(text('来源文件已变更，请重新预览。','Source file changed; update the preview.'))
        owner.edit_tools.record()
        owner.edit_tools.apply(deepcopy(entry.payload['settings']))
        owner.edit_tools.record()
        owner.status.set(text('已恢复历史参数，可撤回；请更新预览确认。','Historical settings restored; Undo is available. Update preview to verify.'))
    history=getattr(owner,'preview_history',None)
    if history is None:
        history=owner.preview_history=PreviewHistory(owner.root.qt,lambda:film_context(owner),allowed,restore)
    from film_foundry.ui.parameter_pins import workbench_labels
    history.labels=lambda:workbench_labels(owner,True)


def prepare_film(owner,request):
    revision=source_revision(request.source_path)
    history=owner.preview_history;history.synchronize()
    if history.entries and history.entries[-1].guard!=revision:history.clear()
    return dict(context=film_context(owner),revision=revision,
                payload={'path':str(request.source_path),'settings':owner.edit_tools.capture()})


def publish_film(owner,pending):
    if pending is None:return
    if source_revision(pending['payload']['path'])!=pending['revision']:return
    payload=deepcopy(pending['payload'])
    payload['settings']['config']['output']['rotation_quarters']=owner.result_view.rotation_quarters
    image=owner.result_view.displayed_result().toImage()
    c=payload['settings']['config']
    offset=payload['settings'].get('frame_exposure_offsets',{}).get(str(Path(payload['path']).resolve()),0.) if pending['context'][0] in ('full','develop','expose') else 0.
    label=(text('曝光 ','Exposure ')+f"{c['look']['exposure_ev']+offset:+.2f} EV · "
           +text('冲洗 ','Develop ')+f"{c['chemistry']['time_min']:g} min / {c['chemistry']['temperature_c']:g}°C · "
           +text('观察 ','View ')+f"{c['look']['print_exposure_ev']:+.2f} EV")
    owner.preview_history.add(image,payload,pending['context'],pending['revision'],label)


def material_context(owner):
    s=owner.session
    lineage=getattr(s.state,'group_id',None) or getattr(s.group,'group_id',None)
    return s.kind,s.source_path,id(s.image) if s.image is not None else lineage or id(s)


def material_guard(owner):
    s=owner.session
    return (id(s),id(s.state),id(s.group),id(s.image),
            getattr(s.state,'process_json',None),getattr(s.group,'provenance_json',None))


def install_material(owner,layout):
    from film_foundry.ui import material_workspace_state as drafts
    def allowed(entry):
        if owner.busy:return text('等待当前操作完成后恢复参数。','Wait for the current operation before restoring.')
        if material_context(owner)!=entry.context:return text('来源已改变。','Source changed.')
        if material_guard(owner)!=entry.guard:return text('材料已推进或替换：此记录仅供对照。回到工序请使用撤回或材料存档。',
            'Material advanced or changed: comparison only. Use Undo or a material archive to return to an earlier state.')
        return ''
    def restore(entry):
        owner.edit_tools.record_draft()
        draft=deepcopy(entry.payload['draft'])
        # Restore settings, retaining the current presentation/layout choices.
        current=drafts.capture(owner)
        current['values']=draft['values'];current['view']=draft['view'];current['body_index']=draft['body_index']
        owner.session.scan_adjustments=deepcopy(entry.payload['scan'])
        drafts.restore(owner,current)
        owner.edit_tools.record_draft();owner._last_observation_key=None
        owner.request_preview()
        owner.status.setText(text('已恢复历史参数，可撤回；材料状态保持。','Historical settings restored; Undo available. Material state retained.'))
    owner.preview_history=PreviewHistory(owner,lambda:material_context(owner),allowed,restore)
    from film_foundry.ui.parameter_pins import workbench_labels
    owner.preview_history.labels=lambda:workbench_labels(owner,False)
    owner.preview_history.button(layout)


def prepare_material(owner,index):
    from film_foundry.ui.material_workspace_state import capture
    draft=capture(owner);draft['body_index']=index
    return dict(draft=draft,scan=deepcopy(owner.session.scan_adjustments))


def publish_material(owner,payload,label):
    if payload is None or owner.viewer.result is None:return
    # Images and numeric settings only; the guard is identity, never a state reference.
    owner.preview_history.add(owner.viewer.result.toImage(),payload,material_context(owner),material_guard(owner),
        label.rstrip('…')+' · '+f"{payload['draft']['view']['exposure_ev']:+.2f} EV")
