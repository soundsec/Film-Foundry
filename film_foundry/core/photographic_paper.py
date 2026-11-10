"""Silver-gelatin and chromogenic paper: latent -> silver/dye -> clearing.

Variable contrast mixes three blue-sensitive emulsions with different green
speeds. Direct-positive models exposure removal of pre-existing development
centres. Neither is a contrast/inversion filter on a finished print.
"""
from dataclasses import dataclass,field
import os
import numpy as np
from film_foundry.core import sheet_common as s, material_defects as defects
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core import paper_response, paper_support as support, material_structure as ms, scaled_structure as scaled
from film_foundry.core.material_arrays import freeze_array
from film_foundry.core.execution_topology import reference_execution_enabled

FAMILY='photographic_paper_v1'

@dataclass(frozen=True)
class PaperMaterial(support.PaperSupportMaterial):
    paper_base: str=field(default='rc',kw_only=True)
    surface_gloss: float=field(default=.35,kw_only=True)
    kind: str='variable_contrast'
    half_exposure: float=.18
    response_contrast: float=2.2
    maximum_density: float=2.2
    paper_reflectance: float=.94
    warmth: float=.05
    coating_variation: float=0.
    edge_loss: float=0.
    texture: float=.2
    fog_level: float=0.
    sensitivity_remaining: float=1.
    wash_seconds: float=60.

    def __post_init__(self):
        super().__post_init__()
        if self.kind not in ('graded','variable_contrast','direct_positive','color'):raise ValueError('Unknown paper emulsion')
        for key in ('half_exposure','maximum_density'):s.number(getattr(self,key),key,.001,20)
        s.number(self.response_contrast,'contrast',.2,8);s.number(self.paper_reflectance,'paper_reflectance',.01,1)
        s.number(self.wash_seconds,'wash_seconds',1,7200)
        for key in ('warmth','coating_variation','edge_loss','texture','fog_level','sensitivity_remaining'):s.number(getattr(self,key),key)

class PaperState(s.SheetState):
    MATERIAL=PaperMaterial
    STAGES=('exposed','developed','stopped','fixed','washed')
    FIELDS={'dose':3,'halide':3,'silver':3,'removed_silver':3,'dye':3,'coating':0,'paper':0,'residue':3}
    FIELD_VERSIONS={1:FIELDS,2:{**FIELDS,**support.FIELDS}}


def expose(image,material=PaperMaterial(),*,group_id,seed=0,schema_version=2):
    image=s.image_rgb(image);h,w=image.shape[:2]
    cover,paper=(scaled.paper_structure((h,w),seed,material) if schema_version>=2 and ms.enabled(material) else
                 s.coating((h,w),seed,material.coating_variation,material.edge_loss,material.texture))
    pools=np.repeat(cover[...,None],3,axis=2)
    return PaperState(group_id,seed,material,'exposed',0.,{'dose':image.copy(),'halide':pools,
        'silver':np.zeros_like(pools),'removed_silver':np.zeros_like(pools),'dye':np.zeros_like(pools),
        'coating':cover,'paper':paper*material.paper_reflectance,'residue':np.zeros_like(pools),
        **(support.initial_fields((h,w),material) if schema_version==2 else {})},schema_version=schema_version)


def latent_response(state):
    return paper_response.response(state.fields['dose'],state.material)


def _development_target(state):
    dose=state.fields['dose'];coating=state.fields['coating'];material=state.material
    backend=os.environ.get('FILM_FOUNDRY_COMPUTE','cpu').lower()
    cached=getattr(state,'_development_target_cache',None)
    if not reference_execution_enabled() and cached is not None:
        if cached[0] is dose and cached[1] is coating and cached[2] is material and cached[3]==backend:return cached
    target=freeze_array(coating[...,None]*latent_response(state))
    return (dose,coating,material,backend,target)


def light_leak(state,*,strength=.2,edge='left'):
    s.require(state,'exposed');s.number(strength,'strength',0,10)
    fields=dict(state.fields);fields['dose']=fields['dose']+strength*defects.edge_field(fields['coating'].shape,state.seed,edge)[...,None]
    return s.change(state,'light_leak',fields,strength=strength,edge=edge)


def develop(state,*,seconds=60.,temperature_c=20.,activity=1.,developer='material'):
    s.require(state,'exposed','developed')
    if developer not in ('material','bw','color'):raise ValueError('Unknown paper developer capability')
    m=state.material;fields=dict(state.fields)
    if state.schema_version==2:fields,_=support.wet_fields(state,seconds,temperature_c=temperature_c,integrated=False)
    cached=_development_target(state);target=cached[-1]
    amount=np.minimum(fields['halide'],np.maximum(target-fields['silver'],0))*s.rate(seconds,20.,activity,temperature_c)
    fields['halide']=fields['halide']-amount;fields['silver']=fields['silver']+amount
    if m.kind=='color' and developer in ('material','color'):fields['dye']=np.minimum(fields['coating'][...,None],fields['dye']+amount)
    fields['residue']=fields['residue']+amount.mean(axis=-1)[...,None]*np.array([.002,.004,.008],np.float32)
    result=s.change(state,'develop',fields,'developed',seconds,temperature_c=temperature_c,activity=activity,developer=developer)
    if not reference_execution_enabled():object.__setattr__(result,'_development_target_cache',cached)
    return result


def stop(state):
    s.require(state,'developed');return s.change(state,'stop',stage='stopped')


