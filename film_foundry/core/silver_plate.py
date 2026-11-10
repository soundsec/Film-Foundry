"""Daguerreotype-inspired creative surface model; no real chemical recipe.

Image particles scatter light over a directional metallic substrate. Observation
depends on light, background and view, never an inversion written into the plate.
"""
from film_foundry.core.material_structure import StructureMaterial
from dataclasses import dataclass,asdict,replace,field
import json
import math
import numpy as np
from film_foundry.core.material_family import MediumBody,ProductGroup,ViewingConditions,frozen_fields
from film_foundry.core import material_defects as defects
from film_foundry.core.material_metadata import ProcessAnnotations
from film_foundry.core.material_family import cached_body_validation
from film_foundry.core import plate_baths as baths, material_structure as ms, scaled_structure as scaled
from film_foundry.core import plate_gold

FAMILY="daguerreotype_surface_v1"
FIELDS={"sensitivity","latent","particles","roughness","polish_x","damage","tarnish","gold"}
SURFACE_FIELDS={"polish_y","microtexture","pit_depth"}


@dataclass(frozen=True)
class PlateMaterial(StructureMaterial):
    half_exposure: float=.18
    reaction_seconds: float=40.
    roughness: float=.12
    metal_reflectance: float=.9
    particle_reflectance: float=.7
    response_contrast: float=1.65
    fixing_seconds: float=12.
    gilding_seconds: float=20.
    gold_capacity: float=field(default=1.,kw_only=True)
    gold_adhesion: float=field(default=.8,kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        from film_foundry.core.sheet_common import number
        number(self.gold_capacity,'gold_capacity',0,10);number(self.gold_adhesion,'gold_adhesion')
        for value in (self.half_exposure,self.reaction_seconds,self.fixing_seconds,self.gilding_seconds):
            if not math.isfinite(value) or value<=0:raise ValueError("Positive finite material parameters required")
        if not .02<=self.roughness<=1:raise ValueError("Roughness must be .02..1")
        if not 0<self.metal_reflectance<=1 or not 0<self.particle_reflectance<=1:raise ValueError("Invalid reflectance")
        if not math.isfinite(self.response_contrast) or not .5<=self.response_contrast<=3:raise ValueError("Invalid response contrast")


@dataclass(frozen=True)
class PlateState(ProcessAnnotations):
    group_id: str
    seed: int
    material: PlateMaterial
    stage: str
    elapsed_seconds: float
    fields: object
    process_json: str="{}"
    schema_version: int=4

    def __post_init__(self):
        if self.schema_version not in (1,2,3,4) or self.stage not in ("blank","polished","sensitized","exposed","developed","fixing","fixed","gilding","gilded","finished"):
            raise ValueError("Unsupported plate state")
        if self.schema_version<4 and self.stage=='finished':raise ValueError('Explicit plate snapshot requires v4')
        if self.schema_version<3 and self.stage in ('fixing','gilding'):raise ValueError('Partial baths require v3 plate')
        if not self.group_id or not isinstance(self.seed,int) or self.seed<0:raise ValueError("Invalid plate identity")
        if not math.isfinite(self.elapsed_seconds) or self.elapsed_seconds<0:raise ValueError("Invalid development time")
        fields=frozen_fields(self.fields)
        if set(fields)!=_field_names(self.schema_version) or any(v.ndim!=2 for v in fields.values()):raise ValueError("Invalid plate fields")
        _validate_ranges(fields,self.schema_version)
        if (fields["roughness"]<.02).any():raise ValueError("Plate roughness too small")
        if (fields["particles"]>fields["latent"]+1e-6).any():raise ValueError("Particles exceed activated surface")
        if self.schema_version>=3:
            baths.validate(fields)
            if self.schema_version>=4:plate_gold.validate(fields)
            if self.stage in ("fixed","gilding","gilded") and np.max(fields["sensitivity"])>1e-4:raise ValueError("Plate still needs fixing")
        elif self.stage in ("fixed","gilded") and np.any(fields["sensitivity"]):raise ValueError("Fixed plate retains sensitivity")
        if self.stage in ("blank","polished","sensitized") and (np.any(fields["particles"]) or np.any(fields["latent"]) or self.elapsed_seconds):
            raise ValueError("Unexposed plate contains developed image")
        if not isinstance(json.loads(self.process_json),dict):raise ValueError("Invalid process snapshot")
        object.__setattr__(self,"fields",fields)


def _field_names(version):
    return FIELDS|(SURFACE_FIELDS if version>=2 else set())|(baths.FIELDS if version>=3 else set())|(plate_gold.FIELDS if version>=4 else set())


def _validate_ranges(fields,version):
    for key,value in fields.items():
        if np.any(value<0) or (not (version>=4 and key in plate_gold.UNBOUNDED) and np.any(value>1)):
            raise ValueError('Plate field outside its range: '+key)


def prepare(shape,material=PlateMaterial(),*,group_id,seed=0,schema_version=4):
    if len(shape)!=2 or any(not isinstance(v,int) or v<=0 for v in shape):raise ValueError("Positive HxW plate dimensions required")
    if schema_version not in (2,3,4):raise ValueError("Only v2/v3/v4 preparation is supported")
    fields={k:np.zeros(shape,np.float32) for k in FIELDS}
    fields["roughness"]=np.full(shape,material.roughness,np.float32)
    fields.update(polish_x=np.full(shape,.5,np.float32),polish_y=np.full(shape,.5,np.float32),
                  microtexture=np.full(shape,.5,np.float32),pit_depth=np.zeros(shape,np.float32))
    if schema_version>=3:fields.update({k:np.zeros(shape,np.float32) for k in baths.FIELDS})
    if schema_version>=4:fields.update({k:np.zeros(shape,np.float32) for k in plate_gold.FIELDS})
    return PlateState(group_id,seed,material,"blank",0.,fields,schema_version=schema_version)


def _record(state,**values):return json.dumps(json.loads(state.process_json)|values,sort_keys=True,allow_nan=False)


def polish(state,*,streaks=.15,angle_degrees=0.):
    if state.stage!="blank" or not 0<=streaks<=1:raise ValueError("Polish a blank plate with streaks in [0,1]")
    if not math.isfinite(angle_degrees) or not -180<=angle_degrees<=180:raise ValueError("Invalid polishing angle")
    h,w=state.fields["roughness"].shape
    if state.schema_version>=2:
        grooves,grain=(scaled.polish((h,w),state.seed,state.material,angle_degrees) if ms.enabled(state.material) else defects.polish_grain((h,w),state.seed,angle_degrees))
        angle=math.radians(angle_degrees)
        fields=dict(state.fields)
        fields["polish_x"]=.5+.5*streaks*grooves*math.cos(angle)
        fields["polish_y"]=.5+.5*streaks*grooves*math.sin(angle)
        fields["roughness"]=np.clip(state.material.roughness+streaks*.08*(.25+grain),.02,1)
        fields["microtexture"]=grain
        return replace(state,stage="polished",fields=fields,process_json=_record(state,polish_streaks=streaks,polish_angle=angle_degrees))
    if angle_degrees:raise ValueError("Directional polishing requires a newly prepared v2 plate")
    phase=np.random.default_rng(state.seed).uniform(0,2*np.pi)
    lines=(.5+.5*np.sin((np.arange(h,dtype=np.float32)+.5)/h*80*np.pi+phase))[:,None]
    field=np.broadcast_to(lines,(h,w))
    fields=dict(state.fields);fields["roughness"]=np.clip(state.material.roughness+streaks*.15*field,.02,1)
    fields["polish_x"]=np.asarray(streaks*field,dtype=np.float32)
    return replace(state,stage="polished",fields=fields,process_json=_record(state,polish_streaks=streaks))


def sensitize(state,*,strength=1.):
    if state.stage!="polished" or not 0<=strength<=1:raise ValueError("Sensitize a polished plate with strength in [0,1]")
    fields=dict(state.fields);fields["sensitivity"]=np.full_like(fields["sensitivity"],strength)
    if state.schema_version>=3:fields.update(halide=fields["sensitivity"],sensitized_silver=fields["sensitivity"])
    return replace(state,stage="sensitized",fields=fields,process_json=_record(state,sensitization=strength))


def expose_surface(state,*,image):
    if state.stage!="sensitized":raise ValueError("Exposure requires a sensitized plate")
    image=np.asarray(image,dtype=np.float32)
    if image.shape!=state.fields["latent"].shape+(3,) or not np.isfinite(image).all() or (image<0).any():
        raise ValueError("Exposure must be matching finite nonnegative linear RGB")
    y=image @ np.array([.2126,.7152,.0722],np.float32)
    fields=dict(state.fields)
    if state.schema_version>=2:
        exponent=state.material.response_contrast
        # Exposure response, not a contrast curve applied to an observed photograph.
        power=np.power(y,exponent)
        fields["latent"]=power/(power+state.material.half_exposure**exponent)*fields["sensitivity"]
    else:fields["latent"]=y/(y+state.material.half_exposure)*fields["sensitivity"]
    return replace(state,stage="exposed",fields=fields)


def expose(image,material=PlateMaterial(),*,group_id,seed=0,schema_version=4):
    """Convenience entry with explicit default preparation; individual stages also available."""
    image=np.asarray(image)
    if image.ndim!=3 or image.shape[2]!=3:raise ValueError("HxWx3 exposure required")
    state=prepare(image.shape[:2],material,group_id=group_id,seed=seed,schema_version=schema_version)
    return expose_surface(sensitize(polish(state)),image=image)


def develop(state,*,seconds):
    if state.stage not in ("exposed","developed"):raise ValueError("Development requires an exposed unfixed plate")
    if not math.isfinite(seconds) or seconds<0:raise ValueError("Invalid development time")
    total=state.elapsed_seconds+seconds
    if not math.isfinite(total):raise ValueError("Process time overflow")
    fields=dict(state.fields)
    if state.schema_version>=3:
        converted=(fields["latent"]-fields["particles"])*(-math.expm1(-seconds/state.material.reaction_seconds))
        fields["particles"]=fields["particles"]+converted
        fields["halide"]=np.maximum(fields["halide"]-converted,0)
        fields["sensitivity"]=fields["halide"]
    else:fields["particles"]=fields["latent"]*(-math.expm1(-total/state.material.reaction_seconds))
    return replace(state,stage="developed",elapsed_seconds=total,fields=fields)


def light_leak(state,*,strength=0.,edge="left"):
    if state.stage!="exposed":raise ValueError("Dark-slide opening is supported after exposure and before development only")
    strength=defects.fraction(strength,"Light exposure")
    if strength==0:return state
    fields=dict(state.fields)
    dose=defects.edge_field(fields["latent"].shape,state.seed,edge)*strength*5
    fields["latent"]=fields["latent"]+np.maximum(fields["sensitivity"]-fields["latent"],0)*(-np.expm1(-dose))
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,"light_leak",strength=strength,edge=edge))


