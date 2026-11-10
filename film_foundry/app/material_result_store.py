"""Commit a complete material result before exposing its catalogue record.

Material formation and encoders remain in their existing APIs. This layer owns
only the bundle, its logical final paths, and publication/rollback boundaries.
"""
from pathlib import Path
from copy import deepcopy
from dataclasses import asdict
from tempfile import TemporaryDirectory
import os
import json
import weakref
from uuid import uuid4
import numpy as np
from film_foundry.core.atomic_io import atomic_output_directory,atomic_path_set,atomic_write_json,atomic_savez,strict_json_load

MODEL='material_result_v2'


def save_result(session,target,view,*,settings_path=None,control=None,batch_item=None):
    if session.group is None:raise ValueError('Form a material before archiving results')
    target=Path(target).resolve()
    source=session.source_path
    record={'key':'photo:'+source,'source':source,'label':session.source_name,
            'storage_precision':session.storage_precision,'model':MODEL,'result_id':str(uuid4()),'group_id':session.group.group_id,'view':asdict(view),'scan_adjustments':deepcopy(session.scan_adjustments),
            'medium':str(target/'products.ffmaterial.npz'),'family':session.kind,
            'artifacts':{'products':str(target/'products.ffmaterial.npz')}}
    reference=session.optical_reference
    if session.optical_source is not None:
        from film_foundry.app.optical_cascade import source_reference
        reference=source_reference(session.optical_source,session.optical_relay,body_id=session.optical_body_id,source_name=session.source_name)
    record['input']={'source_info':deepcopy(session.source_info),'optical_reference':deepcopy(reference),
        'optical_relay':session.optical_relay.as_dict() if session.optical_relay else None,'optical_body_id':session.optical_body_id}
    record['bodies']=[{'body_id':b.body_id,'role':b.role,'usable_negative':b.usable_negative} for b in session.group.bodies]
    if getattr(session,'_result_parent',None):record['parent']=deepcopy(session._result_parent)
    if batch_item is not None:record['batch_item']=deepcopy(batch_item)
    if settings_path is not None:record['artifacts']['settings']=str(Path(settings_path).resolve())
    def check():
        if control:control.check_cancel()
    check()
    with atomic_output_directory(target,replace_existing=False) as staging:
        session.save_products(staging/'products.ffmaterial.npz')
        if session.state is not None:
            check();session.save(staging/'state.ffmaterial.npz')
            record['artifacts']['process_state']=str(target/'state.ffmaterial.npz')
        if session.image is not None:
            from film_foundry.core.material_parallel import thread_count
            check();atomic_savez(staging/'exposure.npz',compression_level=1,parallel_compression=thread_count()>1,durable=True,image=session.image)
            record['artifacts']['working_exposure']=str(target/'exposure.npz')
        from film_foundry.contracts.material_recipe import KEY as RECIPE_KEY
        process=json.loads(session.state.process_json) if session.state is not None else json.loads(session.group.provenance_json).get('process',{})
        if RECIPE_KEY in process:
            check();session.save_recipe(staging/'recipe.json')
            record['artifacts']['recipe']=str(target/'recipe.json')
        for index,body in enumerate(session.group.bodies):
            check();name=body.role+'.ffmaterial.npz'
            session.save(staging/name,index);record['artifacts'][body.role]=str(target/name)
            check();name=body.role+'.png'
            session.export_png(staging/name,view,index)
            record['artifacts'][body.role+':image']=str(target/name)
            record['artifacts'][body.role+':observation']=str(target/(name+'.json'))
            if index==0:record['cover']=str(target/name)
        record['bundle_files']={key:Path(value).name for key,value in record['artifacts'].items() if Path(value).parent==target}
        record['artifacts']['result']=str(target/'result.json')
        record=json.loads(json.dumps(record,ensure_ascii=False,allow_nan=False))
        check();atomic_write_json(staging/'result.json',record)
        check()
    # Neither callbacks nor the session cache can see a partially formed set.
    return record


