"""Portable material documents: exact formation, exposure pixels and UI data.

The directory package is a checkpoint, not a recipe replay or a new medium
schema. Application code owns persistence; no Qt objects enter this module.
"""
from copy import copy, deepcopy
from dataclasses import dataclass
import json
import shutil
from pathlib import Path

import numpy as np

from film_foundry.app.material_lab_session import MaterialLabSession, FAMILIES
from film_foundry.app import material_families
from film_foundry.core.atomic_io import atomic_output_directory, atomic_write_json, atomic_savez, strict_json_load
from film_foundry.core.material_parallel import thread_count
from film_foundry.contracts.optical_cascade import OpticalRelay, validate_cascade

MODEL='material_workspace_v1'
PORTABLE_MODEL='material_workspace_v2'
MAX_DOCUMENTS=32


@dataclass
class MaterialDocument:
    session: MaterialLabSession
    draft: dict
    title: str = ''
    batch_paths: tuple[str, ...] | None = None


@dataclass
class MaterialWorkspace:
    documents: list[MaterialDocument]
    active: int = 0


def _json_copy(value):
    encoded=json.dumps(value,ensure_ascii=False,allow_nan=False)
    if len(encoded.encode('utf-8'))>4*1024*1024:raise ValueError('Workspace metadata is too large')
    return json.loads(encoded)


def snapshot_document(session,draft,*,title='',batch_paths=None):
    """Capture stable references while its frontend has no operation running."""
    current=copy(session)
    current.source_info=deepcopy(session.source_info)
    current.scan_adjustments=deepcopy(session.scan_adjustments)
    current.optical_reference=deepcopy(session.optical_reference)
    if session.optical_source is not None:
        from film_foundry.app.optical_cascade import source_reference
        current.optical_reference=source_reference(session.optical_source,session.optical_relay,
            body_id=session.optical_body_id,source_name=session.source_name)
    # The projected linear source is already in image. Upstream material pools
    # are unnecessary to resume this document and are not silently re-read.
    current.optical_source=None
    return MaterialDocument(current,_json_copy(draft),str(title),tuple(batch_paths) if batch_paths is not None else None)


def _manifest(path):
    file=path/'workspace.json'
    if file.stat().st_size>4*1024*1024:raise ValueError('Workspace metadata is too large')
    value=strict_json_load(file)
    if not isinstance(value,dict) or value.get('model') not in (MODEL,PORTABLE_MODEL):raise ValueError('Not a supported material workspace')
    documents=value.get('documents')
    if not isinstance(documents,list) or not 1<=len(documents)<=MAX_DOCUMENTS:raise ValueError('Invalid document count')
    if type(value.get('active')) is not int or not 0<=value['active']<len(documents):raise ValueError('Invalid active document')
    for entry in documents:
        if not isinstance(entry,dict) or entry.get('family') not in FAMILIES or not isinstance(entry.get('draft'),dict):raise ValueError('Invalid material document')
        if type(entry.get('formation')) is not bool or type(entry.get('exposure')) is not bool:raise ValueError('Invalid document payload flags')
        if not isinstance(entry.get('title'),str):raise ValueError('Invalid document title')
        paths=entry.get('batch_paths')
        if paths is not None and (not isinstance(paths,list) or not paths or any(not isinstance(p,str) for p in paths)):raise ValueError('Invalid batch sources')
        if value['model']==PORTABLE_MODEL:
            sources=entry.get('sources',[])
            if not isinstance(sources,list) or len(set(sources))!=len(sources):raise ValueError('Invalid portable material sources')
            for name in sources:
                if not isinstance(name,str) or not name or Path(name).name!=name or any(c in name for c in '/\\:') or name in ('.','..'):
                    raise ValueError('Invalid portable source filename')
            if paths is not None and any(p not in sources for p in paths):raise ValueError('Missing batch source attachment')
            pending=entry['draft'].get('pending_optical')
            if pending is not None and pending.get('path') not in sources:raise ValueError('Missing pending optical attachment')
    return value


def _contained(path,root):
    # Fixed names are generated internally, but reject symbolic links/junctions
    # as well: replacing a workspace must never remove another directory tree.
    if path.resolve()!=path.absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Workspace links outside its package are not supported')
    return path


def _check_package(path,manifest):
    expected={'workspace.json',*(f'doc_{i:03}' for i in range(len(manifest['documents'])))}
    if {child.name for child in path.iterdir()}!=expected:raise ValueError('Workspace contains missing or unrelated files; choose another save location')
    _contained(path/'workspace.json',path)
    for index,entry in enumerate(manifest['documents']):
        directory=_contained(path/f'doc_{index:03}',path)
        files=({'formation.npz'} if entry['formation'] else set())|({'exposure.npz'} if entry['exposure'] else set())
        if entry.get('sources'):files.add('sources')
        if {child.name for child in directory.iterdir()}!=files:raise ValueError('Workspace document contains missing or unrelated files')
        for name in files:_contained(directory/name,path)
        if entry.get('sources'):
            sources=directory/'sources'
            if {p.name for p in sources.iterdir()}!=set(entry['sources']):raise ValueError('Workspace source contains missing or unrelated files')
            for name in entry['sources']:
                if not _contained(sources/name,path).is_file():raise ValueError('Workspace source is missing')


