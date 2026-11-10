"""Portable sealed rolls and a durable, whole-roll darkroom session.

The envelope owns order and lifecycle, never material formulas. Latent archives
retain the shared data kernel; a missing exposed frame is never a blank frame.
"""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json
import shutil
import zipfile

from film_foundry.app.api import WorkflowRequest, run_request
from film_foundry.app.incident_batch import prepare_incident_config
from film_foundry.contracts.data_kernel import PHONE_ROLL_CAPACITY_VALUES
from film_foundry.core.atomic_io import atomic_write_json, strict_json_load
from film_foundry.core.latent_io import load_exposed_latent, save_exposed_latent
from film_foundry.model.config import DarkroomConfig

ROLL_KIND = 'film_foundry_electronic_roll'


def check_manifest(value):
    if not isinstance(value,dict):raise ValueError('Roll manifest must be an object')
    if value.get('kind') != ROLL_KIND or value.get('version') != 1:
        raise ValueError('Unsupported electronic roll')
    if value.get('lifecycle') != 'sealed' or not isinstance(value.get('roll_id'), str) or not value['roll_id'].strip():
        raise ValueError('A sealed roll with an identity is required')
    capacity = value.get('capacity')
    if type(capacity) is not int or capacity not in PHONE_ROLL_CAPACITY_VALUES:
        raise ValueError('Unsupported roll capacity')
    frames = value.get('frames', [])
    if not isinstance(frames,list) or not all(isinstance(f,dict) and type(f.get('frame_index')) is int for f in frames) or len(frames) != capacity or [f.get('frame_index') for f in frames] != list(range(1, capacity+1)):
        raise ValueError('The manifest must enumerate every frame in order')
    names = []
    for frame in frames:
        if frame.get('state') == 'empty':
            if frame.get('file'): raise ValueError('Empty frame contains a latent file')
        elif frame.get('state') == 'latent':
            name = frame.get('file')
            if not isinstance(name, str) or '/' in name or '\\' in name or ':' in name or not name.endswith('.darkroom_latent.npz'):
                raise ValueError('Latent filenames must be plain relative filenames')
            names.append(name)
        else:
            raise ValueError('Failed, missing or unfinished captures must be recovered before importing')
    if not names or len(set(n.casefold() for n in names)) != len(names): raise ValueError('Missing or duplicate exposed frames')
    return value


def legacy_manifest(paths, capacity, *, confirmed_complete=False):
    if not confirmed_complete:
        raise ValueError('Confirm that all exposed frames were selected before declaring the remainder unexposed')
    frames = {}; roll_id = None
    for path in map(Path, paths):
        state, metadata = load_exposed_latent(path)
        identity = metadata.get('frame_identity') or {}
        index = identity.get('frame_index')
        current = identity.get('roll_id')
        if not current or type(index) is not int or not 1 <= index <= capacity or index in frames:
            raise ValueError('Missing, duplicate or out-of-range frame identity')
        if roll_id and current != roll_id: raise ValueError('Select files from one roll only')
        roll_id = current; frames[index] = {'frame_index':index, 'state':'latent', 'file':path.name}
        del state
    return check_manifest({'kind':ROLL_KIND,'version':1,'roll_id':roll_id,'capacity':capacity,'lifecycle':'sealed',
        'label':roll_id, 'frames':[frames.get(i, {'frame_index':i,'state':'empty'}) for i in range(1,capacity+1)]})


def validate_latents(manifest, files):
    snapshot = None
    for frame in manifest['frames']:
        if frame['state'] == 'empty': continue
        path = files[frame['file']]
        state, metadata = load_exposed_latent(path)
        identity = metadata.get('frame_identity') or {}
        if identity.get('roll_id') != manifest['roll_id'] or identity.get('frame_index') != frame['frame_index']:
            raise ValueError(f'Latent identity disagrees with roll order: {path.name}')
        if state.material_snapshot is None: raise ValueError('A frozen material snapshot is required')
        current = state.material_snapshot.as_dict()
        if snapshot is not None and snapshot != current: raise ValueError('Roll contains different material definitions')
        snapshot = current
        # This also rejects material that has already been partially developed.
        from film_foundry.core.latent_io import unexposed_latent_like
        unexposed_latent_like(state, validate_only=True)
        del state
    return snapshot


