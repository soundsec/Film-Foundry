"""Explicit source selections and result records over the public workflow API."""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
from dataclasses import asdict, replace
from datetime import datetime
from time import perf_counter

from film_foundry.app.api import WorkflowRequest,run_request
from film_foundry.core.atomic_io import atomic_write_json


def workflow_result_record(source,result,*,output_path=None):
    """Index every film endpoint, including separate exposure/development/scan jobs."""
    source=Path(source).resolve();written=Path(output_path or result.output_path).resolve()
    medium=source if result.command=='scan' else Path(result.medium_path or written).resolve()
    artifacts={k:str(Path(v).resolve()) for k,v in result.extra.get('paths',{}).items() if v is not None}
    cover=str(written) if result.command in ('full','scan') else next((p for k,p in artifacts.items() if 'visual_preview' in k),'')
    if result.command in ('full','scan'):artifacts['scan:'+written.stem]=str(written)
    record={'key':'photo:'+str(source),'source':str(source),'label':source.name,'family':'film',
            'command':result.command,'medium':str(medium),'cover':cover,'artifacts':artifacts}
    manifest=written.with_name(written.name+'.result.json')
    record['artifacts']['result']=str(manifest)
    atomic_write_json(manifest,record)
    return record


def scan_record(record, config, *, control=None, output=None):
    if control:control.check_cancel()
    medium=Path(record['medium'])
    result=run_request(WorkflowRequest('scan',medium,medium.parent,config,control=control))
    suffix='.'+config.output.format.lower().lstrip('.')
    destination=medium.parent if output is None else Path(output)
    target=destination/('scan-'+str(uuid4())[:8]+suffix)
    from film_foundry.core.io_utils import save_image_bundle
    from film_foundry.core.sidecar import final_positive_sidecar
    sidecar=final_positive_sidecar(negative_path=medium,scan_source=result.source_kind,scan_source_path=result.source_path or medium,
        output_path=target,config=config,scanned=result.extra['scanned']) if config.save_sidecar else None
    if control:control.check_cancel()
    save_image_bundle(result.extra['scanned'].output_srgb,target,config.output,sidecar,protected_paths=(medium,))
    record.setdefault('artifacts',{})['scan:'+target.stem]=str(target)
    record['cover']=str(target)
    if sidecar:record['artifacts']['scan_metadata:'+target.stem]=str(target.with_suffix(target.suffix+'.json'))
    atomic_write_json(medium.parent/'result.json',record)
    return record


def numbered_photo_config(config,index):
    """A user-defined sequence label, independent of roll/frame identity."""
    if config.film_border.frame_number is None:return config
    result=deepcopy(config);result.film_border.frame_number+=index-1
    return result