def refresh_observations(session,record,view):
    """Keep the previous observations visible throughout rendering/encoding."""
    images=[Path(record['artifacts'][body.role+':image']) for body in session.group.bodies]
    targets=[*images,*(path.with_suffix(path.suffix+'.json') for path in images)]
    manifest=Path(record['medium']).parent/'result.json'
    updated=deepcopy(record);updated['view']=json.loads(json.dumps(asdict(view),allow_nan=False));updated['scan_adjustments']=deepcopy(session.scan_adjustments)
    if record.get('model')==MODEL:targets.append(manifest)
    # The directory is owned solely by this refresh; no search/cleanup of user outputs.
    with TemporaryDirectory(prefix='.ff-observe-',dir=images[0].parent) as temporary:
        staging=Path(temporary)
        for index,path in enumerate(images):session.export_png(staging/path.name,view,index)
        if manifest in targets:atomic_write_json(staging/manifest.name,updated)
        with atomic_path_set(targets):
            for path in targets:os.replace(staging/path.name,path)
    record.update(updated)


def result_record(path):
    """Rebind an explicit bundle or one of its registered members after a move."""
    path=Path(path).resolve();root=path if path.is_dir() else path.parent
    manifest=root/'result.json'
    if not manifest.is_file():return None
    record=strict_json_load(manifest)
    if not isinstance(record,dict):return None
    from film_foundry.app.material_lab_session import FAMILIES
    if record.get('model') is None and record.get('family') in FAMILIES and Path(record.get('medium','')).name=='products.ffmaterial.npz':
        # Old complete bundles already have all media; migrate only their index
        # in memory, never infer missing process state or exposure from an image.
        record=deepcopy(record);old_root=Path(record['medium']).parent
        record['bundle_files']={role:Path(value).name for role,value in record.get('artifacts',{}).items() if Path(value).parent==old_root}
        record['model']=MODEL;record['_legacy_bundle']=True
        from film_foundry.core.material_family import ViewingConditions
        record['view']=asdict(ViewingConditions())
        for role,name in record['bundle_files'].items():
            if role.endswith(':observation') and (root/name).is_file():
                metadata=strict_json_load(root/name).get('FilmFoundryObservation',{})
                if isinstance(metadata.get('view'),dict):record['view']=metadata['view'];break
    if record.get('model')!=MODEL:return None
    files=record.get('bundle_files')
    if record.get('family') not in FAMILIES or not isinstance(files,dict) or files.get('products')!='products.ffmaterial.npz':
        raise ValueError('Invalid material result bundle')
    artifacts={}
    for role,name in files.items():
        if (not isinstance(role,str) or not isinstance(name,str) or Path(name).name!=name
                or name in ('','.','..') or any(c in name for c in '/\\:')):
            raise ValueError('Invalid material result member')
        member=root/name
        if not member.resolve().is_relative_to(root) or member.is_symlink():raise ValueError('Material result links are not supported')
        artifacts[role]=str(member)
    if path!=root and path!=manifest and str(path) not in artifacts.values():return None
    # External one-off exports are not package dependencies or write targets.
    record=deepcopy(record);record['artifacts']=artifacts|{'result':str(manifest)}
    record['medium']=artifacts['products']
    bodies=record.get('bodies')
    if not record.get('_legacy_bundle') and (not isinstance(bodies,list) or not bodies):raise ValueError('Material result lacks its body relationships')
    record['cover']=artifacts.get(bodies[0]['role']+':image','') if bodies else next((p for role,p in artifacts.items() if role.endswith(':image')),'')
    return record


def remember_result(session,record,view):
    root=Path(record['medium']).parent
    session._result_parent={'medium':record['medium'],'result_id':record.get('result_id'),'group_id':record.get('group_id')}
    session._result_archive_cache={'root':root.parent,'manifest':str(root/'result.json'),
        'group':weakref.ref(session.group),'state':weakref.ref(session.state) if session.state is not None else lambda:None,
        'record':deepcopy(record),'view':deepcopy(asdict(view) if view is not None else record.get('view'))}