def save_material_workspace(path,documents,*,active=0):
    target=Path(path).absolute()
    if target.suffix.lower()!='.ffworkspace':raise ValueError('Choose a .ffworkspace directory package')
    if target.resolve()!=target:raise ValueError('Workspace must not be a symbolic link or junction')
    if not 1<=len(documents)<=MAX_DOCUMENTS or type(active) is not int or not 0<=active<len(documents):raise ValueError('Invalid workspace documents')
    if target.exists():_check_package(target,_manifest(target))
    manifest={'model':PORTABLE_MODEL,'active':active,'documents':[]}
    # Build everything first. The existing directory is moved only at commit;
    # normal write/rename failures restore it through the shared atomic writer.
    with atomic_output_directory(target) as staging:
        for index,document in enumerate(documents):
            session=document.session
            if session.kind not in FAMILIES:raise ValueError('Unknown material family')
            directory=staging/f'doc_{index:03}';directory.mkdir()
            draft=deepcopy(document.draft);sources=[];batch_paths=None
            def copy_source(path,name):
                source=Path(path)
                if not source.is_file():raise ValueError(f'Workspace source is missing: {source.name}')
                source_dir=directory/'sources';source_dir.mkdir(exist_ok=True)
                before=source.stat();shutil.copy2(source,source_dir/name);after=source.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Workspace source changed while copying')
                sources.append(name)
            if document.batch_paths is not None:
                batch_paths=[]
                for source_index,source in enumerate(document.batch_paths):
                    name=f'batch_{source_index:06d}'+Path(source).suffix
                    copy_source(source,name);batch_paths.append(name)
            pending=draft.get('pending_optical')
            if pending is not None:
                original=Path(pending['path']);name='pending'+''.join(original.suffixes)
                copy_source(original,name);pending['path']=name
                sidecar=original.with_suffix(original.suffix+'.json')
                if sidecar.is_file():copy_source(sidecar,name+'.json')
            formation=session.state if session.state is not None else session.group
            if formation is not None:material_families.save(directory/'formation.npz',formation,precision=session.storage_precision)
            if session.image is not None:
                atomic_savez(directory/'exposure.npz',compression_level=1,parallel_compression=thread_count()>1,durable=True,image=session.image)
            reference=session.optical_reference
            if session.optical_source is not None:
                from film_foundry.app.optical_cascade import source_reference
                reference=source_reference(session.optical_source,session.optical_relay,body_id=session.optical_body_id,source_name=session.source_name)
            manifest['documents'].append({'family':session.kind,'title':document.title,'draft':draft,
                'batch_paths':batch_paths,'sources':sources,
                'formation':formation is not None,'exposure':session.image is not None,
                'source_name':session.source_name,'source_path':session.source_path,'source_info':session.source_info,'scan_adjustments':session.scan_adjustments,'storage_precision':session.storage_precision,
                'optical_reference':reference,'optical_relay':session.optical_relay.as_dict() if session.optical_relay else None,
                'optical_body_id':session.optical_body_id})
        atomic_write_json(staging/'workspace.json',_json_copy(manifest))
    return target


def load_material_workspace(path):
    root=Path(path).absolute()
    if root.name=='workspace.json':root=root.parent
    if root.resolve()!=root:raise ValueError('Workspace must not be a symbolic link or junction')
    manifest=_manifest(root);_check_package(root,manifest)
    documents=[]
    for index,entry in enumerate(manifest['documents']):
        directory=root/f'doc_{index:03}';session=MaterialLabSession(entry['family'])
        if entry['formation']:session.load(directory/'formation.npz')
        if entry['exposure']:
            with np.load(directory/'exposure.npz',allow_pickle=False) as archive:image=archive['image']
            if image.dtype!=np.float32 or image.ndim!=3 or image.shape[-1]!=3 or not image.size or not np.isfinite(image).all() or (image<0).any():
                raise ValueError('Invalid linear exposure pixels')
            if session.state is not None and image.shape[:2]!=next(iter(session.state.fields.values())).shape[:2]:raise ValueError('Exposure and material dimensions differ')
            session.image=image
        reference=entry.get('optical_reference');relay=entry.get('optical_relay')
        if reference is not None:
            if not isinstance(reference,dict) or set(reference)!={'source','parent'} or not isinstance(reference['source'],dict) or relay is None or session.image is None:
                raise ValueError('Invalid optical exposure reference')
            if reference['parent'] is not None:validate_cascade(reference['parent'])
        elif relay is not None:raise ValueError('Optical relay lacks its source reference')
        session.optical_reference=deepcopy(reference);session.optical_relay=OpticalRelay.from_value(relay) if relay is not None else None
        session.optical_body_id=entry.get('optical_body_id')
        for key in ('source_name','source_path'):
            if not isinstance(entry.get(key),str):raise ValueError('Invalid source description')
            setattr(session,key,entry[key])
        if not isinstance(entry.get('source_info'),dict):raise ValueError('Invalid source metadata')
        session.source_info=entry['source_info']
        from film_foundry.app.material_scan import normalize_adjustments
        session.scan_adjustments=normalize_adjustments(entry.get('scan_adjustments'))
        from film_foundry.core.storage_precision import precision_policy
        session.storage_precision=precision_policy(entry.get('storage_precision','full'))
        draft=deepcopy(entry['draft']);batch=entry['batch_paths']
        if manifest['model']==PORTABLE_MODEL:
            if batch is not None:batch=[str(directory/'sources'/name) for name in batch]
            if draft.get('pending_optical') is not None:draft['pending_optical']['path']=str(directory/'sources'/draft['pending_optical']['path'])
        documents.append(MaterialDocument(session,draft,entry['title'],tuple(batch) if batch is not None else None))
    return MaterialWorkspace(documents,manifest['active'])
