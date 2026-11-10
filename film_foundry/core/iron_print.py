"""Distinct iron-mediated cyanotype and platinum/palladium material pools.

RGB input is an explicit actinic proxy, not reconstructed UV. The families
share field/transport helpers, but cyan pigment and noble-metal development
remain different reactions with different endpoints.
"""
from dataclasses import dataclass,replace
import math
import numpy as np
from film_foundry.core import sheet_common as s, material_defects as defects
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core import sheet_structure as structure, paper_support as support
CYAN_FAMILY='cyanotype_v1'
NOBLE_FAMILY='platinum_palladium_v1'

@dataclass(frozen=True)
class IronMaterial(support.PaperSupportMaterial):
    half_exposure: float=.3
    response_contrast: float=1.3
    maximum_density: float=2.1
    paper_reflectance: float=.93
    actinic_gain: float=1.
    green_sensitivity: float=.05
    humidity: float=.6
    coating_variation: float=.15
    edge_loss: float=.1
    texture: float=.3
    sensitizer_remaining: float=1.

    def __post_init__(self):
        super().__post_init__()
        for key in ('half_exposure','maximum_density','actinic_gain'):s.number(getattr(self,key),key,.001,20)
        s.number(self.response_contrast,'contrast',.2,8)
        for key in ('paper_reflectance','green_sensitivity','humidity','coating_variation','edge_loss','texture','sensitizer_remaining'):s.number(getattr(self,key),key)

@dataclass(frozen=True)
class CyanotypeMaterial(IronMaterial):
    response_contrast: float=2.
    texture: float=.45
    oxidation_seconds: float=240.
    alkali_susceptibility: float=1.
    brush_direction: str='right'
    brush_overlap: float=.35
    pigment_binding: float=.7
    paper_buffer: float=0.
    def __post_init__(self):
        super().__post_init__();s.number(self.oxidation_seconds,'oxidation_seconds',1,7200);s.number(self.alkali_susceptibility,'alkali_susceptibility')
        if self.brush_direction not in ('down','up','left','right'):raise ValueError('Invalid brush direction')
        for key in ('brush_overlap','pigment_binding','paper_buffer'):s.number(getattr(self,key),key)

@dataclass(frozen=True)
class PlatinumMaterial(IronMaterial):
    response_contrast: float=.95
    coating_variation: float=.08
    platinum_fraction: float=.4
    clearing_seconds: float=45.
    coating_method: str='rod'
    paper_sizing: float=.65
    def __post_init__(self):
        super().__post_init__();s.number(self.platinum_fraction,'platinum_fraction');s.number(self.clearing_seconds,'clearing_seconds',1,7200)
        if self.coating_method not in ('rod','brush'):raise ValueError('Invalid platinum coating method')
        s.number(self.paper_sizing,'paper_sizing')

BASE_FIELDS={'ferric':0,'ferrous':0,'iron_removed':0,'coating':0,'paper':0,'residue':3}

class CyanotypeState(s.SheetState):
    MATERIAL=CyanotypeMaterial
    STAGES=('exposed','washed','oxidized')
    FIELDS={**BASE_FIELDS,'reduced_pigment':0,'blue_pigment':0}
    FIELD_VERSIONS={1:FIELDS,2:{**FIELDS,'binding':0,'fibers':0},3:{**FIELDS,'binding':0,'fibers':0,**support.FIELDS}}

class PlatinumState(s.SheetState):
    MATERIAL=PlatinumMaterial
    STAGES=('exposed','developed','cleared','washed')
    FIELDS={**BASE_FIELDS,'spent_ferric':0,'platinum_salt':0,'palladium_salt':0,'platinum':0,'palladium':0,'metal_removed':0}
    FIELD_VERSIONS={1:FIELDS,2:{**FIELDS,'absorption':0,'clear_access':0,'fibers':0},3:{**FIELDS,'absorption':0,'clear_access':0,'fibers':0,**support.FIELDS}}