def load_result(path,*,kind=None):
    """Load all associated media and optional reactive state without reforming."""
    from film_foundry.app.material_lab_session import MaterialLabSession
    from film_foundry.app import material_families as api
    from film_foundry.core.material_family import ProductGroup,ViewingConditions
    from film_foundry.contracts.optical_cascade import OpticalRelay
    record=result_record(path)
    if record is None:raise ValueError('Choose a material result bundle')
    if kind is not None and kind!=record['family']:raise ValueError('Result belongs to another material family')
    session=MaterialLabSession(record['family']);group=api.load(record['medium'])
    if record.pop('_legacy_bundle',False) and isinstance(group,ProductGroup):
        record['group_id']=group.group_id
        record['bodies']=[{'body_id':b.body_id,'role':b.role,'usable_negative':b.usable_negative} for b in group.bodies]
    if (not isinstance(group,ProductGroup) or group.group_id!=record.get('group_id')
            or any(b.family!=session.module.FAMILY for b in group.bodies)
            or [(b.body_id,b.role,b.usable_negative) for b in group.bodies]!=[(b['body_id'],b['role'],b['usable_negative']) for b in record['bodies']]):
        raise ValueError('Material result body relationships do not match')
    state_path=record['artifacts'].get('process_state')
    if state_path:
        state=api.load(state_path)
        if api.state_family(state)!=session.module.FAMILY or state.group_id!=group.group_id or state.stage not in session.module.FINAL_STAGES:
            raise ValueError('Material result state does not match its products')
        session.state=state
    session.group=group;session.source_path=record['source'];session.source_name=record['label']
    exposure=record['artifacts'].get('working_exposure')
    if exposure:
        with np.load(exposure,allow_pickle=False) as archive:image=archive['image']
        shape=next(iter(group.bodies[0].fields.values())).shape[:2]
        if image.dtype!=np.float32 or image.shape!=(*shape,3):raise ValueError('Invalid saved working exposure')
        session.image=image
    source=record.get('input',{});process=json.loads(session.state.process_json) if session.state is not None else json.loads(group.provenance_json).get('process',{})
    session.source_info=deepcopy(source.get('source_info',process.get('source_input',{})))
    session.optical_reference=deepcopy(source.get('optical_reference'))
    relay=source.get('optical_relay');session.optical_relay=OpticalRelay.from_value(relay) if relay is not None else None
    session.optical_body_id=source.get('optical_body_id')
    view=ViewingConditions(**record['view'])
    from film_foundry.app.material_scan import normalize_adjustments
    session.scan_adjustments=normalize_adjustments(record.get('scan_adjustments'))
    from film_foundry.core.storage_precision import precision_policy
    session.storage_precision=precision_policy(record.get('storage_precision','full'))
    remember_result(session,record,view)
    return session,record,view


def observation_result_record(session,path,view,body_index=0):
    """Index a rendered image without implicitly archiving chemical state."""
    path=Path(path).resolve();source=session.source_path
    cache=getattr(session,'_result_archive_cache',None)
    if cache and cache['group']() is session.group and cache['state']() is session.state:
        medium=cache['record']['medium']
        if Path(medium).is_file():source=medium
    record={'key':'observation:'+str(path),'source':source,'label':path.name,'family':session.kind,
            'command':'observe','medium':str(path),'cover':str(path),'view':asdict(view),
            'scan_adjustments':deepcopy(session.scan_adjustments),
            'artifacts':{'scan:observation':str(path),'observation':str(path.with_suffix(path.suffix+'.json'))}}
    if session.group:record.update(group_id=session.group.group_id,body_id=session.group.bodies[body_index].body_id)
    manifest=path.with_name(path.name+'.result.json');record['artifacts']['result']=str(manifest)
    atomic_write_json(manifest,record)
    return record
