"""Area-preserving horizontal film shear, on primary pools and owned deposits.

Each material row translates by its declared millimetre displacement. Fractional
pixel overlap transports quantities; outside-frame material leaves the working
domain. No wrapping, support motion, compression, folding, or vertical transport.
"""
import numpy as np
from film_foundry.contracts.emulsion_integrity import OWNED_POOLS


def shift_rows(array, pixels):
    """Conservative cell-overlap transport; positive displacement goes right."""
    source=np.asarray(array,np.float32);pixels=np.asarray(pixels,np.float64)
    h,w=source.shape[:2]
    if pixels.shape!=(h,) or not np.isfinite(pixels).all():raise ValueError('One finite shift is required per row')
    if not np.any(pixels):return source.copy()
    out=np.empty_like(source);cols=np.arange(w,dtype=np.int64)[None,:]
    block=max(1,262144//w)
    for start in range(0,h,block):
        stop=min(h,start+block);offset=-pixels[start:stop]
        lower=np.floor(offset).astype(np.int64);fraction=(offset-lower).astype(np.float32)
        left=cols+lower[:,None];right=left+1
        row=np.arange(stop-start)[:,None];part=source[start:stop]
        a=part[row,np.clip(left,0,w-1)];b=part[row,np.clip(right,0,w-1)]
        tail=(1,)*(source.ndim-2)
        wa=((1-fraction[:,None])*((left>=0)&(left<w))).reshape(stop-start,w,*tail)
        wb=(fraction[:,None]*((right>=0)&(right<w))).reshape(stop-start,w,*tail)
        out[start:stop]=a*wa+b*wb
    return out


def _transport_plan(shape,pixels):
    from film_foundry.core.execution_topology import reference_execution_enabled
    if reference_execution_enabled():return lambda field:shift_rows(field,pixels)
    from film_foundry.core.row_transport import RowShiftPlan
    return RowShiftPlan(shape,pixels)


def displacement_mm(fields,material,event,hardening,wet_seconds,hydration_s,extent):
    from film_foundry.core.emulsion_integrity import adhesion_threshold
    c=fields['emulsion_coverage'];h=c.shape[0]
    threshold=adhesion_threshold(fields,material,hardening,wet_seconds,hydration_s)
    stress=np.maximum(event['pressure']-threshold,0.)
    # A coherent row band moves together. Local damage/hardening contributes
    # through its coating-weighted row response, never an arbitrary x warp.
    average=np.divide(np.sum(stress*c,axis=1),np.sum(c,axis=1),out=np.zeros(h,np.float32),where=np.sum(c,axis=1)>0)
    y=((np.arange(h)+.5)/h-event['center_y'])*extent[1]
    if event['band_width_mm']==0:band=np.ones(h)
    else:
        u=np.clip(np.abs(y)/(event['band_width_mm']*.5),0.,1.)
        band=1-u*u*(3-2*u)
    mobility=-np.expm1(-average*material['shear_rate_s']*event['duration_s'])
    return np.asarray(event['travel_mm']*band*mobility,np.float32)


def transport_surface(fields,shift_mm,extent,hardening):
    pixels=shift_mm*fields['emulsion_coverage'].shape[1]/extent[0]
    shift=_transport_plan(fields['emulsion_coverage'].shape,pixels)
    old=fields['emulsion_coverage'].copy();coverage=shift(old)
    outside={}
    for total,owned in OWNED_POOLS.items():
        if total not in fields:continue
        attached=fields[owned];moved=shift(attached)
        # The bare-support share stays at its original coordinates.
        fields[total]-=attached;fields[total]+=moved
        amount=max(0.,float(np.mean(attached,dtype=np.float64)-np.mean(moved,dtype=np.float64)))
        outside[total]=amount
        if total=='backing_front_mg_m2':fields['backing_removed_mg_m2']+=amount
        fields[owned][:]=moved
    for name in ('emulsion_damage',):
        amount=shift(fields[name]*old)
        fields[name][:]=np.divide(amount,coverage,out=np.zeros_like(amount),where=coverage>0)
    if np.ndim(hardening):
        amount=shift(hardening*old)
        hardening=np.divide(amount,coverage,out=np.zeros_like(amount),where=coverage>0)
    fields['emulsion_coverage'][:]=coverage
    fields['emulsion_shift_x_mm']+=shift_mm[:,None]
    return hardening,{'outside_deposits_mg_m2':outside,
        'outside_area_fraction':max(0.,float(np.mean(old,dtype=np.float64)-np.mean(coverage,dtype=np.float64))),
        'shift_mm_range':[float(shift_mm.min()),float(shift_mm.max())]}


def transport_state(state,shift_pixels):
    from film_foundry.core.film_process.emulsion import POOLS
    if not np.any(shift_pixels):return 0.
    before=state.emulsion_coverage
    if before is None:before=np.ones(state.halide.shape[:2],np.float32)
    shift=_transport_plan(before.shape,shift_pixels)
    coverage=shift(before)
    latent=shift(state.halide*state.developability)
    if state.original_developability is not None:
        original=shift(state.original_developability*before[...,None])
        state.original_developability=np.divide(original,coverage[...,None],out=np.zeros_like(original),where=coverage[...,None]>0)
    ledger=dict(state.emulsion_outside or {});silver=0.
    for name in POOLS:
        pool=getattr(state,name)
        if pool is None:continue
        moved=shift(pool)
        outside=max(0.,float(np.sum(pool,dtype=np.float64)-np.sum(moved,dtype=np.float64)))
        ledger[name]=ledger.get(name,0.)+outside
        if name in ('halide','metallic_silver','bleached_halide'):silver+=outside
        setattr(state,name,moved)
    state.developability=np.divide(latent,state.halide,out=np.zeros_like(latent),where=state.halide>0)
    np.clip(state.developability,0.,1.,out=state.developability)
    state.emulsion_coverage=coverage;state.emulsion_outside=ledger
    if state.formation_history is not None:
        state.formation_history={key:shift(value) for key,value in state.formation_history.items()}
    return silver