def expose(image,material,*,group_id,seed=0,schema_version=3):
    image=s.image_rgb(image);shape=image.shape[:2]
    cyan=isinstance(material,CyanotypeMaterial)
    if not cyan and not isinstance(material,PlatinumMaterial):raise ValueError('Unknown iron material')
    geometry=(structure.cyan_structure if cyan else structure.platinum_structure)(shape,seed,material) if schema_version>=2 else {}
    cover,paper=(geometry['coating'],geometry['paper']) if geometry else s.coating(shape,seed,material.coating_variation,material.edge_loss,material.texture)
    dose=(image[...,2]+material.green_sensitivity*image[...,1])*material.actinic_gain
    humidity_response=math.exp(-3*(material.humidity-.6)**2)
    if schema_version>=2 and not cyan:humidity_response=humidity_response*(.85+.15*geometry['absorption'])
    activated=s.response(dose*humidity_response,material.half_exposure,material.response_contrast)*material.sensitizer_remaining
    capacity=cover*(1 if cyan else 2)
    fields=dict(ferric=capacity*(1-activated),ferrous=capacity*activated,iron_removed=np.zeros(shape,np.float32),
        coating=cover,paper=paper*material.paper_reflectance,residue=np.zeros(shape+(3,),np.float32))
    if cyan:
        fields.update(reduced_pigment=np.zeros(shape,np.float32),blue_pigment=np.zeros(shape,np.float32));cls=CyanotypeState
    else:
        fields.update(spent_ferric=np.zeros(shape,np.float32),platinum_salt=cover*material.platinum_fraction,
            palladium_salt=cover*(1-material.platinum_fraction),platinum=np.zeros(shape,np.float32),palladium=np.zeros(shape,np.float32),metal_removed=np.zeros(shape,np.float32));cls=PlatinumState
    fields.update({k:v for k,v in geometry.items() if k not in ('coating','paper')})
    if schema_version==3:fields.update(support.initial_fields(shape,material,material.humidity))
    return cls(group_id,seed,material,'exposed',0.,fields,schema_version=schema_version)

def light_leak(state,*,strength=.2,edge='left'):
    s.require(state,'exposed');s.number(strength,'strength',0,10);f=dict(state.fields)
    dose=strength*defects.edge_field(f['coating'].shape,state.seed,edge)*state.material.actinic_gain
    fraction=-np.expm1(-dose/state.material.half_exposure)*state.material.sensitizer_remaining
    amount=f['ferric']*fraction;f['ferric']=f['ferric']-amount;f['ferrous']=f['ferrous']+amount
    return s.change(state,'light_leak',f,strength=strength,edge=edge)

def cyan_wash(state,*,seconds=180.,water_changes=3.,region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.,temperature_c=20.):
    s.require(state,'exposed','washed','oxidized');s.number(seconds,'seconds',0,86400);s.number(water_changes,'water_changes',0,100)
    f=dict(state.fields);flow=water_changes/3
    local=dict(region=region,position_x=position_x,position_y=position_y,radius=radius,coverage=coverage,temperature_c=temperature_c)
    if state.schema_version==3:f,dose=support.wet_fields(state,seconds,**local)
    else:support.legacy_parameters(**local);dose=seconds
    if state.schema_version>=2:
        reaction=flow/4;total=reaction+flow/120
        loss=flow*(.0004+.0018*(1-f['binding'])+.004*state.material.paper_buffer)
        ea=np.exp(-total*dose) if state.schema_version==3 else math.exp(-total*seconds);ep=np.exp(-loss*dose)
        gain=np.divide(ea-ep,loss-total,out=dose*ep.copy(),where=np.abs(loss-total)>1e-8)
        retained=f['ferrous']*reaction*gain
        converted=f['ferrous']*(1-ea)
        ferric=f['ferric']*(-np.expm1(-dose*flow/35.) if state.schema_version==3 else s.rate(seconds,35.,flow))
        lost=(f['reduced_pigment']+f['blue_pigment'])*(1-ep)+np.maximum(0,converted-retained)
        f['ferrous']=f['ferrous']*ea;f['ferric']=f['ferric']-ferric
        f['reduced_pigment']=f['reduced_pigment']*ep+retained;f['blue_pigment']=f['blue_pigment']*ep
        f['iron_removed']=f['iron_removed']+ferric+lost
        f['residue']=f['residue']*(np.exp(-dose[...,None]*flow/60.) if state.schema_version==3 else 1-s.rate(seconds,60.,flow))
        return s.change(state,'wash',f,'washed',seconds,water_changes=water_changes,**local)
    reaction=flow/4;leach=flow/120
    total=reaction+leach
    converted=f['ferrous']*(-math.expm1(-total*seconds)) if total else np.zeros_like(f['ferrous'])
    retained=converted*(reaction/total) if total else converted
    ferric=f['ferric']*s.rate(seconds,35.,flow)
    f['ferric']=f['ferric']-ferric;f['ferrous']=f['ferrous']-converted
    f['reduced_pigment']=f['reduced_pigment']+retained;f['iron_removed']=f['iron_removed']+ferric+converted-retained
    f['residue']=f['residue']*(1-s.rate(seconds,60.,flow))
    return s.change(state,'wash',f,'washed',seconds,water_changes=water_changes)

