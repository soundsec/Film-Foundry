"""Small, typed catalogue descriptors. Never decode image pools or change media."""
from copy import deepcopy
from pathlib import Path
import os
import json

from film_foundry.core.io_utils import SUPPORTED_EXTENSIONS


def path_key(path):
    return os.path.normcase(str(Path(path).resolve())) if path else ''


def family_key(name):
    from film_foundry.app.material_lab_session import FAMILIES
    return next((key for key,(module,_) in FAMILIES.items() if name in (key,module.FAMILY)),name)


def inspect_archive(path):
    """Read only the archive's JSON descriptor, not its pixel arrays."""
    import numpy as np
    with np.load(path,allow_pickle=False) as archive:
        keys=set(archive.files)
        if 'manifest' in keys:
            manifest=json.loads(str(archive['manifest']))
            if manifest.get('kind')=='products':
                bodies=manifest.get('bodies',[])
                if not bodies:raise ValueError('Material archive has no bodies')
                return {'kind':'material','family':family_key(bodies[0]['family']),
                        'group_id':manifest['group_id'],'bodies':bodies}
            if manifest.get('kind')=='state':
                return {'kind':'state','family':family_key(manifest['family']),'group_id':manifest['group_id']}
        if 'latent_medium_metadata' in keys:
            metadata=json.loads(str(archive['latent_medium_metadata']))
            return {'kind':'latent','family':'film','frame_identity':metadata.get('frame_identity')}
        if 'optical_density_rgb' in keys or 'density_grain' in keys:
            metadata=json.loads(str(archive['developed_medium_metadata'])) if 'developed_medium_metadata' in keys else {}
            return {'kind':'medium','family':'film','polarity':metadata.get('image_polarity','')}
        # Checkpoint inspection also reads descriptors only and validates its model.
    from film_foundry.app.film_process_work import describe_work
    describe_work(path)
    return {'kind':'state','family':'film'}


def asset_descriptor(path,role,*,family='',body=None,cover='',descriptor=None):
    path=str(Path(path).resolve());suffix=Path(path).suffix.lower()
    data={'id':'asset:'+path_key(path),'path':path,'role':role,'family':family,
          'kind':'document','preview':cover,'body_id':None,'usable_negative':False}
    if descriptor:data.update(descriptor)
    if path.lower().endswith(('.scanner_raw.tiff','.light_table_raw.tiff')):data.update(kind='scanner_raw',family='film')
    elif role=='source' and suffix in SUPPORTED_EXTENSIONS:data.update(kind='source',family='')
    elif suffix in SUPPORTED_EXTENSIONS:
        data['kind']='scan' if role.startswith(('scan', 'output', 'positive','export:', 'saved:png')) or role.endswith(':image') or role in ('cover','medium') else 'preview'
        data['preview']=path
    elif path.lower().endswith('.darkroom_latent.npz'):data.update(kind='latent',family='film')
    elif path.lower().endswith('.ffprocess.npz') or role=='process_state':data['kind']='state'
    elif suffix=='.npz' and role!='working_exposure':
        data['kind']=(descriptor or {}).get('kind','medium' if family=='film' else 'material')
    if body:
        data.update(body_id=body['body_id'],usable_negative=body.get('usable_negative',False))
        data['kind']='auxiliary' if body['role'] in ('donor_residue','silver_negative') else 'material'
        data['role']=body['role']
    tags={data['kind']}
    if data['kind']=='medium':tags.add('film')
    if data['usable_negative'] or data.get('polarity')=='negative':tags.add('negative')
    if data.get('polarity')=='positive':tags.add('positive')
    data['types']=sorted(tags)
    if suffix in SUPPORTED_EXTENSIONS:data['preview']=path
    stat=Path(path).stat() if Path(path).is_file() else None
    data.update(time=stat.st_mtime if stat else 0,bytes=stat.st_size if stat else 0)
    return data


