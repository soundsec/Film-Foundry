"""V4 wet-collodion access and finite silver intensification.

Explicit process time, normalized equivalents, no image-space recoloring.
Rewetting does not undo the collodion's drying-induced loss of permeability.
"""
import numpy as np
from film_foundry.core import sheet_common as s
from film_foundry.core.material_parallel import run_rows

SILVER_FIELDS=('enhancement_silver','enhancement_deposit','enhancement_removed','enhancement_supplied')
FIELDS={'collodion_access':0,**{key:0 for key in SILVER_FIELDS}}
LIMITS={key:None for key in SILVER_FIELDS}


def validate(fields):
    if not np.allclose(fields['enhancement_silver']+fields['enhancement_deposit']+fields['enhancement_removed'],
                       fields['enhancement_supplied'],rtol=2e-6,atol=2e-6):
        raise ValueError('Enhancement silver has no conserved source')


def contact_field(value,shape):
    result=np.broadcast_to(np.asarray(value,np.float32),shape)
    if not np.isfinite(result).all() or np.any(result<0) or np.any(result>1):raise ValueError('Bath contact must be in [0,1]')
    return result


def contact_record(value):
    """Spatial inputs keep a descriptive event; application recipe marks a gap."""
    return float(value) if np.ndim(value)==0 else {'kind':'spatial_fraction','shape':list(np.shape(value))}


def _integral(rate,seconds):
    return np.divide(-np.expm1(-rate*seconds),rate,out=np.full_like(rate,seconds),where=rate>0)


def wet_interval(state,seconds,*,coverage=0.,humidity=None,temperature_c=20.):
    """Return new fields and the exact integral of contact*wetness*access."""
    m=state.material;humidity=m.humidity if humidity is None else humidity
    s.number(seconds,'seconds',0,86400);s.number(humidity,'humidity');s.number(temperature_c,'temperature_c',0,60)
    f=dict(state.fields);shape=f['moisture'].shape;contact=contact_field(coverage,shape)
    effective=np.zeros(shape,np.float32)
    if seconds==0:return f,effective
    f['moisture']=np.empty(shape,np.float32);f['collodion_access']=np.empty(shape,np.float32)
    thermal=2**((temperature_c-20)/10)
    def rows(start,stop):
        c=np.asarray(contact[start:stop],np.float64)
        w=np.asarray(state.fields['moisture'][start:stop],np.float64)
        p=np.asarray(state.fields['collodion_access'][start:stop],np.float64)
        drying=(1-c)*(1-.9*humidity)*thermal/m.drying_seconds*(2-state.fields['coating'][start:stop])
        wetting=c*thermal/m.wetting_seconds
        total=drying+wetting;lock=drying*m.drying_lock_strength
        equilibrium=np.divide(wetting,total,out=np.zeros_like(total),where=total>0)
        f['moisture'][start:stop]=equilibrium+(w-equilibrium)*np.exp(-total*seconds)
        f['collodion_access'][start:stop]=p*np.exp(-lock*seconds)
        effective[start:stop]=np.maximum(c*p*(equilibrium*_integral(lock,seconds)+(w-equilibrium)*_integral(lock+total,seconds)),0)
    run_rows(*shape,rows,chunk_pixels=131072)
    return f,effective


def conversion(effective,tau,activity,temperature_c):
    s.rate(0,tau,activity,temperature_c)
    return -np.expm1(-effective/tau*activity*2**((temperature_c-20)/10))


def load_enhancer(state,*,silver_load=.25,coverage=1.):
    if state.schema_version<4:raise ValueError('Independent intensification requires a v4 wet plate')
    s.require(state,'fixed','washed','enhancing');s.number(silver_load,'silver_load')
    f=dict(state.fields);c=contact_field(coverage,f['coating'].shape)
    added=f['coating']*c*silver_load
    f['enhancement_silver']=f['enhancement_silver']+added
    f['enhancement_supplied']=f['enhancement_supplied']+added
    return s.change(state,'load_enhancer',f,'enhancing' if np.any(added) else state.stage,silver_load=silver_load,coverage=contact_record(coverage))


def intensify(state,*,seconds=60.,activity=1.,temperature_c=20.,coverage=1.,pour_uniformity=1.):
    if state.schema_version<4:raise ValueError('Independent intensification requires a v4 wet plate')
    s.require(state,'enhancing');s.number(pour_uniformity,'pour_uniformity');s.rate(seconds,1,activity,temperature_c)
    c=contact_field(coverage,state.fields['coating'].shape)*(1-(1-pour_uniformity)*state.fields['flow'])
    f,wet_seconds=wet_interval(state,seconds,coverage=c,temperature_c=temperature_c)
    deposited=np.empty_like(f['enhancement_silver']);remaining=np.empty_like(deposited)
    def rows(start,stop):
        available=np.asarray(f['enhancement_silver'][start:stop],np.float64)
        seeds=np.asarray(f['silver'][start:stop],np.float64)+f['external_deposit'][start:stop]+f['enhancement_deposit'][start:stop]
        seeds+=state.material.enhancement_nucleation*f['coating'][start:stop]
        exposure=wet_seconds[start:stop]*activity*2**((temperature_c-20)/10)/state.material.enhancement_seconds
        decay=np.exp(-(available+seeds)*exposure)
        # dA/dt=-k*A*(S+D); deposited silver joins the reaction centres.
        converted=np.divide(available*seeds*(-np.expm1(-(available+seeds)*exposure)),
                            seeds+available*decay,out=np.zeros_like(available),where=(seeds+available*decay)>0)
        converted=np.minimum(available,converted)
        deposited[start:stop]=f['enhancement_deposit'][start:stop]+converted
        remaining[start:stop]=available-converted
    run_rows(*deposited.shape,rows,chunk_pixels=131072)
    f['enhancement_silver']=remaining;f['enhancement_deposit']=deposited
    return s.change(state,'intensify',f,seconds=seconds,activity=activity,temperature_c=temperature_c,coverage=contact_record(coverage),pour_uniformity=pour_uniformity)
