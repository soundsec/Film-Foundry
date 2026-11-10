"""Rotary wetting, integrated analytically on the bath clock.

Frame rows represent one unwrapped turn. A wet arc replenishes the surface
liquid; outside it, residual reaction access decays exponentially. This is an
effective contact model, not tank geometry, fluid transport or solution capacity.
Whole revolutions are integrated in O(1). Stable rotation only needs row
profiles; instability uses four seeded poses and retains liquid on the material.
Optional axial depth variation tapers contact; a stationary meniscus slows
local drainage. These are reduced wetting laws, not a hydrodynamic solution.
"""
import numpy as np

# Fixed reference for this version, not a measured material constant. Retained
# liquid does not disappear the instant a point rises above the solution.
DRAINAGE_TIME_S=5.
MODEL='rotary_contact_v1'
INSTABILITY_MODEL='rotary_contact_v2'
MENISCUS_MODEL='rotary_contact_v3'
POSE_COUNT=4
MAX_PHASE_SHIFT=.2
MAX_TILT_DEGREES=25.


def _unit_phase(value):
    # Unit-period remainder: subtracting the integer part avoids a general
    # floating-point division/remainder kernel on each full-resolution pose.
    from film_foundry.core.execution_topology import reference_execution_enabled
    return np.mod(value,1.) if reference_execution_enabled() else value-np.floor(value)


def validate_duration(event,duration):
    if event['pause_s']>duration*(1.-event['pause_at_fraction'])+1e-9:
        raise ValueError('停转时长超出本浴剩余时间 / Rotary pause exceeds remaining bath duration')


def _stationary(phase,activity,seconds,fraction,retention=0.):
    wet=phase<fraction
    tau=DRAINAGE_TIME_S
    if retention:
        # Liquid pinned just above the upper wetting boundary drains more
        # slowly during a pause. It only reacts if liquid is already present.
        tau=tau+retention*np.exp(-np.maximum(phase-fraction,0.)/.012)
    decay=np.exp(-seconds/tau)
    dose=np.where(wet,seconds,activity*tau*(-np.expm1(-seconds/tau)))
    return dose,np.where(wet,1.,activity*decay)


def _partial_turn(phase,activity,seconds,fraction,period):
    # This interval never crosses phase 1. Wet contact can therefore only
    # precede a dry interval, so a closed-form integral is sufficient.
    wet=np.minimum(seconds,np.maximum(fraction-phase,0.)*period)
    dry=seconds-wet;start_activity=np.where(wet>0.,1.,activity)
    decay=np.exp(-dry/DRAINAGE_TIME_S)
    return wet+start_activity*DRAINAGE_TIME_S*(-np.expm1(-dry/DRAINAGE_TIME_S)),start_activity*decay