def oxidize(state,*,seconds=600.,oxygen=1.):
    s.require(state,'washed','oxidized');f=dict(state.fields)
    rate=s.spatial_rate(seconds,state.material.oxidation_seconds,oxygen,.7+.3*(1-f['binding'])) if state.schema_version>=2 else s.rate(seconds,state.material.oxidation_seconds,oxygen)
    amount=f['reduced_pigment']*rate
    f['reduced_pigment']=f['reduced_pigment']-amount;f['blue_pigment']=f['blue_pigment']+amount
    return s.change(state,'oxidize',f,'oxidized',seconds,oxygen=oxygen)

def alkali_contact(state,*,seconds=30.,activity=1.):
    s.require(state,'washed','oxidized');f=dict(state.fields)
    if state.schema_version==3:f,_=support.wet_fields(state,seconds,integrated=False)
    fraction=s.rate(seconds,60.,activity)*state.material.alkali_susceptibility
    removed=(f['blue_pigment']+f['reduced_pigment'])*fraction
    f['blue_pigment']=f['blue_pigment']*(1-fraction);f['reduced_pigment']=f['reduced_pigment']*(1-fraction)
    f['iron_removed']=f['iron_removed']+removed
    return s.change(state,'alkali_contact',f,seconds=seconds,activity=activity)

def noble_develop(state,*,seconds=60.,temperature_c=20.,activity=1.):
    s.require(state,'exposed','developed');f=dict(state.fields)
    if state.schema_version==3:f,_=support.wet_fields(state,seconds,temperature_c=temperature_c,integrated=False)
    # Exposed reducing capacity is co-located with both salts. Its original
    # quantity remains ferrous + spent_ferric until clearing. Fixed targets
    # prevent repeated short baths from manufacturing additional exposure.
    activated=np.divide(f['ferrous']+f['spent_ferric'],2*f['coating'],out=np.zeros_like(f['coating']),where=f['coating']>0)
    pt=np.maximum(0,(f['platinum_salt']+f['platinum'])*activated-f['platinum'])*s.rate(seconds,25.,activity,temperature_c)
    pd=np.maximum(0,(f['palladium_salt']+f['palladium'])*activated-f['palladium'])*s.rate(seconds,15.,activity,temperature_c)
    used=2*(pt+pd)
    f['platinum_salt']=f['platinum_salt']-pt;f['palladium_salt']=f['palladium_salt']-pd
    f['platinum']=f['platinum']+pt;f['palladium']=f['palladium']+pd
    f['ferrous']=np.maximum(0,f['ferrous']-used);f['spent_ferric']=f['spent_ferric']+used
    return s.change(state,'develop',f,'developed',seconds,activity=activity,temperature_c=temperature_c)

def _clear_fields(state,seconds,activity,local):
    f=dict(state.fields);dose=None
    if state.schema_version==3:
        s.number(activity,'activity',0,100)
        f,dose=support.wet_fields(state,seconds,**local)
        fraction=-np.expm1(-dose*activity*f['clear_access']/state.material.clearing_seconds)
    else:
        support.legacy_parameters(**local)
        fraction=s.spatial_rate(seconds,state.material.clearing_seconds,activity,f['clear_access']) if state.schema_version==2 else s.rate(seconds,state.material.clearing_seconds,activity)
    removed=(f['ferric']+f['ferrous']+f['spent_ferric'])*fraction
    for key in ('ferric','ferrous','spent_ferric'):f[key]=f[key]*(1-fraction)
    f['iron_removed']=f['iron_removed']+removed
    metal=(f['platinum_salt']+f['palladium_salt'])*fraction
    f['metal_removed']=f['metal_removed']+metal
    for key in ('platinum_salt','palladium_salt'):f[key]=f[key]*(1-fraction)
    return f,dose


def clear(state,*,seconds=180.,activity=1.,region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.,temperature_c=20.):
    s.require(state,'exposed','developed','cleared','washed')
    local=dict(region=region,position_x=position_x,position_y=position_y,radius=radius,coverage=coverage,temperature_c=temperature_c)
    f,_=_clear_fields(state,seconds,activity,local)
    return s.change(state,'clear',f,'cleared',seconds,activity=activity,**local)


def noble_wash(state,*,seconds=180.,water_changes=3.,region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.,temperature_c=20.):
    s.require(state,'cleared','washed');s.number(water_changes,'water_changes',0,100)
    local=dict(region=region,position_x=position_x,position_y=position_y,radius=radius,coverage=coverage,temperature_c=temperature_c)
    # Water alone has less clearing power than the explicit clearing bath.
    f,dose=_clear_fields(state,seconds,.1*water_changes/3,local)
    f['residue']=f['residue']*(np.exp(-dose[...,None]*water_changes/180.) if state.schema_version==3 else 1-s.rate(seconds,60.,water_changes/3))
    return s.change(state,'wash',f,'washed',seconds,water_changes=water_changes,**local)