def import_roll(source, storage, *, paths=None, capacity=None, confirmed_complete=False):
    """Import into an owned directory; publishing occurs only after validation."""
    storage = Path(storage); storage.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    with TemporaryDirectory(prefix='.import-', dir=storage) as scratch:
        scratch = Path(scratch)
        if paths is not None:
            paths = list(map(Path, paths))
            manifest = legacy_manifest(paths, capacity, confirmed_complete=confirmed_complete)
            files = {p.name:p for p in paths}
        elif source.suffix.lower() == '.ffroll':
            with zipfile.ZipFile(source) as archive:
                members = archive.infolist()
                names = [m.filename for m in members]
                if len(names) != len(set(names)) or 'roll.json' not in names:
                    raise ValueError('Invalid electronic roll members')
                info = archive.getinfo('roll.json')
                if info.file_size > 1024*1024: raise ValueError('Roll manifest is too large')
                from film_foundry.core.atomic_io import strict_json_loads
                manifest = check_manifest(strict_json_loads(archive.read(info).decode('utf-8')))
                expected = {'roll.json'} | {f['file'] for f in manifest['frames'] if f['state']=='latent'}
                if set(names) != expected or sum(m.file_size for m in members) > 128*1024**3:
                    raise ValueError('Roll contains missing or unexpected members')
                files = {}
                for name in expected-{'roll.json'}:
                    target = scratch/name
                    with archive.open(name) as src, target.open('wb') as dst: shutil.copyfileobj(src,dst,1024*1024)
                    files[name] = target
        else:
            manifest = check_manifest(strict_json_load(source))
            files = {f['file']:source.parent/f['file'] for f in manifest['frames'] if f['state']=='latent'}
        material = validate_latents(manifest,files)
        # A second import resumes the owned roll instead of granting a new bath.
        for existing in storage.glob('*/session.json'):
            saved = strict_json_load(existing)
            if saved['roll']['roll_id'] == manifest['roll_id']:
                if saved['roll'] != manifest: raise ValueError('This roll identity already has a different manifest')
                return RollSession(existing)
        target = storage/str(uuid4())
        owned = scratch/'owned';owned.mkdir()
        for name,path in files.items(): shutil.copyfile(path,owned/name)
        atomic_write_json(owned/'roll.json',manifest)
        atomic_write_json(owned/'session.json',{'version':1,'roll':manifest,'material':material,'phase':'sealed',
            'config':None,'frames':{},'id':target.name})
        owned.rename(target)
    return RollSession(target/'session.json')