def _rotate(phase,activity,seconds,fraction,rpm,retention=0.):
    if seconds<=0:return np.zeros_like(phase),phase,activity
    if rpm==0:
        dose,activity=_stationary(phase,activity,seconds,fraction,retention)
        return dose,phase,activity
    if np.ndim(fraction) and np.any((fraction<=0.)|(fraction>=1.)):
        # A tapered band can end before reaching one side of the frame.
        # An empty local arc must not replenish liquid once per revolution.
        dose,phase,last=_rotate(phase,activity,seconds,np.clip(fraction,1e-6,1.-1e-6),rpm)
        dose=np.where(fraction<=0.,activity*DRAINAGE_TIME_S*(-np.expm1(-seconds/DRAINAGE_TIME_S)),dose)
        last=np.where(fraction<=0.,activity*np.exp(-seconds/DRAINAGE_TIME_S),last)
        return np.where(fraction>=1.,seconds,dose),phase,np.where(fraction>=1.,1.,last)
    period=60./rpm
    # After at least two complete turns every point has crossed the wet arc.
    # Its terminal liquid activity no longer depends on the entry activity.
    # Keep the original dose arithmetic, but avoid calculating the unused
    # intermediate activity arrays and the repeated zero-origin wetting mask.
    from film_foundry.core.execution_topology import reference_execution_enabled
    if seconds>=2.*period and np.all((fraction>0.)&(fraction<1.)) and not reference_execution_enabled():
        first=(1.-phase)*period
        wet=np.minimum(first,np.maximum(fraction-phase,0.)*period)
        dry=first-wet
        dose=wet+np.where(wet>0.,1.,activity)*DRAINAGE_TIME_S*(-np.expm1(-dry/DRAINAGE_TIME_S))
        remaining=np.maximum(seconds-first,0.)
        turns=np.floor(remaining/period);tail=np.maximum(remaining-turns*period,0.)
        cycle_dry=(1.-fraction)*period
        cycle_dose=fraction*period+DRAINAGE_TIME_S*(-np.expm1(-cycle_dry/DRAINAGE_TIME_S))
        dose+=turns*cycle_dose
        wet=np.minimum(tail,fraction*period);dry=tail-wet
        dose+=wet+DRAINAGE_TIME_S*(-np.expm1(-dry/DRAINAGE_TIME_S))
        activity=np.where(wet>0.,np.exp(-dry/DRAINAGE_TIME_S),np.exp(-cycle_dry/DRAINAGE_TIME_S))
        return dose,_unit_phase(phase+seconds/period),activity
    first=np.minimum(seconds,(1.-phase)*period)
    dose,activity=_partial_turn(phase,activity,first,fraction,period)
    remaining=np.maximum(seconds-first,0.)
    turns=np.floor(remaining/period);tail=np.maximum(remaining-turns*period,0.)
    dry=(1.-fraction)*period
    cycle_dose=fraction*period+DRAINAGE_TIME_S*(-np.expm1(-dry/DRAINAGE_TIME_S))
    dose+=turns*cycle_dose
    activity=np.where(turns>0.,np.exp(-dry/DRAINAGE_TIME_S),activity)
    extra,activity=_partial_turn(np.zeros_like(phase),activity,tail,fraction,period)
    dose+=extra
    return dose,_unit_phase(phase+seconds/period),activity


def cumulative_contact(phases,seconds,duration,event):
    """Effective seconds from bath entry, preserving history across a pause."""
    fraction=event['immersion_fraction']
    if fraction==0.:return np.zeros_like(phases)
    if fraction==1.:return np.full_like(phases,seconds)
    pause_start=duration*event['pause_at_fraction'];pause_end=pause_start+event['pause_s']
    activity=np.zeros_like(phases)
    dose,phase,activity=_rotate(phases,activity,min(seconds,pause_start),fraction,event['rotation_rpm'])
    pause=max(0.,min(seconds,pause_end)-pause_start)
    if pause>0:
        extra,activity=_stationary(phase,activity,pause,fraction);dose+=extra
    extra,phase,activity=_rotate(phase,activity,max(0.,seconds-pause_end),fraction,event['rotation_rpm'])
    return dose+extra


def motion_intervals(duration,event):
    """Small reproducible schedule; pauses freeze pose, not residual reaction.

    Poses advance on the moving part of the bath clock. Their identity is local
    to this operation and never consumes the image/grain random stream.
    """
    pause_start=duration*event['pause_at_fraction'];pause_end=pause_start+event['pause_s']
    moving_duration=duration-event['pause_s'];rpm=event['rotation_rpm']
    count=POSE_COUNT if rpm>0 and moving_duration>0 else 1
    rng=np.random.Generator(np.random.PCG64(event.get('rotation_seed',0)))
    poses=rng.uniform(-1.,1.,(POSE_COUNT,2))*event.get('rotation_instability',0.)
    boundaries={0.,duration,pause_start,pause_end}
    for i in range(1,count):
        moving=moving_duration*i/count
        boundaries.add(moving if moving<pause_start else moving+event['pause_s'])
    boundaries=sorted(boundaries);intervals=[]
    for start,stop in zip(boundaries,boundaries[1:]):
        if stop<=start:continue
        midpoint=(start+stop)*.5
        paused=pause_start<=midpoint<pause_end
        moving=start-np.clip(start-pause_start,0.,event['pause_s'])
        # A pause exactly on a pose boundary retains the preceding pose.
        elapsed=midpoint-np.clip(midpoint-pause_start,0.,event['pause_s'])
        if paused and elapsed>0:elapsed=np.nextafter(elapsed,-np.inf)
        index=min(count-1,int(elapsed/moving_duration*count)) if moving_duration>0 else 0
        shift,tilt=poses[index]
        intervals.append((start,stop,moving,0. if paused else rpm,
                          shift*MAX_PHASE_SHIFT,np.deg2rad(tilt*MAX_TILT_DEGREES)))
    return intervals