def fix(state,*,seconds=120.,activity=1.,temperature_c=20.,coverage=1.):
    if state.schema_version>=3:return baths.fixing(state,seconds=seconds,activity=activity,temperature_c=temperature_c,coverage=coverage)
    if (seconds,activity,temperature_c,coverage)!=(120.,1.,20.,1.):raise ValueError("Bath parameters require v3 plate")
    if state.stage!="developed":raise ValueError("Fixing requires a developed plate")
    fields=dict(state.fields);fields["sensitivity"]=np.zeros_like(fields["sensitivity"])
    return replace(state,stage="fixed",fields=fields)


def gild(state,*,amount=.5,seconds=180.,activity=1.,temperature_c=20.,coverage=1.):
    if state.schema_version>=4:return plate_gold.gild(state,amount=amount,seconds=seconds,activity=activity,temperature_c=temperature_c,coverage=coverage)
    if state.schema_version==3:return baths.gilding(state,amount=amount,seconds=seconds,activity=activity,temperature_c=temperature_c,coverage=coverage)
    if (seconds,activity,temperature_c,coverage)!=(180.,1.,20.,1.):raise ValueError("Bath parameters require v3 plate")
    if state.stage!="fixed" or not 0<=amount<=1:raise ValueError("Gild a fixed plate with amount in [0,1]")
    fields=dict(state.fields);fields["gold"]=np.full_like(fields["gold"],amount)
    return replace(state,stage="gilded",fields=fields,process_json=_record(state,gilding=amount))