class RollSession:
    def __init__(self,path):
        self.path=Path(path);self.data=strict_json_load(self.path)
        if self.data.get('version') != 1: raise ValueError('Unsupported darkroom session')
        check_manifest(self.data['roll'])

    def save(self): atomic_write_json(self.path,self.data)

    def process_config(self,config):
        """The imported material, not the photo workspace, owns native topology."""
        from film_foundry.contracts.material_snapshot import MaterialSnapshot
        from film_foundry.core.media import is_monochrome_material
        result=deepcopy(config)
        result.film=DarkroomConfig.from_dict({'film':MaterialSnapshot.from_value(self.data['material']).parameters()}).film
        result.mode='bw_negative' if is_monochrome_material(result) else 'color_negative'
        return result

    def export(self,target):
        from film_foundry.core.atomic_io import atomic_output_path
        target=Path(target)
        with atomic_output_path(target) as temporary:
            # NPZ already compresses pools. Store it without a second deflate pass.
            with zipfile.ZipFile(temporary,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                archive.writestr('roll.json',json.dumps(self.data['roll'],ensure_ascii=False))
                for frame in self.data['roll']['frames']:
                    if frame['state']=='latent':archive.write(self.path.parent/frame['file'],frame['file'])

    def develop(self,config,output,*,control=None,on_result=None):
        if self.data['phase'] not in ('sealed','developing'): raise ValueError('This roll is already developed; scan its final media')
        if self.data['config'] is None:
            from film_foundry.app.darkroom_process import condition_driven_config
            frozen=prepare_incident_config(condition_driven_config(self.process_config(config)),roll_id=self.data['roll']['roll_id'])
            if frozen.seed_strategy=='random':
                frozen.random_seed=uuid4().int % (2**31);frozen.seed_strategy='path'
            self.data['config']=json.loads(json.dumps(asdict(frozen)));self.data['output']=str(Path(output).resolve()/self.data['id'])
            self.data['process_policy']='conditions_v1'
            self.data['phase']='developing';self.save()
        config=DarkroomConfig.from_dict(self.data['config'])
        root=Path(self.data['output']);root.mkdir(parents=True,exist_ok=True)
        template=next(f for f in self.data['roll']['frames'] if f['state']=='latent')
        for frame in self.data['roll']['frames']:
            if control:control.check_cancel()
            key=str(frame['frame_index'])
            receipt=root/f'frame-{int(key):03d}'/'frame-result.json'
            if key not in self.data['frames'] and receipt.is_file():
                restored=strict_json_load(receipt)
                if restored.get('key')!=f"roll:{self.data['roll']['roll_id']}:{key}":raise ValueError('Frame receipt identity mismatch')
                self.data['frames'][key]=restored;self.save()
            if key in self.data['frames']:
                record=self.data['frames'][key]
                if not Path(record['medium']).is_file():raise ValueError('A developed frame is missing; restore it instead of redeveloping the roll')
                if on_result:on_result(record)
                continue
            identity={'roll_id':self.data['roll']['roll_id'],'frame_index':frame['frame_index']}
            source=self.path.parent/(frame['file'] if frame['state']=='latent' else f'blank-{key}.darkroom_latent.npz')
            if frame['state']=='empty':
                from film_foundry.core.latent_io import unexposed_latent_like
                state,metadata=load_exposed_latent(self.path.parent/template['file'])
                blank=unexposed_latent_like(state);del state
                save_exposed_latent(source,blank,identity={'frame_identity':identity,'unexposed_slot':True})
                del blank
            result=run_request(WorkflowRequest('develop_latent',source,root/f'frame-{int(key):03d}',config,frame_identity=identity,control=control))
            paths={k:str(v) for k,v in result.extra['paths'].items()}
            record={'source':str(source),'medium':str(result.medium_path),'artifacts':paths,
                'key':f"roll:{identity['roll_id']}:{key}",'label':f"{self.data['roll'].get('label') or identity['roll_id']} · {key}/{self.data['roll']['capacity']}",
                'unexposed':frame['state']=='empty','roll_session':str(self.path)}
            atomic_write_json(receipt,record)
            self.data['frames'][key]=record;self.save()
            if on_result:on_result(deepcopy(record))
            del result
        self.data['phase']='developed';self.save()

    def scan(self,config,*,control=None,on_result=None,output=None):
        if self.data['phase'] not in ('developed','scanned'):raise ValueError('Develop the entire roll before scanning')
        from film_foundry.app.workspace_jobs import scan_record
        root=None
        if output is not None:
            from film_foundry.app.workflow import create_run_directory
            root,_=create_run_directory(Path(output),'roll_scan')
        for frame in self.data['roll']['frames']:
            record=self.data['frames'][str(frame['frame_index'])]
            scan_record(record,config,control=control,output=None if root is None else root/f"{frame['frame_index']:04d}")
            self.save()
            if on_result:on_result(deepcopy(record))
        self.data['phase']='scanned';self.save()
