"""Small shared mechanics for independently versioned sheet materials.

Pools use normalized equivalents, not measured chemical concentrations. These
helpers never turn an observed image into a material state.
"""
from film_foundry.core.material_structure import from_record
from dataclasses import dataclass, asdict, replace
from types import MappingProxyType
import json
import math
import numpy as np
from film_foundry.core import material_defects as defects
from film_foundry.core.material_family import MediumBody, ProductGroup, ViewingConditions, frame_observation
from film_foundry.core.material_arrays import freeze_array
from film_foundry.core.material_metadata import ProcessAnnotations
from film_foundry.core.material_family import cached_body_validation


def number(value, name, low=0., high=1.):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{name} must be finite in [{low}, {high}]')
    return float(value)


def image_rgb(image):
    image=np.asarray(image,dtype=np.float32)
    if image.ndim!=3 or image.shape[-1]!=3 or min(image.shape[:2])<=0 or not np.isfinite(np.min(image)) or not np.isfinite(np.max(image)) or np.min(image)<0:
        raise ValueError('Exposure requires finite nonnegative HxWx3 linear RGB')
    return image


def validate_fields(fields,spec,limits=None):
    if not isinstance(fields,(dict,MappingProxyType)) or set(fields)!=set(spec):raise ValueError('Unexpected sheet fields')
    result={};shape=None
    for key,channels in spec.items():
        # Validate the detached snapshot, not a caller's still-mutable buffer.
        a=freeze_array(fields[key])
        if a.ndim!=(2 if channels==0 else 3) or (channels and a.shape[-1]!=channels) or min(a.shape[:2])<=0:
            raise ValueError(f'Invalid shape for {key}')
        if shape is not None and a.shape[:2]!=shape:raise ValueError('Sheet fields must share geometry')
        shape=a.shape[:2]
        low=float(np.min(a));high=float(np.max(a))
        if not math.isfinite(low) or not math.isfinite(high) or low<0:raise ValueError(f'Invalid pool {key}')
        if key not in ('dose','residue'):
            limit=2. if key in ('ferric','ferrous','spent_ferric','iron_removed') else 1.
            limit=(limits or {}).get(key,limit)
            if limit is not None and high>limit+1e-5:raise ValueError(f'Out-of-range pool {key}')
        # A caller's read-only view may still have a writable base. Only storage
        # with immutable backing is shared across states and final bodies.
        result[key]=a
    return MappingProxyType(result)


@dataclass(frozen=True)
class SheetState(ProcessAnnotations):
    group_id: str
    seed: int
    material: object
    stage: str
    elapsed_seconds: float
    fields: object
    process_json: str='{}'
    schema_version: int=1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in getattr(self,'FIELD_VERSIONS',{1:self.FIELDS}) or self.stage not in self.STAGES or not isinstance(self.group_id,str) or not self.group_id:raise ValueError('Invalid sheet version/stage/identity')
        if type(self.seed) is not int or not 0<=self.seed<=4294967295:raise ValueError('Invalid sheet seed')
        if not isinstance(self.material,self.MATERIAL):raise ValueError('Wrong material definition for this family')
        number(self.elapsed_seconds,'elapsed_seconds',0,1e12)
        snapshot=json.loads(self.process_json)
        if not isinstance(snapshot,dict):raise ValueError('Invalid process snapshot')
        json.dumps(snapshot,allow_nan=False)
        events=snapshot.get('events',[])
        if not isinstance(events,list) or any(not isinstance(event,dict) or not isinstance(event.get('operation'),str) for event in events):raise ValueError('Invalid process events')
        from film_foundry.contracts.optical_cascade import CASCADE_KEY,validate_cascade
        if CASCADE_KEY in snapshot:validate_cascade(snapshot[CASCADE_KEY])
        object.__setattr__(self,'fields',validate_fields(self.fields,getattr(self,'FIELD_VERSIONS',{}).get(self.schema_version,self.FIELDS),getattr(self,'FIELD_LIMITS',None)))


def change(state,operation,fields=None,stage=None,seconds=0.,**parameters):
    return replace(state,fields=fields if fields is not None else state.fields,stage=stage or state.stage,
        elapsed_seconds=state.elapsed_seconds+seconds,
        process_json=defects.record_event(state.process_json,operation,seconds=seconds,**parameters))