def surface(state,*,damage=0.,tarnish=0.):
    if state.schema_version>=4:
        if state.stage not in FINAL_STAGES:raise ValueError('Surface wear follows fixing or an explicit snapshot')
        from film_foundry.core.sheet_common import change
        return change(state,'surface',plate_gold.attrition(state,abrasion=damage,tarnish=tarnish),
                      damage=plate_gold.record(damage),tarnish=plate_gold.record(tarnish))
    if state.stage not in ("fixed","gilded"):raise ValueError("Surface ageing requires a fixed plate")
    fields=dict(state.fields)
    for key,value in (("damage",damage),("tarnish",tarnish)):
        array=np.broadcast_to(np.asarray(value,dtype=np.float32),fields[key].shape)
        if not np.isfinite(array).all() or (array<0).any() or (array>1).any():raise ValueError("Surface fractions must be in [0,1]")
        fields[key]=array
    return replace(state,fields=fields)


def weather(state,*,scratches=0.,pitting=0.,edge_tarnish=0.):
    """Accumulate local surface wear; viewing conditions never call this operation."""
    if state.stage not in (("fixed","gilded","finished") if state.schema_version>=4 else ("fixed","gilded")):raise ValueError("Surface wear requires a fixed plate or explicit snapshot")
    scratches=defects.fraction(scratches,"Scratches")
    pitting=defects.fraction(pitting,"Pitting")
    edge_tarnish=defects.fraction(edge_tarnish,"Edge tarnish")
    if not (scratches or pitting or edge_tarnish):return state
    shape=state.fields["particles"].shape
    if state.schema_version>=4:
        if ms.enabled(state.material):
            _,pits,tarnish,abrasion=scaled.wear(shape,state.seed,state.material,scratches,pitting,edge_tarnish,separate=True)
        else:
            abrasion=defects.scratches(shape,state.seed)*scratches
            pits=np.clip((defects.clouds(shape,state.seed,304,cells=39)-.5)*3,0,1)*pitting
            y,x=defects.coordinates(shape);edge=np.minimum(np.minimum(x,1-x),np.minimum(y,1-y))
            variation=defects.clouds(shape,state.seed,305,cells=6)
            tarnish=edge_tarnish*np.exp(-edge/(.02+.24*variation**2))*(.45+.55*defects.clouds(shape,state.seed,306,cells=35))
        from film_foundry.core.sheet_common import change
        return change(state,'weather',plate_gold.attrition(state,abrasion,pits,tarnish),
                      scratches=scratches,pitting=pitting,edge_tarnish=edge_tarnish)
    if state.schema_version>=2 and ms.enabled(state.material):
        loss,pits,tarnish=scaled.wear(shape,state.seed,state.material,scratches,pitting,edge_tarnish)
        fields=dict(state.fields);fields['damage']=1-(1-fields['damage'])*(1-loss)
        if pitting:fields['pit_depth']=1-(1-fields['pit_depth'])*(1-pits)
        if edge_tarnish:fields['tarnish']=1-(1-fields['tarnish'])*(1-tarnish)
        return replace(state,fields=fields,process_json=defects.record_event(state.process_json,'weather',scratches=scratches,pitting=pitting,edge_tarnish=edge_tarnish))
    loss=np.zeros(shape,np.float32)
    if scratches:loss=defects.scratches(shape,state.seed)*scratches
    if pitting:
        pits=(np.clip((defects.clouds(shape,state.seed,304,cells=39)-.5)*3,0,1) if state.schema_version>=2
              else defects.patches(shape,state.seed,304,count=48,radius=(.004,.025)))*pitting
        loss=1-(1-loss)*(1-pits)
    fields=dict(state.fields);fields["damage"]=1-(1-fields["damage"])*(1-loss)
    if state.schema_version>=2 and pitting:fields["pit_depth"]=1-(1-fields["pit_depth"])*(1-pits)
    if edge_tarnish:
        y,x=defects.coordinates(shape)
        edge=np.minimum(np.minimum(x,1-x),np.minimum(y,1-y))
        variation=(defects.clouds(shape,state.seed,305,cells=6) if state.schema_version>=2
                   else defects.patches(shape,state.seed,305,count=12,radius=(.06,.2)))
        if state.schema_version>=2:
            fine=defects.clouds(shape,state.seed,306,cells=35)
            tarnish=edge_tarnish*np.exp(-edge/(.02+.24*variation**2))*(.45+.55*fine)
        else:tarnish=edge_tarnish*np.exp(-edge/(.015+.10*variation))*(.4+.6*variation)
        fields["tarnish"]=1-(1-fields["tarnish"])*(1-tarnish)
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,"weather",
        scratches=scratches,pitting=pitting,edge_tarnish=edge_tarnish))