def record_assets(record,*,inspect=False):
    family=record.get('family','film');cover=record.get('cover') or record.get('artifacts',{}).get('cover','')
    bodies={b['role']:b for b in record.get('bodies',[])}
    paths={'source':record.get('source',''),'medium':record['medium'],**record.get('artifacts',{})}
    output=[];seen=set()
    for role,path in paths.items():
        if not path or path_key(path) in seen:continue
        seen.add(path_key(path));descriptor=None
        if inspect and path_key(path)==path_key(record['medium']) and Path(path).suffix.lower()=='.npz' and Path(path).is_file():
            descriptor=inspect_archive(path)
            family=descriptor.get('family',family)
        preview=record.get('artifacts',{}).get(role+':image',cover)
        if role=='source':preview=path if Path(path).suffix.lower() in SUPPORTED_EXTENSIONS else ''
        data=asset_descriptor(path,role,family=family,body=bodies.get(role),cover=preview,descriptor=descriptor)
        # A final archive containing one body preserves its exact body identity.
        if role=='medium' and descriptor and len(descriptor.get('bodies',[]))==1:
            body=descriptor['bodies'][0]
            data=asset_descriptor(path,body['role'],family=family,body=body,cover=preview,descriptor=descriptor)
        if role=='medium' and (len((descriptor or {}).get('bodies',[]))>1 or any(b in record.get('artifacts',{}) for b in bodies)):
            data.update(kind='group',types=['group'],body_id=None)
        output.append(data)
        if role=='medium' and descriptor and len(descriptor.get('bodies',[]))>1:
            for body in descriptor['bodies']:
                if body['role'] in record.get('artifacts',{}):continue
                child=asset_descriptor(path,body['role'],family=family,body=body,cover=cover)
                child['id']+=':'+body['body_id'];output.append(child)
    for asset in output:
        asset['process_path']=record.get('artifacts',{}).get('process_state','')
    return output


def import_record(path):
    """Import an explicit bundle/member or standalone state; never scan a directory tree."""
    from film_foundry.app.material_result_store import result_record
    from film_foundry.core.atomic_io import strict_json_load
    path=Path(path).resolve()
    record=result_record(path)
    if record is not None:return record
    manifest=path/'result.json' if path.is_dir() else path if path.name.endswith('result.json') else path.with_name(path.name+'.result.json')
    if not manifest.is_file():manifest=path.parent/'result.json'
    if manifest.is_file():
        candidate=strict_json_load(manifest)
        if isinstance(candidate,dict) and isinstance(candidate.get('medium'),str) and isinstance(candidate.get('artifacts'),dict):
            # A per-output scan record may refer to a medium in another directory.
            candidate=deepcopy(candidate)
            old_root=Path(candidate['artifacts'].get('result',candidate['medium'])).parent
            for key,value in list(candidate['artifacts'].items()):
                if Path(value).parent==old_root:candidate['artifacts'][key]=str(manifest.parent/Path(value).name)
            if Path(candidate['medium']).parent==old_root:
                candidate['medium']=str(manifest.parent/Path(candidate['medium']).name)
            cover=candidate.get('cover','')
            if cover and Path(cover).parent==old_root:candidate['cover']=str(manifest.parent/Path(cover).name)
            members={path_key(candidate['medium']),*(path_key(p) for p in candidate['artifacts'].values())}
            if path.is_dir() or path==manifest or path_key(path) in members:return candidate
    if path.suffix.lower()!='.npz':raise ValueError('Choose a result.json or a material NPZ archive')
    descriptor=inspect_archive(path)
    return {'key':'archive:'+path_key(path),'source':'','label':path.name,'medium':str(path),
            'family':descriptor['family'],'group_id':descriptor.get('group_id'),
            'bodies':descriptor.get('bodies',[]),'artifacts':{}}


def asset_actions(asset):
    """Capabilities are about the selected object, never its displayed cover."""
    kind=asset['kind'];family=asset.get('family','')
    if kind in ('source','scan','preview'):return ('image',)
    if kind=='scanner_raw':return ('scan','image')
    if kind=='latent':return ('develop',)
    if kind=='state':return ('continue',)
    if kind=='medium' and family=='film':return ('scan','reexpose')
    if kind in ('material','auxiliary','group'):
        return ('observe','reexpose')
    return ()
