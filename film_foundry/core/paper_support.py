"""Shared reduced paper hydration and reflection, independent of image chemistry.

Moisture is fractional saturation, not a measured volume. Integrated wet access
keeps constant-condition split baths equivalent without per-second image passes.
"""
from film_foundry.core.material_structure import StructureMaterial
from dataclasses import dataclass
import numpy as np
from film_foundry.core import sheet_common as s, sheet_structure as structure

FIELDS={'paper_moisture':0}
LOCAL_DEFAULTS=dict(region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.,temperature_c=20.)


@dataclass(frozen=True,kw_only=True)
class PaperSupportMaterial(StructureMaterial):
    paper_base: str='fiber'
    water_uptake_seconds: float=25.
    drying_seconds: float=600.
    surface_gloss: float=.15
    drydown: float=.06

    def __post_init__(self):
        super().__post_init__()
        if self.paper_base not in ('rc','fiber'):raise ValueError('Unknown paper base')
        s.number(self.water_uptake_seconds,'water_uptake_seconds',.1,7200)
        s.number(self.drying_seconds,'drying_seconds',1,86400)
        s.number(self.surface_gloss,'surface_gloss');s.number(self.drydown,'drydown',0,.4)


def available(state):return 'paper_moisture' in state.fields


def require_new(state):
    if not available(state):raise ValueError('Paper hydration requires a new material version')


def legacy_parameters(**parameters):
    if any(value!=LOCAL_DEFAULTS[key] for key,value in parameters.items()):
        raise ValueError('Local bath parameters require a new material version')


def initial_fields(shape,material,humidity=.5):
    equilibrium=.12*humidity*(.15 if material.paper_base=='rc' else 1.)
    return {'paper_moisture':np.full(shape,equilibrium,np.float32)}


def contact(shape,*,region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.):
    if region not in ('all','spot'):raise ValueError('Unknown bath region')
    for value,name in ((position_x,'position_x'),(position_y,'position_y'),(coverage,'coverage')):s.number(value,name)
    s.number(radius,'radius',.01,2.)
    if region=='all' or coverage==0:return coverage
    h,w=shape;scale=min(h,w)
    x=(np.arange(w,dtype=np.float32)+.5-position_x*w)/scale
    y=(np.arange(h,dtype=np.float32)+.5-position_y*h)/scale
    edge=np.clip((radius*radius-x[None,:]**2-y[:,None]**2)/(.15*radius*radius),0,1)
    return coverage*edge*edge*(3-2*edge)


def wet_fields(state,seconds,*,temperature_c=20.,region='all',position_x=.5,position_y=.5,radius=.25,coverage=1.,integrated=True):
    """Return fields and time-integrated local access for the actual bath."""
    require_new(state);s.number(seconds,'seconds',0,86400);s.number(temperature_c,'temperature_c',0,60)
    f=dict(state.fields);m=state.material;wet=f['paper_moisture']
    access=contact(wet.shape,region=region,position_x=position_x,position_y=position_y,radius=radius,coverage=coverage)
    heat=2**((temperature_c-20)/10)
    tau=m.water_uptake_seconds*(.35 if m.paper_base=='rc' else 1.)/heat
    gain=-np.expm1(-seconds*access/tau)
    f['paper_moisture']=wet+(1-wet)*gain
    if not integrated:return f,None
    # Integral of contact * (.25 + .75 * moisture(t)); valid also at contact=0.
    dose=np.maximum(0,heat*(access*seconds-.75*(1-wet)*tau*gain))
    return f,dose


def dry(state,*,seconds=600.,humidity=.5,temperature_c=20.,airflow=1.):
    require_new(state)
    s.require(state,'washed','oxidized','cleared')
    s.number(humidity,'humidity');s.number(airflow,'airflow',0,100)
    m=state.material;tau=m.drying_seconds*(.2 if m.paper_base=='rc' else 1.)
    fraction=s.rate(seconds,tau,airflow,temperature_c)
    target=.12*humidity*(.15 if m.paper_base=='rc' else 1.)
    f=dict(state.fields);f['paper_moisture']=f['paper_moisture']+(target-f['paper_moisture'])*fraction
    # Chemical endpoint and hydration are orthogonal; drying is not fixing or oxidation.
    return s.change(state,'dry',f,seconds=seconds,humidity=humidity,temperature_c=temperature_c,airflow=airflow)


def observe(diffuse,fields,material,view):
    """Optical dry-down and surface reflection; never changes any substance pool."""
    wet=fields['paper_moisture'][...,None];base=fields['paper'][...,None]
    bulk=.2 if material.paper_base=='rc' else 1.
    exponent=1+material.drydown*bulk*(1-wet)
    relative=np.divide(diffuse,base,out=np.zeros_like(diffuse),where=base>0)
    image=base*np.power(np.clip(relative,0,1),exponent)
    gloss=material.surface_gloss+(1-material.surface_gloss)*.65*wet[...,0]
    weight=.035*gloss
    rough=np.clip(.15+.65*(1-gloss)+material.texture*.08,0,1)
    return image*(1-weight[...,None])+weight[...,None]*structure.view_gloss(view,rough)[...,None]