def _body(state):
    fields=dict(state.fields)
    for name in ("metal_reflectance","particle_reflectance"):
        fields[name]=np.full_like(fields["particles"],getattr(state.material,name))
    model=f"metal_particle_reflection_v{state.schema_version}"
    presentation={}
    if state.schema_version>=4:
        events=json.loads(state.process_json).get('events',[])
        final=next((event for event in reversed(events) if event.get('operation')=='finish'),{})
        presentation={'formation_stage':final.get('formation_stage',state.stage),
                      'completion':final.get('completion','process_stage'),
                      'fixed':bool(np.max(fields['halide'])<=1e-4)}
    return MediumBody(FAMILY,state.schema_version,state.group_id,state.group_id+":plate","silver_plate",model,fields,presentation_json=json.dumps(presentation))


def products(state):
    if state.stage not in FINAL_STAGES:raise ValueError("Fix or explicitly finish before committing a plate")
    body=_body(state)
    return ProductGroup(state.group_id,(body,),json.dumps(dict(family=FAMILY,material=asdict(state.material),
        process=json.loads(state.process_json),seed=state.seed,process_seconds=state.elapsed_seconds,
        group_id=state.group_id,members=[body.body_id]),sort_keys=True))


@cached_body_validation
def validate_body(body):
    model=f"metal_particle_reflection_v{body.schema_version}"
    if body.family!=FAMILY or body.schema_version not in (1,2,3,4) or body.role!="silver_plate" or body.observation_model!=model or body.usable_negative:
        raise ValueError("Unsupported silver plate")
    if set(body.fields)!=_field_names(body.schema_version)|{"metal_reflectance","particle_reflectance"}:raise ValueError("Incomplete plate surface")
    if any(v.ndim!=2 for v in body.fields.values()):raise ValueError("Invalid plate surface")
    _validate_ranges(body.fields,body.schema_version)
    if body.schema_version>=3:baths.validate(body.fields)
    if body.schema_version>=4:plate_gold.validate(body.fields)
    if (body.fields["roughness"]<.02).any():raise ValueError("Invalid roughness")
    if (body.fields["particles"]>body.fields["latent"]+1e-6).any():raise ValueError("Invalid activated surface")


