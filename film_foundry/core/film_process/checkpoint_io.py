"""Lossless pool snapshots for reaction continuation, distinct from latent I/O."""
from dataclasses import fields
import json
from pathlib import Path
import numpy as np

from film_foundry.core.atomic_io import atomic_savez, strict_json_loads
from film_foundry.core.film_process.model import FilmProcessState
from film_foundry.core.material_geometry import MaterialGeometry
from film_foundry.contracts.material_snapshot import MaterialSnapshot
from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile

KIND = 'film_process_work_v1'
KIND_V2 = 'film_process_work_v2'
KIND_V3 = 'film_process_work_v3'
KIND_V4 = 'film_process_work_v4'
KIND_V5 = 'film_process_work_v5'
SUFFIX = '.ffprocess.npz'
JSON_KEY = 'film_process_work_metadata'
PROFILES = {'material_geometry': MaterialGeometry.from_dict,
    'material_snapshot': MaterialSnapshot.from_value,
    'spectral_exposure_profile': SpectralExposureProfile.from_value,
    'spectral_material_profile': SpectralMaterialProfile.from_value}
ARRAY_FIELDS = {'halide', 'developability', 'metallic_silver', 'coupler', 'dye',
    'bleached_halide', 'original_developability', 'emulsion_coverage'}


def pack_state(state, prefix, arrays):
    state.validate()
    record = {}
    for descriptor in fields(state):
        name = descriptor.name; value = getattr(state, name)
        if name in ARRAY_FIELDS and value is not None:
            key = prefix + name
            if value.dtype != np.float32:
                raise ValueError('Reaction checkpoint pools must be float32')
            arrays[key] = value; record[name] = key
        elif name == 'formation_history' and value is not None:
            record[name] = {}
            for field, array in value.items():
                key = prefix + name + '_' + field
                arrays[key] = array; record[name][field] = key
        elif name in PROFILES:
            record[name] = value.as_dict() if value is not None else None
        else:
            record[name] = value
    return record


def unpack_state(archive, record, prefix):
    record = dict(record)
    record.setdefault('film_border_snapshot', None)  # pre-rebate archives
    if set(record) != {f.name for f in fields(FilmProcessState)}:
        raise ValueError('Incomplete or unsupported reaction checkpoint state')
    values = dict(record)
    for name, value in record.items():
        if name in ARRAY_FIELDS and value is not None:
            if value != prefix + name:
                raise ValueError('Invalid checkpoint pool reference')
            values[name] = archive[value]
            if values[name].dtype != np.float32:
                raise ValueError('Reaction checkpoint pools must be float32')
        elif name == 'formation_history' and value is not None:
            from film_foundry.core.film_process.formation_history import NAMES, POOLS
            allowed = {p + '_' + n for p in POOLS for n in NAMES}
            if not set(value) <= allowed:
                raise ValueError('Unsupported formation history fields')
            for field, key in value.items():
                if key != prefix + name + '_' + field:
                    raise ValueError('Invalid formation history reference')
            values[name] = {field: archive[key] for field, key in value.items()}
            if any(a.dtype != np.float32 for a in values[name].values()):
                raise ValueError('Formation history must be float32')
        elif name in PROFILES and value is not None:
            values[name] = PROFILES[name](value)
    state = FilmProcessState(**values); state.validate()
    original = state.original_developability
    if original is not None and (original.shape != state.halide.shape or not np.isfinite(original).all()
                                 or original.min() < 0 or original.max() > 1):
        raise ValueError('Invalid original activation in reaction checkpoint')
    return state