def _unstable_contact(shape,start,stop,duration,event,window):
    left,right=(duration*v for v in window)
    fraction=event['immersion_fraction']
    if fraction in (0.,1.):return np.full((stop-start,shape[1]),(right-left)*fraction,np.float32)
    intervals=motion_intervals(duration,event)
    from .execution_topology import reference_execution_enabled
    if not reference_execution_enabled():
        from .desktop_compute import try_rotary_contact
        accelerated=try_rotary_contact(shape,start,stop,duration,event,window,intervals)
        if accelerated is not None:return accelerated
    result=np.empty((stop-start,shape[1]),np.float32)
    # Use full-resolution pixel centres. Bounded scratch arrays, no image warp
    # or low-resolution contact grid. Row splits cannot change the schedule.
    x=((np.arange(shape[1],dtype=np.float64)+.5)/shape[1]-.5)*(shape[1]/shape[0])
    # Axial depth variation changes the separation of the two boundaries,
    # unlike a rigid rotation of an equal-width strip. Preserve dry/full baths.
    gradient=event.get('liquid_level_gradient',0.)
    if gradient:
        fraction=np.clip(fraction+gradient*x[None,:]*(shape[0]/shape[1]),0.,1.)
    block_rows=max(1,262144//shape[1])
    for row in range(start,stop,block_rows):
        end=min(row+block_rows,stop)
        y=((np.arange(row,end,dtype=np.float64)+.5)/shape[0]-.5)[:,None]
        activity=np.zeros((end-row,shape[1]),np.float64);dose=np.zeros_like(activity)
        for begin,finish,moving,rpm,shift,angle in intervals:
            finish=min(finish,right)
            if finish<=begin:break
            phase=_unit_phase(.5+y*np.cos(angle)+x[None,:]*np.sin(angle)+shift+
                              moving*event['rotation_rpm']/60.)
            # Warm up through the original history, even when this call only
            # requests a later coupled-reaction window. Never reset the liquid.
            before=max(0.,min(finish,left)-begin)
            if before>0:
                _,phase,activity=_rotate(phase,activity,before,fraction,rpm,event.get('meniscus_retention_s',0.))
            seconds=finish-begin-before
            if seconds>0:
                extra,phase,activity=_rotate(phase,activity,seconds,fraction,rpm,event.get('meniscus_retention_s',0.))
                dose+=extra
        result[row-start:end-start]=np.clip(dose,0.,right-left)
    return result


def contact_duration(shape,start,stop,duration,event,window=None,*,extent=None,precision='float64'):
    """Shared effective seconds for actual reaction windows and hardening."""
    validate_duration(event,duration)
    left,right=window or (0.,1.)
    if event.get('wetting_model')=='edge_front_v1':
        from .edge_wetting import contact_duration as edge_duration
        if extent is None:
            scale=36./max(shape);extent=(shape[1]*scale,shape[0]*scale)
        return edge_duration(shape,extent,start,stop,duration,event,(left,right),precision=precision)
    if any(event.get(key,0.) for key in ('rotation_instability','liquid_level_gradient','meniscus_retention_s')):
        return _unstable_contact(shape,start,stop,duration,event,(left,right))
    phases=(np.arange(start,stop,dtype=np.float64)+.5)/shape[0]
    dose=cumulative_contact(phases,duration*right,duration,event)
    if left>0:dose-=cumulative_contact(phases,duration*left,duration,event)
    dose=np.clip(dose,0.,duration*(right-left)).astype(np.float32)
    return np.broadcast_to(dose[:,None],(stop-start,shape[1])).copy()