def observe(body,view=ViewingConditions()):
    validate_body(body)
    def vector(value,unit=False):
        v=np.asarray(value,dtype=np.float64)
        if v.shape!=(3,) or not np.isfinite(v).all():raise ValueError("Invalid viewing vector")
        if unit:
            length=np.linalg.norm(v)
            if not math.isfinite(length) or length<=0:raise ValueError("Zero/overflow direction")
            return v/length
        if (v<0).any():raise ValueError("Negative viewing radiance")
        return v
    light=vector(view.illuminant_rgb);background=vector(view.background_rgb)
    l=vector(view.light_direction,True);v=vector(view.view_direction,True)
    if v[2]<=0:raise ValueError("View must face the front of the plate")
    if not math.isfinite(view.exposure_ev) or not -20<=view.exposure_ev<=20:raise ValueError("Invalid viewing exposure")
    f=body.fields
    # Polishing texture changes local normals, not a final image overlay.
    modern=body.schema_version>=2
    nx=(f["polish_x"]-.5)*.10 if modern else np.sin(f["polish_x"]*2*np.pi)*.01
    ny=(f["polish_y"]-.5)*.10 if modern else np.zeros_like(nx)
    normal=np.stack([nx,ny,np.ones_like(nx)],axis=-1)
    normal/=np.linalg.norm(normal,axis=-1,keepdims=True)
    nl=np.maximum(normal @ l,0)
    reflected=2*nl[...,None]*normal-l
    alignment=np.clip(reflected @ v,0,1)
    rough=np.clip(f["roughness"]+f["damage"]*(.12 if modern else .5)+(f["pit_depth"]*.16 if modern else 0),.02,1)
    specular=alignment**np.minimum(2/(rough*rough)-2,5000)*nl
    gold=f["gold"][...,None]
    tint=1-gold*np.array([.02,.08,.22])
    metal=(background+light*specular[...,None])*f["metal_reflectance"][...,None]*tint
    particles=light*nl[...,None]*f["particle_reflectance"][...,None]*(1-gold*.08)
    if modern:
        grain=f["microtexture"][...,None]
        # Fine surface variation changes scattering, not the stored image particles.
        particles*=.96+.08*grain
        damaged=f["damage"][...,None]
        metal=metal*(1-.65*damaged)+light*(.018*damaged+.012*f["pit_depth"][...,None])
    coverage=f['particles'] if body.schema_version>=4 else f["particles"]*(1-f["damage"])
    result=metal*(1-coverage[...,None])+particles*coverage[...,None]
    if body.schema_version>=3:
        salt=f["halide"][...,None]
        result=result*np.exp(-salt*np.array([.15,.25,.7]))+light*.08*(-np.expm1(-2*salt))
    if modern:
        thickness=f["tarnish"][...,None]
        strength=-np.expm1(-thickness*7)
        # RGB thin-layer interference proxy; not measured silver-sulfide optical constants.
        phase=4*np.pi*1.8*(thickness*320)*max(v[2],.1)/np.array([650.,535.,460.])
        interference=1-.62*strength*(.5+.5*np.cos(phase))
        absorption=np.exp(-thickness*np.array([1.1,1.35,1.7]))
        result*=interference*absorption
    else:result*=1-f["tarnish"][...,None]*np.array([.65,.75,.85])
    return (result*2**view.exposure_ev).astype(np.float32)


