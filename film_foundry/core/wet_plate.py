"""Wet collodion: finite silver pools and a drying-dependent exposure/process.

Glass negatives transmit; ambrotype/tintype silver is viewed against a dark
support. Drying is a process condition, not an aged-photo overlay.
"""
from film_foundry.core.material_structure import StructureMaterial
from dataclasses import dataclass,field
import math
import numpy as np
from film_foundry.core import sheet_common as s, material_defects as defects
from film_foundry.core.material_family import ViewingConditions
from film_foundry.core import sheet_structure as structure
from film_foundry.core import wet_plate_process as process
FAMILY='wet_collodion_v1'
EXTERNAL_FIELDS={'external_silver':0,'external_deposit':0,'external_removed':0,'external_supplied':0}

@dataclass(frozen=True)
class WetPlateMaterial(StructureMaterial):
    support: str='glass_negative'
    half_exposure: float=.15
    response_contrast: float=1.5
    maximum_density: float=2.5
    drying_seconds: float=300.
    exposure_delay_s: float=0.
    humidity: float=.6
    coating_variation: float=.12
    edge_loss: float=.15
    texture: float=.15
    fog_level: float=0.
    particle_reflectance: float=.65
    base_transmission: float=.94
    pour_direction: str='down'
    flow_ridges: float=.25
    edge_pooling: float=.2
    pinholes: float=.05
    external_silver_load: float=.25
    physical_development_seconds: float=20.
    wetting_seconds: float=field(default=10.,kw_only=True)
    drying_lock_strength: float=field(default=.75,kw_only=True)
    enhancement_seconds: float=field(default=30.,kw_only=True)
    enhancement_nucleation: float=field(default=0.,kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        s.number(self.wetting_seconds,'wetting_seconds',.1,86400)
        s.number(self.drying_lock_strength,'drying_lock_strength',0,4)
        s.number(self.enhancement_seconds,'enhancement_seconds',.1,86400)
        s.number(self.enhancement_nucleation,'enhancement_nucleation')
        if self.support not in ('glass_negative','ambrotype','tintype'):raise ValueError('Unknown wet-plate support')
        s.number(self.external_silver_load,'external_silver_load')
        s.number(self.physical_development_seconds,'physical_development_seconds',.1,86400)
        s.number(self.half_exposure,'half_exposure',.001,20);s.number(self.maximum_density,'density',.01,10)
        s.number(self.response_contrast,'contrast',.2,8);s.number(self.drying_seconds,'drying_seconds',1,86400)
        s.number(self.exposure_delay_s,'exposure_delay_s',0,86400)
        for key in ('humidity','coating_variation','edge_loss','texture','fog_level','particle_reflectance','base_transmission'):s.number(getattr(self,key),key)
        if self.pour_direction not in ('down','up','left','right'):raise ValueError('Invalid pour direction')
        for key in ('flow_ridges','edge_pooling','pinholes'):s.number(getattr(self,key),key)

class WetPlateState(s.SheetState):
    MATERIAL=WetPlateMaterial
    STAGES=('coated','sensitized','exposed','developed','fixed','washed','dry','varnished','enhancing')
    FIELDS={'dose':0,'latent':0,'halide':0,'silver':0,'removed_silver':0,'coating':0,'moisture':0,'surface':0,'varnish':0,'residue':3}
    FIELD_VERSIONS={1:FIELDS,2:{**FIELDS,'flow':0,'roughness':0},3:{**FIELDS,'flow':0,'roughness':0,**EXTERNAL_FIELDS},
                    4:{**FIELDS,'flow':0,'roughness':0,**EXTERNAL_FIELDS,**process.FIELDS}}
    FIELD_LIMITS=process.LIMITS

    def __post_init__(self):
        super().__post_init__()
        if self.schema_version<4 and self.stage=='enhancing':raise ValueError('Legacy wet plates cannot intensify')
        if self.schema_version>=4:process.validate(self.fields)
        if self.schema_version>=3:
            f=self.fields
            if not np.allclose(f['external_silver']+f['external_deposit']+f['external_removed'],f['external_supplied'],atol=2e-6,rtol=0):
                raise ValueError('External silver inventory is not conserved')
            if (f['external_supplied']>f['coating']*self.material.external_silver_load+2e-6).any():
                raise ValueError('Silver supply exceeds the sensitizing carryover')

def prepare(shape,material=WetPlateMaterial(),*,group_id,seed=0,schema_version=4):
    if len(shape)!=2 or any(type(n) is not int or n<=0 for n in shape):raise ValueError('Invalid plate dimensions')
    geometry=structure.wet_structure(shape,seed,material) if schema_version>=2 else {}
    cover,surface=(geometry['coating'],geometry['surface']) if geometry else s.coating(shape,seed,material.coating_variation,material.edge_loss,material.texture)
    zero=np.zeros(shape,np.float32)
    return WetPlateState(group_id,seed,material,'coated',0.,dict(dose=zero,latent=zero,halide=zero,silver=zero,
        removed_silver=zero,coating=cover,moisture=np.ones(shape,np.float32),surface=surface,varnish=zero,residue=np.zeros(shape+(3,),np.float32),
        **{k:v for k,v in geometry.items() if k in ('flow','roughness')},
        **({k:zero for k in EXTERNAL_FIELDS} if schema_version>=3 else {}),
        **({'collodion_access':np.ones(shape,np.float32),**{k:zero for k in process.SILVER_FIELDS}} if schema_version>=4 else {})),schema_version=schema_version)

def sensitize(state,*,seconds=180.,activity=1.,temperature_c=20.,coverage=1.):
    s.require(state,'coated','sensitized');f=dict(state.fields)
    if state.schema_version>=4:
        f,effective=process.wet_interval(state,seconds,coverage=coverage,temperature_c=temperature_c)
        rate=process.conversion(effective,30.,activity,temperature_c)
        f['halide']=f['halide']+(f['coating']-f['halide'])*rate
        supplied=(f['coating']*state.material.external_silver_load-f['external_supplied'])*rate
        f['external_supplied']=f['external_supplied']+supplied;f['external_silver']=f['external_silver']+supplied
        return s.change(state,'sensitize',f,'sensitized',seconds,activity=activity,temperature_c=temperature_c,coverage=process.contact_record(coverage))
    if temperature_c!=20. or np.any(np.asarray(coverage)!=1.):raise ValueError('Bath contact and temperature require a v4 plate')
    amount=(f['coating']-f['halide'])*s.rate(seconds,30.,activity)*f['moisture']
    f['halide']=f['halide']+amount
    if state.schema_version==3:
        supplied=(f['coating']*state.material.external_silver_load-f['external_supplied'])*s.rate(seconds,30.,activity)*f['moisture']
        f['external_supplied']=f['external_supplied']+supplied
        f['external_silver']=f['external_silver']+supplied
    return s.change(state,'sensitize',f,'sensitized',seconds,activity=activity)

def _dry_fields(state,seconds,humidity,temperature_c):
    if state.schema_version>=4:return process.wet_interval(state,seconds,humidity=humidity,temperature_c=temperature_c)[0]
    humidity=state.material.humidity if humidity is None else humidity
    s.number(humidity,'humidity');s.number(seconds,'seconds',0,86400);s.number(temperature_c,'temperature_c',0,60)
    f=dict(state.fields);tau=state.material.drying_seconds
    rate=(1-.9*humidity)*2**((temperature_c-20)/10)/tau
    # Thin/poorly coated parts lose solvent faster; no random redraw over time.
    f['moisture']=f['moisture']*np.exp(-rate*seconds*(2-f['coating']))
    return f


def wait(state,*,seconds=60.,humidity=None,temperature_c=20.):
    if state.schema_version>=4:s.require(state,'coated','sensitized','exposed','developed','fixed','washed','dry')
    humidity=state.material.humidity if humidity is None else humidity
    f=_dry_fields(state,seconds,humidity,temperature_c)
    return s.change(state,'wait',f,seconds=seconds,humidity=humidity,temperature_c=temperature_c)

def expose_surface(state,*,image):
    s.require(state,'sensitized');image=s.image_rgb(image)
    if image.shape[:2]!=state.fields['coating'].shape:raise ValueError('Plate exposure geometry differs')
    if state.material.exposure_delay_s:state=wait(state,seconds=state.material.exposure_delay_s)
    f=dict(state.fields)
    if state.schema_version>=2:image=image*10**(-f['residue'])
    f['dose']=image[...,2]+image[...,1]*.06
    if state.schema_version>=4:
        f['dose']=f['dose']*f['moisture']*f['collodion_access']
        f['latent']=np.where(f['dose']>0,s.response(f['dose'],state.material.half_exposure,state.material.response_contrast),0.)
    else:f['latent']=s.response(f['dose']*f['moisture'],state.material.half_exposure,state.material.response_contrast)
    return s.change(state,'expose',f,'exposed')

def expose(image,material=WetPlateMaterial(),*,group_id,seed=0,schema_version=4):
    image=s.image_rgb(image);state=sensitize(prepare(image.shape[:2],material,group_id=group_id,seed=seed,schema_version=schema_version))
    return expose_surface(state,image=image)

def light_leak(state,*,strength=.2,edge='left'):
    s.require(state,'exposed');s.number(strength,'strength',0,10);f=dict(state.fields)
    if state.schema_version>=4:
        # Dose is already effective at each exposure instant; later drying and
        # residue must not recompute or erase earlier exposures.
        transmitted=10**(-f['residue'][...,2])+.06*10**(-f['residue'][...,1])
        f['dose']=f['dose']+strength*defects.edge_field(f['coating'].shape,state.seed,edge)*transmitted*f['moisture']*f['collodion_access']
        f['latent']=np.where(f['dose']>0,s.response(f['dose'],state.material.half_exposure,state.material.response_contrast),0.)
        return s.change(state,'light_leak',f,strength=strength,edge=edge)
    f['dose']=f['dose']+strength*defects.edge_field(f['coating'].shape,state.seed,edge)
    new=s.response(f['dose']*f['moisture'],state.material.half_exposure,state.material.response_contrast)
    f['latent']=np.maximum(f['latent'],new)
    return s.change(state,'light_leak',f,strength=strength,edge=edge)

def develop(state,*,seconds=30.,activity=1.,temperature_c=20.,pour_uniformity=1.,coverage=1.):
    s.require(state,'exposed','developed');f=dict(state.fields)
    initial=f['halide']+f['silver']+f['removed_silver'];m=state.material
    target=initial*(f['latent']+(1-f['latent'])*m.fog_level)
    s.number(pour_uniformity,'pour_uniformity')
    if state.schema_version>=4:
        contact=process.contact_field(coverage,f['coating'].shape)*(1-(1-pour_uniformity)*f['flow'])
        f,effective=process.wet_interval(state,seconds,coverage=contact,temperature_c=temperature_c)
        rate=process.conversion(effective,12.,activity,temperature_c)
    elif state.schema_version==1:
        if pour_uniformity!=1:raise ValueError('Uneven developer flow requires a new plate')
        rate=s.rate(seconds,12.,activity,temperature_c)*f['moisture']
    else:
        access=f['moisture']*(1-(1-pour_uniformity)*f['flow'])
        rate=s.spatial_rate(seconds,12.,activity,access,temperature_c)
    if state.schema_version<4 and np.any(np.asarray(coverage)!=1.):raise ValueError('Developer contact requires a v4 plate')
    converted=np.minimum(f['halide'],np.maximum(target-f['silver'],0))*rate
    f['silver']=f['silver']+converted;f['halide']=f['halide']-converted
    if state.schema_version>=3 and m.external_silver_load:
        centres=f['latent']+(1-f['latent'])*m.fog_level
        transfer_rate=(process.conversion(effective*centres,m.physical_development_seconds,activity,temperature_c) if state.schema_version>=4 else s.spatial_rate(seconds,m.physical_development_seconds,activity,access*centres,temperature_c))
        deposited=f['external_silver']*transfer_rate
        f['external_silver']=f['external_silver']-deposited
        f['external_deposit']=f['external_deposit']+deposited
    return s.change(state,'develop',f,'developed',seconds,activity=activity,temperature_c=temperature_c,pour_uniformity=pour_uniformity,**({'coverage':process.contact_record(coverage)} if state.schema_version>=4 else {}))

def fix(state,*,seconds=120.,activity=1.,temperature_c=20.,coverage=1.):
    if state.schema_version>=4:
        s.require(state,'exposed','developed','fixed','enhancing')
        f,effective=process.wet_interval(state,seconds,coverage=coverage,temperature_c=temperature_c)
        rate=process.conversion(effective,20.,activity,temperature_c)
        for source,destination in (('halide','removed_silver'),('external_silver','external_removed'),('enhancement_silver','enhancement_removed')):
            removed=f[source]*rate;f[source]=f[source]-removed;f[destination]=f[destination]+removed
        stage='fixed' if state.stage!='enhancing' or (seconds>0 and activity>0 and np.any(effective)) else 'enhancing'
        return s.change(state,'fix',f,stage,seconds,activity=activity,temperature_c=temperature_c,coverage=process.contact_record(coverage))
    if temperature_c!=20. or np.any(np.asarray(coverage)!=1.):raise ValueError('Bath contact and temperature require a v4 plate')
    s.require(state,'exposed','developed','fixed');f=dict(state.fields)
    removed=f['halide']*s.rate(seconds,20.,activity);f['halide']=f['halide']-removed;f['removed_silver']=f['removed_silver']+removed
    if state.schema_version==3:
        removed_external=f['external_silver']*s.rate(seconds,20.,activity)
        f['external_silver']=f['external_silver']-removed_external
        f['external_removed']=f['external_removed']+removed_external
    return s.change(state,'fix',f,'fixed',seconds,activity=activity)

def wash(state,*,seconds=180.,water_changes=3.,temperature_c=20.,coverage=1.):
    if state.schema_version>=4:
        s.require(state,'fixed','washed','enhancing');s.number(water_changes,'water_changes',0,100)
        f,effective=process.wet_interval(state,seconds,coverage=coverage,temperature_c=temperature_c)
        contact=process.contact_field(coverage,f['coating'].shape)
        f['residue']=f['residue']*(1-process.conversion(contact*seconds,60.,water_changes/3,temperature_c))[...,None]
        rate=process.conversion(effective,30.,water_changes/3,temperature_c)
        for source,destination in (('external_silver','external_removed'),('enhancement_silver','enhancement_removed')):
            removed=f[source]*rate;f[source]=f[source]-removed;f[destination]=f[destination]+removed
        stage='washed' if state.stage!='enhancing' or (seconds>0 and water_changes>0 and np.any(contact)) else 'enhancing'
        return s.change(state,'wash',f,stage,seconds,water_changes=water_changes,temperature_c=temperature_c,coverage=process.contact_record(coverage))
    if temperature_c!=20. or np.any(np.asarray(coverage)!=1.):raise ValueError('Bath contact and temperature require a v4 plate')
    s.require(state,'fixed','washed');s.number(water_changes,'water_changes',0,100);f=dict(state.fields)
    f['residue']=f['residue']*(1-s.rate(seconds,60.,water_changes/3));f['moisture']=np.ones_like(f['moisture'])
    if state.schema_version==3:
        removed_external=f['external_silver']*s.rate(seconds,30.,water_changes/3)
        f['external_silver']=f['external_silver']-removed_external
        f['external_removed']=f['external_removed']+removed_external
    return s.change(state,'wash',f,'washed',seconds,water_changes=water_changes)

def dry(state,*,seconds=600.,humidity=0.,temperature_c=20.):
    if state.schema_version<4 and (humidity!=0. or temperature_c!=20.):raise ValueError('Drying environment requires a v4 plate')
    s.require(state,'washed','dry');f=_dry_fields(state,seconds,humidity,temperature_c)
    return s.change(state,'dry',f,'dry',seconds,**({'humidity':humidity,'temperature_c':temperature_c} if state.schema_version>=4 else {}))

def varnish(state,*,amount=1.):
    s.require(state,'dry','varnished');s.number(amount,'amount')
    if np.max(state.fields['moisture'])>.1:raise ValueError('Dry the washed plate further before varnishing')
    fields=dict(state.fields);fields['varnish']=np.minimum(1.,fields['varnish']+amount)
    return s.change(state,'varnish',fields,stage='varnished',amount=amount)

BODY_FIELDS={k:v for k,v in WetPlateState.FIELDS.items() if k in ('silver','halide','coating','surface','varnish','residue')}

def _body(state):
    negative=state.material.support=='glass_negative'
    keys=tuple(BODY_FIELDS)+(('external_deposit','external_silver') if state.schema_version>=3 else ())+(('roughness',) if state.schema_version>=2 else ())
    if state.schema_version>=4:keys+=tuple(process.FIELDS)+('moisture',)
    return s.finish(state,FAMILY,'wet_negative' if negative else 'wet_positive',f'wet_plate_optics_v{state.schema_version}',
                    {k:state.fields[k] for k in keys},negative=negative)

def products(state):
    s.require(state,'washed','dry','varnished');return _body(state)

@s.cached_body_validation
def validate_body(body):
    negative=body.role=='wet_negative'
    if body.schema_version not in (1,2,3,4):raise ValueError('Unknown wet-plate version')
    spec={**BODY_FIELDS,**({'roughness':0} if body.schema_version>=2 else {}),
          **({'external_deposit':0,'external_silver':0} if body.schema_version>=3 else {}),
          **({**process.FIELDS,'moisture':0} if body.schema_version>=4 else {})}
    m=s.validate_body(body,FAMILY,'wet_negative' if negative else 'wet_positive',f'wet_plate_optics_v{body.schema_version}',spec,WetPlateMaterial,negative,version=body.schema_version,limits=process.LIMITS)
    if body.schema_version>=4:process.validate(body.fields)
    if negative!=(m.support=='glass_negative'):raise ValueError('Support and observation disagree')
    return m

def observe(body,view=ViewingConditions()):
    m=validate_body(body);f=body.fields
    varnish=f['varnish'][...,None]*np.array([.002,.006,.018],np.float32)
    silver=f['silver']+f.get('external_deposit',0)+f.get('enhancement_deposit',0)
    density=(silver*m.maximum_density+(f['halide']+f.get('external_silver',0)+f.get('enhancement_silver',0))*.12)[...,None]+f['residue']+varnish
    if body.usable_negative:image=m.base_transmission*10**(-density)*np.ones(3,np.float32)
    else:
        # Bright particulate silver against dark backing, not image inversion.
        scatter=(1-np.exp(-silver*2))*m.particle_reflectance*f['surface']
        image=(.012*10**(-2*density)+scatter[...,None]*np.array([1.,.94,.83],np.float32)*10**(-f['residue']-varnish))
        if body.schema_version>=2:
            # The silver image is backed by glass or dark metal; a varnished
            # surface adds directional reflection rather than paper texture.
            glass=m.support=='ambrotype'
            tint=np.array([1.,.96,.9] if glass else [.95,.98,1.],np.float32)
            backing=.006 if glass else .022
            surface_reflection=(.035 if glass else .012)+.025*f['varnish']
            glare=surface_reflection*structure.view_gloss(view,np.clip(f['roughness']+.08,0,1))
            image=(backing*10**(-2*density)+scatter[...,None]*tint*10**(-f['residue']-varnish)+glare[...,None])
    return s.illuminate(image,body,view)

def preview(state,view=ViewingConditions()):return observe(_body(state).bodies[0],view)

def handling(state,**parameters):return s.handling(state,**parameters)

load_enhancer=process.load_enhancer
intensify=process.intensify

class WetPlateFamily:
    expose=staticmethod(expose);finish=staticmethod(products);observe=staticmethod(observe)
    @staticmethod
    def process(state,operation,**parameters):
        handlers={'expose':expose_surface,**OPERATION_FUNCTIONS}
        if operation not in handlers:raise ValueError('Unsupported wet-plate operation')
        return handlers[operation](state,**parameters)

OPERATION_FUNCTIONS={'sensitize':sensitize,'wait':wait,'develop':develop,'fix':fix,'wash':wash,'dry':dry,'varnish':varnish,'light_leak':light_leak,'handling':handling,
                    'load_enhancer':load_enhancer,'intensify':intensify}
FINAL_STAGES=('washed','dry','varnished')
EXPOSURE_STEPS=('prepare','sensitize','expose')
def available_operations(state):
    if state is not None and state.schema_version>=4:
        if state.stage=='enhancing':return ('intensify','load_enhancer','wash','fix','handling')
        if state.stage in ('fixed','washed'):
            return ('load_enhancer','fix','wash','wait','handling') if state.stage=='fixed' else ('load_enhancer','wash','dry','wait','handling')
    if state is not None and state.stage=='dry' and np.max(state.fields['moisture'])>.1:return ('dry','handling')
    return {None:('prepare',),'coated':('sensitize','wait','handling'),'sensitized':('sensitize','wait','expose','handling'),
        'exposed':('develop','fix','wait','light_leak','handling'),'developed':('develop','fix','wait','handling'),
        'fixed':('fix','wash','handling'),'washed':('wash','dry','handling'),'dry':('dry','varnish','handling'),
        'varnished':('varnish','handling')}[state.stage if state else None]
def default_operations(material):return [('prepare',{}),('sensitize',{}),('expose',{}),('develop',{}),('fix',{}),('wash',{})]