def fix(state,*,seconds=120.,activity=1.):
    s.require(state,'exposed','developed','stopped','fixed','washed');fields=dict(state.fields)
    if state.schema_version==2:fields,_=support.wet_fields(state,seconds,integrated=False)
    amount=fields['halide']*s.rate(seconds,20.,activity)
    fields['halide']=fields['halide']-amount;fields['removed_silver']=fields['removed_silver']+amount
    return s.change(state,'fix',fields,'fixed',seconds,activity=activity)


def bleach_fix(state,*,seconds=90.,bleach_activity=1.,fix_activity=1.):
    s.require(state,'developed','stopped','fixed','washed');s.number(seconds,'seconds',0,86400);fields=dict(state.fields)
    if state.schema_version==2:fields,_=support.wet_fields(state,seconds,integrated=False)
    # Same bath, sequentially coupled silver -> halide -> soluble removal.
    b=s.rate(seconds/8,15.,bleach_activity);f=s.rate(seconds/8,15.,fix_activity)
    for _ in range(8):
        silver=fields['silver']*b;fields['silver']=fields['silver']-silver;fields['halide']=fields['halide']+silver
        cleared=fields['halide']*f;fields['halide']=fields['halide']-cleared;fields['removed_silver']=fields['removed_silver']+cleared
    return s.change(state,'bleach_fix',fields,'fixed',seconds,bleach_activity=bleach_activity,fix_activity=fix_activity)


def wash(state,*,seconds=180.,water_changes=3.,region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.,temperature_c=20.):
    s.require(state,'fixed','washed');s.number(water_changes,'water_changes',0,100)
    local=dict(region=region,position_x=position_x,position_y=position_y,radius=radius,coverage=coverage,temperature_c=temperature_c)
    if state.schema_version==2:
        fields,dose=support.wet_fields(state,seconds,**local)
        tau=state.material.wash_seconds*(3. if state.material.paper_base=='fiber' else 1.)
        fields['residue']=fields['residue']*np.exp(-dose[...,None]*water_changes/(3*tau))
    else:
        support.legacy_parameters(**local)
        fields=dict(state.fields);fields['residue']=fields['residue']*(1-s.rate(seconds,state.material.wash_seconds,water_changes/3))
    return s.change(state,'wash',fields,'washed',seconds,water_changes=water_changes,**local)


dry=support.dry
BODY_FIELDS={key:value for key,value in PaperState.FIELDS.items() if key not in ('dose','removed_silver')}


def _body(state):
    keys={**BODY_FIELDS,**(support.FIELDS if state.schema_version==2 else {})}
    return s.finish(state,FAMILY,'paper_print',f'paper_reflection_v{state.schema_version}',{k:state.fields[k] for k in keys})


def products(state):
    s.require(state,'washed');return _body(state)


@s.cached_body_validation
def validate_body(body):
    if body.schema_version not in (1,2):raise ValueError('Unknown photographic-paper version')
    spec={**BODY_FIELDS,**(support.FIELDS if body.schema_version==2 else {})}
    return s.validate_body(body,FAMILY,'paper_print',f'paper_reflection_v{body.schema_version}',spec,PaperMaterial,version=body.schema_version)


def observe(body,view=ViewingConditions()):
    m=validate_body(body);f=body.fields
    silver=f['silver'].mean(axis=-1)[...,None]*m.maximum_density
    density=silver*np.array([1.,1.+.12*m.warmth,1.+.35*m.warmth],np.float32)+f['halide'].mean(axis=-1)[...,None]*.08+f['residue']
    if m.kind=='color':density=density+f['dye']@np.array([[1,.06,.03],[.04,1,.04],[.02,.08,1]],np.float32)*m.maximum_density
    image=f['paper'][...,None]*10**(-density)
    if body.schema_version==2:image=support.observe(image,f,m,view)
    return s.illuminate(image,body,view)


def preview(state,view=ViewingConditions()):return observe(_body(state).bodies[0],view)


def handling(state,**parameters):
    s.require(state,'exposed','developed','stopped','fixed','washed');return s.handling(state,**parameters)

class PaperFamily:
    expose=staticmethod(expose);finish=staticmethod(products);observe=staticmethod(observe)
    @staticmethod
    def process(state,operation,**parameters):
        handlers={'develop':develop,'stop':stop,'fix':fix,'bleach_fix':bleach_fix,'wash':wash,'dry':dry,'light_leak':light_leak,'handling':handling}
        if operation not in handlers:raise ValueError('Unsupported paper operation')
        return handlers[operation](state,**parameters)

OPERATION_FUNCTIONS={'develop':develop,'stop':stop,'fix':fix,'bleach_fix':bleach_fix,'wash':wash,'dry':dry,'light_leak':light_leak,'handling':handling}
FINAL_STAGES=('washed',)
def available_operations(state):
    if state is not None and state.schema_version==2 and state.stage=='washed':return ('fix','bleach_fix','wash','dry','handling')
    return {None:('expose',),'exposed':('develop','fix','light_leak','handling'),
        'developed':('develop','stop','fix','bleach_fix','handling'),'stopped':('fix','bleach_fix','handling'),
        'fixed':('fix','bleach_fix','wash','handling'),'washed':('fix','bleach_fix','wash','handling')}[state.stage if state else None]
def default_operations(material):return [('expose',{}),('develop',{}),('bleach_fix' if material.kind=='color' else 'fix',{}),('wash',{})]