def preview(state,view=ViewingConditions()):return observe(_body(state),view)


class SilverPlateFamily:
    expose=staticmethod(expose)
    finish=staticmethod(products)
    observe=staticmethod(observe)
    @staticmethod
    def process(state,operation,**parameters):
        if operation not in OPERATION_FUNCTIONS:raise ValueError("Unsupported plate operation")
        return OPERATION_FUNCTIONS[operation](state,**parameters)


def load_gilding(state,*,gold_load=.5,coverage=1.):
    if state.schema_version<4:raise ValueError('Finite gold inventory requires v4')
    return plate_gold.load(state,gold_load=gold_load,coverage=coverage)


def rinse_gilding(state,*,strength=1.):
    if state.schema_version<4:raise ValueError('Finite gold inventory requires v4')
    return plate_gold.rinse(state,strength=strength)


def finish(state):
    if state.schema_version<4:raise ValueError('Explicit final snapshot requires v4')
    return plate_gold.finish(state)


OPERATION_FUNCTIONS={'polish':polish,'sensitize':sensitize,'expose':expose_surface,'develop':develop,'fix':fix,'gild':gild,'surface':surface,'light_leak':light_leak,'weather':weather,'load_gilding':load_gilding,'rinse_gilding':rinse_gilding,'finish':finish}
FINAL_STAGES=('fixed','gilded','finished')
EXPOSURE_STEPS=('prepare','polish','sensitize','expose')


def available_operations(state):
    if state is not None and state.schema_version>=4:
        if state.stage=='finished':return ('surface','weather')
        if state.stage=='developed':return ('develop','fix','finish')
        if state.stage=='fixing':return ('fix','finish')
        if state.stage in ('fixed','gilding','gilded'):
            return (('fix',) if state.stage=='fixed' else ())+('load_gilding','gild','rinse_gilding','finish')+(('surface','weather') if state.stage!='gilding' else ())
    if state is not None and state.schema_version==3:
        if state.stage=='fixing':return ('fix',)
        if state.stage=='gilding':return ('gild',)
        if state.stage=='fixed':return ('fix','gild','surface','weather')
        if state.stage=='gilded':return ('gild','surface','weather')
    return {None:('prepare',),'blank':('polish',),'polished':('sensitize',),'sensitized':('expose',),
            'exposed':('develop','light_leak'),'developed':('develop','fix'),
            'fixed':('gild','surface','weather'),'gilded':('surface','weather')}[state.stage if state else None]
