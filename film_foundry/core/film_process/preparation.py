"""Exact reusable spatial inputs for a frozen film reaction program.

These are derived fields, separate from the mutable chemical inventories.
Archive ownership binds them to the latent in the same checkpoint; no image
hash, external cache, dynamic class lookup or platform resource is involved.
"""
from copy import deepcopy
from dataclasses import asdict
import json
import numpy as np
from film_foundry.core.spatial_fields import (
    GlobalFieldGrid,GlobalRateMultiplierField,GlobalLayerRateMultiplierField)

MODEL='film_spatial_preparation_v1'
ARRAY_NAMES={'uneven','adjacency','dir_0','dir_1','dir_2'}


def _json(value):
    return json.loads(json.dumps(value,allow_nan=False))


def binding_for(config,shape,program_kind):
    # Each checkpoint freezes the full effective/projected configuration.
    # Explicit bath revisions check phase dependencies before rebinding it.
    from film_foundry.model.config import DarkroomConfig
    normalized=DarkroomConfig.from_dict(asdict(config))
    return _json(dict(config=asdict(normalized),shape=list(shape[:2]),program_kind=program_kind))


def _rng_state(rng):
    value=rng.bit_generator.state
    if value.get('bit_generator')!='PCG64':raise ValueError('Prepared film continuation requires PCG64')
    return deepcopy(value)


class SpatialPreparation:
    def __init__(self,record=None,*,reuse=True):
        self.record=record;self.reuse=reuse;self.reused=False;self._binding=None;self._before=None
        # Explicit future-bath revisions may refresh deterministic fields for
        # an unentered target. Normal continuation always reuses the whole set.
        self.rebuild_fields=frozenset()

    def lookup(self,config,shape,program_kind,rng):
        self._binding=binding_for(config,shape,program_kind);self._before=_rng_state(rng)
        if self.record is None:return None
        record=self.record
        if record.get('model')!=MODEL or record.get('binding')!=self._binding:
            raise ValueError('Spatial preparation differs from the frozen configuration or frame')
        if record.get('rng_before')!=self._before:
            raise ValueError('Spatial preparation random state differs from the frozen run')
        if not self.reuse:return None
        arrays=record['arrays'];descriptions=record['fields'];used=set()
        if set(descriptions)!={'uneven','adjacency','dir'} or not set(arrays)<=ARRAY_NAMES:
            raise ValueError('Unsupported prepared spatial fields')
        full=tuple(shape[:2])
        def scalar(name,description):
            if description is None:return None
            if name not in arrays:raise ValueError('Missing prepared spatial field')
            values=arrays[name];used.add(name)
            if not isinstance(values,np.ndarray) or values.dtype!=np.float32:
                raise ValueError('Prepared spatial fields must be float32 arrays')
            if description.get('kind')=='array':
                if name!='uneven' or values.shape!=full or not np.isfinite(values).all() or np.min(values)<0:
                    raise ValueError('Invalid full-frame prepared uneven field')
                values.setflags(write=False);return values
            if description.get('kind')!='grid':raise ValueError('Unsupported prepared field representation')
            grid=GlobalFieldGrid(full_shape=tuple(description['full_shape']),
                work_shape=tuple(description['work_shape']),pixel_center_convention=description['pixel_center_convention'])
            if grid.full_shape!=full:raise ValueError('Prepared grid differs from its frame')
            return GlobalRateMultiplierField(values,grid)
        uneven=scalar('uneven',descriptions['uneven'])
        adjacency=scalar('adjacency',descriptions['adjacency'])
        dir_description=descriptions['dir'];dir_field=None
        if dir_description is not None:
            if not isinstance(dir_description,list) or len(dir_description)!=3:
                raise ValueError('Prepared DIR field requires three layers')
            dir_field=GlobalLayerRateMultiplierField(tuple(scalar(f'dir_{i}',d) for i,d in enumerate(dir_description)))
        if used!=set(arrays):raise ValueError('Unexpected prepared spatial arrays')
        audits=deepcopy(record['audits'])
        if set(audits)!={'adjacency','dir'} or not all(isinstance(v,dict) for v in audits.values()):
            raise ValueError('Invalid spatial preparation audits')
        after=record['rng_after']
        if not isinstance(after,dict) or after.get('bit_generator')!='PCG64':raise ValueError('Invalid prepared random generator')
        # Validate in a private generator before changing the caller's stream.
        probe=np.random.PCG64();probe.state=deepcopy(after)
        rng.bit_generator.state=deepcopy(after);self.reused=True
        return uneven,adjacency,audits['adjacency'],dir_field,audits['dir']

    def capture(self,rng,uneven,adjacency,adjacency_audit,dir_field,dir_audit):
        if self._binding is None:raise ValueError('Spatial preparation was not bound before capture')
        arrays={}
        def scalar(name,field):
            if field is None:return None
            if isinstance(field,np.ndarray):
                if name!='uneven' or field.dtype!=np.float32:raise ValueError('Unsupported prepared array')
                field.setflags(write=False);arrays[name]=field
                return {'kind':'array'}
            if not isinstance(field,GlobalRateMultiplierField):raise ValueError('Unsupported prepared grid field')
            arrays[name]=field.work_field
            return {'kind':'grid',**asdict(field.grid)}
        fields={'uneven':scalar('uneven',uneven),'adjacency':scalar('adjacency',adjacency),'dir':None}
        if dir_field is not None:
            if not isinstance(dir_field,GlobalLayerRateMultiplierField):raise ValueError('Unsupported prepared DIR field')
            fields['dir']=[scalar(f'dir_{i}',f) for i,f in enumerate(dir_field.layer_fields)]
        self.record=dict(model=MODEL,binding=self._binding,rng_before=self._before,rng_after=_rng_state(rng),
            fields=_json(fields),arrays=arrays,audits=_json({'adjacency':adjacency_audit,'dir':dir_audit}))

    def snapshot(self):
        if self.record is None:raise ValueError('Spatial preparation has not completed')
        return self.record


def pack_preparation(record,arrays):
    if not set(record['arrays'])<=ARRAY_NAMES:raise ValueError('Unsupported prepared spatial arrays')
    packed=dict(record,arrays={name:'prepared_'+name for name in record['arrays']})
    arrays.update({'prepared_'+name:value for name,value in record['arrays'].items()})
    return packed


def unpack_preparation(record,archive):
    refs=record['arrays']
    if not set(refs)<=ARRAY_NAMES or any(key!='prepared_'+name for name,key in refs.items()):
        raise ValueError('Invalid prepared spatial array reference')
    binding=record['binding']
    if 'rotation_quarters' not in binding['config'].get('output',{}):
        binding=deepcopy(binding)
        binding['config'].setdefault('output',{})['rotation_quarters']=0
    if 'contact_precision' not in binding['config'].get('processing',{}):
        binding=deepcopy(binding)
        binding['config'].setdefault('processing',{})['contact_precision']='float64'
    if 'reduced_profile' not in binding['config'].get('processing',{}):
        # Add only the versioned default selected for this historical recipe;
        # never reinterpret old reduced checkpoints as the new contact model.
        from film_foundry.model.config import DarkroomConfig
        binding=deepcopy(binding)
        profile=DarkroomConfig.from_dict(binding['config']).processing.reduced_profile
        binding['config'].setdefault('processing',{})['reduced_profile']=profile
    return dict(record,binding=binding,arrays={name:archive[key] for name,key in refs.items()})