def dry(state,*,seconds=600.,humidity=.5,temperature_c=20.,airflow=1.):
    result=support.dry(state,seconds=seconds,humidity=humidity,temperature_c=temperature_c,airflow=airflow)
    if isinstance(state,CyanotypeState) and airflow:
        f=dict(result.fields)
        rate=s.spatial_rate(seconds,state.material.oxidation_seconds,airflow,.7+.3*(1-f['binding']),temperature_c)
        amount=f['reduced_pigment']*rate
        f['reduced_pigment']=f['reduced_pigment']-amount;f['blue_pigment']=f['blue_pigment']+amount
        result=replace(result,fields=f,stage='oxidized')
    return result


def _body(state):
    cyan=isinstance(state,CyanotypeState);fields={k:v for k,v in state.fields.items() if k not in ('iron_removed','metal_removed')}
    return s.finish(state,CYAN_FAMILY if cyan else NOBLE_FAMILY,'cyanotype_print' if cyan else 'noble_print',
                    ('cyanotype_reflection_v' if cyan else 'noble_reflection_v')+str(state.schema_version),fields)

def products(state):
    s.require(state,*(('oxidized',) if isinstance(state,CyanotypeState) else ('washed',)))
    return _body(state)

@s.cached_body_validation
def validate_body(body):
    cyan=body.family==CYAN_FAMILY;cls=CyanotypeState if cyan else PlatinumState
    if body.schema_version not in (1,2,3):raise ValueError('Unknown iron-print version')
    spec={k:v for k,v in cls.FIELD_VERSIONS[body.schema_version].items() if k not in ('iron_removed','metal_removed')}
    return s.validate_body(body,CYAN_FAMILY if cyan else NOBLE_FAMILY,'cyanotype_print' if cyan else 'noble_print',
        ('cyanotype_reflection_v' if cyan else 'noble_reflection_v')+str(body.schema_version),spec,cls.MATERIAL,version=body.schema_version)

def observe(body,view=ViewingConditions()):
    m=validate_body(body);f=body.fields;iron=f['ferric']+f['ferrous']
    if body.schema_version>=2:
        if body.family==CYAN_FAMILY:
            pigment=f['blue_pigment'][...,None]
            absorption=.5*np.expm1(pigment*m.maximum_density*math.log(10))*np.array([1.35,.85,.12],np.float32)
            absorption+=f['reduced_pigment'][...,None]*np.array([.15,.1,.03],np.float32)
        else:
            iron=iron+f['spent_ferric']
            metal=f['platinum'][...,None]+f['palladium'][...,None]*np.array([.98,1.,1.045],np.float32)
            absorption=.5*np.expm1(metal*m.maximum_density*math.log(10))
        absorption+=iron[...,None]*np.array([.04,.16,.65],np.float32)+f['residue']
        image=structure.paper_reflectance(f['paper'],absorption,1+m.texture*.35*f['fibers'][...,None])
        if body.schema_version==3:image=support.observe(image,f,m,view)
        return s.illuminate(image,body,view)
    if body.family==CYAN_FAMILY:
        density=(f['blue_pigment'][...,None]*np.array([1.1,.65,.12],np.float32)+f['reduced_pigment'][...,None]*.04)*m.maximum_density
    else:
        iron=iron+f['spent_ferric']
        density=(f['platinum'][...,None]*np.array([1.,1.,1.],np.float32)+f['palladium'][...,None]*np.array([.88,1.,1.14],np.float32))*m.maximum_density
    density=density+iron[...,None]*np.array([.03,.1,.32],np.float32)+f['residue']
    return s.illuminate(f['paper'][...,None]*10**(-density),body,view)

def preview(state,view=ViewingConditions()):return observe(_body(state).bodies[0],view)

def handling(state,**parameters):return s.handling(state,**parameters)

class CyanotypeFamily:
    @staticmethod
    def expose(image,material=CyanotypeMaterial(),**kwargs):return expose(image,material,**kwargs)
    finish=staticmethod(products);observe=staticmethod(observe)
    @staticmethod
    def process(state,operation,**parameters):
        handlers={'wash':cyan_wash,'oxidize':oxidize,'alkali_contact':alkali_contact,'light_leak':light_leak,'handling':handling,'dry':dry}
        if operation not in handlers:raise ValueError('Unsupported cyanotype operation')
        return handlers[operation](state,**parameters)

class PlatinumFamily:
    @staticmethod
    def expose(image,material=PlatinumMaterial(),**kwargs):return expose(image,material,**kwargs)
    finish=staticmethod(products);observe=staticmethod(observe)
    @staticmethod
    def process(state,operation,**parameters):
        handlers={'develop':noble_develop,'clear':clear,'wash':noble_wash,'light_leak':light_leak,'handling':handling,'dry':dry}
        if operation not in handlers:raise ValueError('Unsupported platinum/palladium operation')
        return handlers[operation](state,**parameters)
