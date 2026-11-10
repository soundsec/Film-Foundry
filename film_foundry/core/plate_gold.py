"""Finite local gilding inventory and surface attrition for plate schema 4.

Gold strengthens attachment of image particles; it is not a sulfide barrier.
Amounts and adhesion are creative equivalents, not measured alloy chemistry.
"""
import numpy as np
from film_foundry.core import sheet_common as s
from film_foundry.core.material_parallel import run_rows

UNBOUNDED={'gold_available','gold_removed','gold_supplied'}
FIELDS=UNBOUNDED|{'particles_removed'}


def validate(fields):
    if not np.allclose(fields['gold_available']+fields['gold']+fields['gold_removed'],fields['gold_supplied'],atol=2e-6,rtol=2e-6):
        raise ValueError('Plate gold has no conserved source')
    if np.any(fields['particles']+fields['particles_removed']>fields['latent']+2e-6):
        raise ValueError('Plate image exceeds its developed source')


def contact(value,shape):
    result=np.broadcast_to(np.asarray(value,np.float32),shape)
    if not np.isfinite(result).all() or np.any(result<0) or np.any(result>1):raise ValueError('Contact must be in [0,1]')
    return result


def record(value):
    return float(value) if np.ndim(value)==0 else {'kind':'spatial_fraction','shape':list(np.shape(value))}


def load(state,*,gold_load=.5,coverage=1.):
    s.require(state,'fixed','gilding','gilded');s.number(gold_load,'gold_load')
    f=dict(state.fields);added=contact(coverage,f['gold'].shape)*gold_load*state.material.gold_capacity
    f['gold_available']=f['gold_available']+added;f['gold_supplied']=f['gold_supplied']+added
    return s.change(state,'load_gilding',f,'gilding' if np.any(added) else state.stage,
                    gold_load=gold_load,coverage=record(coverage))


def gild(state,*,amount,seconds,activity,temperature_c,coverage):
    s.require(state,'fixed','gilding','gilded');s.number(amount,'amount')
    s.rate(seconds,state.material.gilding_seconds,activity,temperature_c)
    f=dict(state.fields);shape=f['gold'].shape;c=contact(coverage,shape)
    remaining=np.empty(shape,np.float32);gold=np.empty(shape,np.float32)
    def rows(start,stop):
        a=np.asarray(f['gold_available'][start:stop],np.float64)
        current=np.asarray(f['gold'][start:stop],np.float64)
        gap=np.maximum(amount*(.25+.75*f['particles'][start:stop])-current,0)
        x=seconds/state.material.gilding_seconds*activity*2**((temperature_c-20)/10)*c[start:stop]
        # dD/dx=(A-D)*(gap-D). Integrate the smaller pool to avoid
        # overflow and cancellation, including equal finite reactant pools.
        lo=np.minimum(a,gap);hi=np.maximum(a,gap);delta=hi-lo
        integral=np.divide(-np.expm1(-delta*x),delta,out=np.array(x,dtype=np.float64),where=delta>0)
        converted=lo*(hi*integral)/(1+lo*integral)
        converted=np.minimum(converted,lo)
        remaining[start:stop]=a-converted;gold[start:stop]=current+converted
    run_rows(*shape,rows,chunk_pixels=131072)
    f['gold_available']=remaining;f['gold']=gold
    gap=np.maximum(amount*(.25+.75*f['particles'])-gold,0)
    active=seconds>0 and activity>0 and np.any(c)
    stage=('gilded' if np.max(gap)<=1e-4 else 'gilding') if active else state.stage
    return s.change(state,'gild',f,stage,seconds,amount=amount,activity=activity,
                    temperature_c=temperature_c,coverage=record(coverage))


def rinse(state,*,strength=1.):
    s.require(state,'fixed','gilding','gilded');s.number(strength,'strength')
    f=dict(state.fields);removed=f['gold_available']*strength
    f['gold_available']=f['gold_available']-removed;f['gold_removed']=f['gold_removed']+removed
    return s.change(state,'rinse_gilding',f,strength=strength)


def attrition(state,abrasion=0.,pits=0.,tarnish=0.):
    """Lost particles leave the image pool. Observer must not remove them twice."""
    f=dict(state.fields);shape=f['gold'].shape
    abrasion=contact(abrasion,shape);pits=contact(pits,shape);tarnish=contact(tarnish,shape)
    raw=abrasion+pits-abrasion*pits
    lost_gold=f['gold']*raw;retained_gold=f['gold']-lost_gold
    # Strong abrasion strips the binder as well. Pitting bypasses attachment;
    # gold does not prevent the silver substrate from corroding.
    mechanical=abrasion*(1-state.material.gold_adhesion*retained_gold)
    lost_particles=f['particles']*(mechanical+pits-mechanical*pits)
    lost_salt=f['halide']*raw
    f.update(gold=retained_gold,gold_removed=f['gold_removed']+lost_gold,
             particles=f['particles']-lost_particles,particles_removed=f['particles_removed']+lost_particles,
             halide=f['halide']-lost_salt,removed_halide=f['removed_halide']+lost_salt,
             sensitivity=f['halide']-lost_salt,damage=f['damage']+(1-f['damage'])*raw,
             pit_depth=f['pit_depth']+(1-f['pit_depth'])*pits,tarnish=f['tarnish']+(1-f['tarnish'])*tarnish)
    return f


def finish(state):
    s.require(state,'developed','fixing','fixed','gilding','gilded')
    return s.change(state,'finish',stage='finished',formation_stage=state.stage,
                    fixed=bool(np.max(state.fields['halide'])<=1e-4),
                    remaining_gold=float(np.mean(state.fields['gold_available'])),
                    completion='explicit_snapshot_not_chemical_completion')