def process_photos(paths, output, config, *, control=None, on_result=None,scaled_override=False,on_run=None,path_seed_offsets=None,command="full"):
    """No selection limit; one full-resolution image is resident at a time."""
    if command not in {"full","develop","expose","scan"}:raise ValueError("Photo jobs require an image-source workflow")
    paths=list(dict.fromkeys(str(Path(p).resolve()) for p in paths))
    from film_foundry.app.source_seed import normalize_offsets,offset_for
    path_seed_offsets=normalize_offsets(path_seed_offsets)
    config=deepcopy(config)
    from film_foundry.app.workflow import create_run_directory
    root,run_id=create_run_directory(Path(output),command)
    if on_run:on_run(root)
    atomic_write_json(root/'settings.json',asdict(config))
    from film_foundry.model.session import session_payload,save_session
    from film_foundry.app.workflow import batch_item_record,finalize_batch_run
    payload=session_payload(config=config,pipeline_mode=command,input_path=paths[0] if paths else '',negative_path=paths[0] if paths and command=='scan' else '',output_path=str(output),presets={})
    payload['run']={'id':run_id,'directory':str(root),'actual_output':str(root),'status':'running','inputs':paths,'selection_layout':'numbered'}
    payload['ui_options']={'run_as_preview':scaled_override}
    payload['path_seed_offsets']=path_seed_offsets
    from film_foundry.app.workflow import planned_developed_path
    from film_foundry.core.media import developed_medium_contract
    def destination(index,path):
        directory=root/f'{index:04d}'
        if command=='expose':return directory/(Path(path).stem+'.darkroom_latent.npz')
        if command=='develop':return planned_developed_path(Path(path),directory,image_polarity=developed_medium_contract(config)['image_polarity'])
        return directory/('scan.'+config.output.format.lower().lstrip('.'))
    records=[{'input':path,'output':str(destination(i,path)),'status':'pending','elapsed_seconds':0.} for i,path in enumerate(paths,1)]
    payload['batch']={'operation':command,'status':'running','items':records}
    manifest=root/'film_foundry_run.json';save_session(manifest,payload)
    started=perf_counter();started_at=datetime.now().isoformat(timespec='seconds')
    failures=[]
    for index,path in enumerate(map(Path,paths),1):
        item_start=perf_counter();target=Path(records[index-1]['output'])
        try:
            if control:control.check_cancel()
            item_config=numbered_photo_config(config,index) if command!='scan' else config
            result=run_request(WorkflowRequest(command,path,target,item_config,control=control,scaled_override=scaled_override,
                path_seed_offset=offset_for(path,path_seed_offsets)))
            if command=='scan':
                from film_foundry.core.io_utils import save_image_bundle
                from film_foundry.core.sidecar import final_positive_sidecar
                sidecar=final_positive_sidecar(negative_path=path,scan_source=result.source_kind,scan_source_path=path,
                    output_path=target,config=config,scanned=result.extra['scanned']) if config.save_sidecar else None
                if control:control.check_cancel()
                save_image_bundle(result.extra['scanned'].output_srgb,target,config.output,sidecar,protected_paths=(path,))
                result=replace(result,output_path=target)
            if command=='full':
                record={'key':'photo:'+str(path.resolve()),'source':str(path.resolve()),'label':path.name,
                    'medium':str(result.medium_path),'cover':str(result.output_path),'artifacts':{k:str(v) for k,v in result.extra['paths'].items()}}
                del result
                atomic_write_json(Path(record['medium']).parent/'result.json',record)
            else:
                record=workflow_result_record(path,result)
                del result
            if on_result:on_result(deepcopy(record))
            records[index-1]=batch_item_record(path,target,perf_counter()-item_start)
        except Exception as exc:
            from film_foundry.app.control import WorkflowCancelled
            records[index-1]=batch_item_record(path,target,perf_counter()-item_start,exc)
            if getattr(exc,'code',None)=='cancelled' or isinstance(exc,WorkflowCancelled):
                payload['run']['status']=payload['batch']['status']='cancelled';save_session(manifest,payload);raise
            failures.append({'source':str(path),'error':str(exc)})
        save_session(manifest,payload)
    finalize_batch_run(payload,operation=command,started_at=started_at,elapsed_seconds=perf_counter()-started,items=records)
    save_session(manifest,payload)
    atomic_write_json(root/'batch.json',{'sources':[str(p) for p in paths],'failures':failures})
    return failures


def process_material_photos(kind,paths,output,material,steps,view,long_edge=0,seed=0,*,on_result=None,control=None,on_run=None,scan_adjustments=None,storage_precision="auto"):
    from film_foundry.app.material_batch_run import create_batch,run_batch
    if control:control.check_cancel()
    manifest=create_batch(kind,paths,output,material,steps,view,long_edge,seed,on_run=on_run,scan_adjustments=scan_adjustments,storage_precision=storage_precision)
    return run_batch(manifest,on_result=on_result,control=control)


def archive_material_result(session,root,view):
    """Reuse one archive for an unchanged in-memory formation, without hashes.

    Weak ownership avoids retaining a previous full-size state after processing.
    View changes regenerate only observation images; processing creates a new run.
    """
    if session.group is None:raise ValueError('Form a material before archiving results')
    from film_foundry.app.material_result_store import save_result,refresh_observations
    root=Path(root).resolve();cached=getattr(session,'_result_archive_cache',None)
    if (cached and cached['root']==root and cached['group']() is session.group and
            cached['state']() is session.state and cached['record'].get('storage_precision','full')==session.storage_precision and Path(cached['manifest']).is_file() and
            all(Path(p).is_file() for p in cached['record']['artifacts'].values())):
        record=deepcopy(cached['record'])
        from film_foundry.core.atomic_io import strict_json_load
        import json
        # Another open page may have refreshed this same group since loading.
        # Compare only its small persisted viewing record, never pixel hashes.
        saved_observation=strict_json_load(cached['manifest'])
        current_view=saved_observation.get('view',cached['view'])
        requested_view=json.loads(json.dumps(asdict(view)))
        from film_foundry.app.material_scan import normalize_adjustments
        if current_view!=requested_view or normalize_adjustments(saved_observation.get('scan_adjustments'))!=normalize_adjustments(session.scan_adjustments):
            refresh_observations(session,record,view)
        record['view']=requested_view
        record['scan_adjustments']=deepcopy(session.scan_adjustments)
        cached['view']=deepcopy(requested_view)
        cached['record']=deepcopy(record)
        return record
    target=root/str(uuid4());record=save_result(session,target,view)
    from film_foundry.app.material_result_store import remember_result
    remember_result(session,record,view)
    return record