def require(state,*stages):
    if state.stage not in stages:raise ValueError(f'Operation is unavailable at {state.stage}; expected {stages}')


def rate(seconds,tau,activity=1.,temperature_c=20.,reference_c=20.):
    number(seconds,'seconds',0,86400);number(activity,'activity',0,100);number(temperature_c,'temperature_c',0,60)
    return -math.expm1(-seconds/tau*activity*2**((temperature_c-reference_c)/10))


def response(dose,half,contrast=1.):
    # Evaluate in log space; arbitrary high dose cannot overflow powers.
    return np.exp(-np.logaddexp(0.,-contrast*(np.log(np.maximum(dose,1e-30))-math.log(half)))).astype(np.float32)


def spatial_rate(seconds,tau,activity,access,temperature_c=20.):
    rate(seconds,tau,activity,temperature_c)  # same scalar validation
    return -np.expm1(-seconds/tau*activity*2**((temperature_c-20)/10)*access)


def coating(shape,seed,variation,edge_loss,texture):
    number(variation,'coating_variation');number(edge_loss,'edge_loss');number(texture,'texture')
    small=tuple(max(1,round(n*min(1,512/max(shape)))) for n in shape)
    cloud=defects.clouds(small,seed,913)
    import cv2
    rng=np.random.default_rng(np.random.SeedSequence([seed,914]))
    grid=rng.random((7,41),dtype=np.float32)
    marks=np.clip(cv2.resize(grid,(small[1],small[0]),interpolation=cv2.INTER_CUBIC),0,1)
    y,x=defects.coordinates(small);distance=np.minimum(np.minimum(x,1-x),np.minimum(y,1-y))
    coverage=(1-variation*(.6*cloud+.4*marks))*(1-edge_loss*np.exp(-distance/.035))
    paper=1-texture*.12*defects.clouds(small,seed,915,cells=29)
    if small!=shape:
        import cv2
        coverage=cv2.resize(coverage,(shape[1],shape[0]),interpolation=cv2.INTER_LINEAR)
        paper=cv2.resize(paper,(shape[1],shape[0]),interpolation=cv2.INTER_LINEAR)
    return np.asarray(coverage,np.float32),np.asarray(paper,np.float32)


def handling(state,*,fingerprints=0.,reagent_residue=0.,position_x=.5,position_y=.5,angle_degrees=0.):
    fields=dict(state.fields)
    fields['residue']=fields['residue']+defects.handling_density(fields['coating'].shape,state.seed,
        fingerprints=fingerprints,reagent_residue=reagent_residue,position_x=position_x,position_y=position_y,angle_degrees=angle_degrees)
    return change(state,'handling',fields,fingerprints=fingerprints,reagent_residue=reagent_residue,
                  position_x=position_x,position_y=position_y,angle_degrees=angle_degrees)


def finish(state,family,role,model,fields,*,negative=False):
    body=MediumBody(family,state.schema_version,state.group_id,state.group_id+':'+role,role,model,fields,usable_negative=negative,
        presentation_json=json.dumps({'frame':'none','material':asdict(state.material)}))
    provenance={'family':family,'version':state.schema_version,'material':asdict(state.material),'seed':state.seed,
                'process':json.loads(state.process_json),'process_seconds':state.elapsed_seconds,
                'group_id':state.group_id,'members':[body.body_id],'units':'normalized_material_equivalents'}
    return ProductGroup(state.group_id,(body,),json.dumps(provenance,allow_nan=False))


def validate_body(body,family,role,model,spec,material_type,negative=False,version=1,limits=None):
    if body.family!=family or body.schema_version!=version or body.role!=role or body.observation_model!=model or body.usable_negative!=negative:
        raise ValueError('Unexpected sheet body contract')
    validate_fields(body.fields,spec,limits)
    return from_record(material_type,json.loads(body.presentation_json)['material'])


def illuminate(image,body,view=ViewingConditions()):
    light=np.asarray(view.illuminant_rgb,np.float32)
    if light.shape!=(3,) or not np.isfinite(light).all() or np.min(light)<0:raise ValueError('Invalid illuminant')
    number(view.exposure_ev,'view exposure',-20,20)
    return frame_observation(body,np.asarray(image*light*(2**view.exposure_ev),np.float32),view)