def save_checkpoint(path, payload, latent, state, *, surface=None, inhibitor=None, preparation=None):
    path = Path(path)
    if not str(path).lower().endswith(SUFFIX):
        raise ValueError(f'Reaction checkpoint output requires {SUFFIX}')
    arrays = {}
    metadata = dict(payload)
    surface = surface if surface is not None else metadata.pop('surface_state',None)
    inhibitor = inhibitor if inhibitor is not None else metadata.pop('inhibitor_state',None)
    preparation = preparation if preparation is not None else metadata.pop('spatial_preparation',None)
    if inhibitor is not None and surface is None:raise ValueError('Wet inventory requires the surface checkpoint')
    if preparation is not None and inhibitor is None:raise ValueError('Prepared continuation requires the wet inventories')
    wet_history=surface is not None and surface.get('model')=='film_surface_boundary_v2'
    if wet_history and preparation is None:raise ValueError('Wet history continuation requires spatial preparation')
    metadata.update(kind=KIND_V5 if wet_history else KIND_V4 if preparation is not None else KIND_V3 if inhibitor is not None else KIND_V2 if surface is not None else KIND,
        latent=pack_state(latent, 'latent_', arrays),
        state=pack_state(state, 'current_', arrays))
    if surface is not None:
        packed=dict(surface)
        if surface['active']:
            packed['fields']={name:'surface_'+name for name in surface['fields']}
            arrays.update({'surface_'+name:array for name,array in surface['fields'].items()})
        if 'hardening' in surface and np.ndim(surface['hardening']):
            arrays['surface_hardening']=surface['hardening'];packed['hardening']='surface_hardening'
        metadata['surface_state']=packed
    if inhibitor is not None:
        packed=dict(inhibitor)
        if inhibitor['active']:
            for name in ('load','hardening','rate'):
                value=inhibitor[name]
                if value is not None and np.ndim(value):
                    key='inhibitor_'+name;arrays[key]=value;packed[name]=key
        metadata['inhibitor_state']=packed
    if preparation is not None:
        from film_foundry.core.film_process.preparation import pack_preparation
        metadata['spatial_preparation']=pack_preparation(preparation,arrays)
    arrays[JSON_KEY] = np.asarray(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
    from film_foundry.core.material_parallel import thread_count
    atomic_savez(path, compression_level=1, parallel_compression=thread_count() > 1, durable=True, **arrays)


def _header(archive):
    if JSON_KEY not in archive:
        raise ValueError('Choose a reaction checkpoint, not a latent or final medium')
    raw = archive[JSON_KEY]
    if raw.shape != () or raw.dtype.kind != 'U' or raw.nbytes > 16 * 1024 * 1024:
        raise ValueError('Invalid reaction checkpoint metadata')
    metadata = strict_json_loads(str(raw))
    if not isinstance(metadata, dict) or metadata.get('kind') not in (KIND,KIND_V2,KIND_V3,KIND_V4,KIND_V5):
        raise ValueError('Unsupported reaction checkpoint version')
    if metadata['kind'] in (KIND_V2,KIND_V3,KIND_V4,KIND_V5) and not isinstance(metadata.get('surface_state'),dict):
        raise ValueError('Missing surface checkpoint inventory')
    if metadata['kind'] in (KIND_V3,KIND_V4,KIND_V5) and not isinstance(metadata.get('inhibitor_state'),dict):
        raise ValueError('Missing inhibitor checkpoint inventory')
    if metadata['kind'] in (KIND_V4,KIND_V5) and not isinstance(metadata.get('spatial_preparation'),dict):
        raise ValueError('Missing spatial preparation in checkpoint')
    if metadata['kind']==KIND_V5 and metadata['surface_state'].get('model')!='film_surface_boundary_v2':
        raise ValueError('Missing continuous wet history in checkpoint')
    return metadata


def inspect_checkpoint(path):
    """Small UI/CLI description; execution still loads and checks real pools."""
    with np.load(path, allow_pickle=False) as archive:
        return _header(archive)


def load_checkpoint(path):
    with np.load(path, allow_pickle=False) as archive:
        metadata = _header(archive)
        # Historical checkpoints formed their wet-contact history in FP64.
        metadata['config'].setdefault('processing',{}).setdefault('contact_precision','float64')
        latent = unpack_state(archive, metadata.pop('latent'), 'latent_')
        state = unpack_state(archive, metadata.pop('state'), 'current_')
        if metadata['kind'] in (KIND_V2,KIND_V3,KIND_V4,KIND_V5):
            surface=metadata['surface_state']
            if surface.get('active'):
                refs=surface['fields']
                if any(key!='surface_'+name for name,key in refs.items()):raise ValueError('Invalid surface pool reference')
                surface['fields']={name:archive[key] for name,key in refs.items()}
            if surface.get('hardening')=='surface_hardening':surface['hardening']=archive['surface_hardening']
        if metadata['kind'] in (KIND_V3,KIND_V4,KIND_V5):
            inhibitor=metadata['inhibitor_state']
            if inhibitor.get('active'):
                for name in ('load','hardening','rate'):
                    if isinstance(inhibitor[name],str):
                        if inhibitor[name]!='inhibitor_'+name:raise ValueError('Invalid inhibitor field reference')
                        inhibitor[name]=archive[inhibitor[name]]
        if metadata['kind'] in (KIND_V4,KIND_V5):
            from film_foundry.core.film_process.preparation import unpack_preparation
            metadata['spatial_preparation']=unpack_preparation(metadata['spatial_preparation'],archive)
    if latent.halide.shape != state.halide.shape:
        raise ValueError('Reaction checkpoint source and state dimensions differ')
    return metadata, latent, state
