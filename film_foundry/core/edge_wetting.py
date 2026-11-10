"""Edge-connected liquid fronts and finite contact-line solute deposits.

Moving rotation uses a cycle-averaged contact rate; a pause exposes a single
free boundary connected to the selected frame edge. No image-space wrap occurs.
This is a reduced contact/drainage model, not a vessel or fluid simulation.
"""
import numpy as np
from .rotary_contact import motion_intervals

MODEL='edge_front_v1'


def front_plan(shape,extent,duration,event):
    width=shape[1];physical_w,physical_h=extent
    x=(np.arange(width,dtype=np.float64)+.5)/width-.5
    rng=np.random.default_rng(int(event.get('rotation_seed',0)) ^ 0x51AE7)
    phases=rng.uniform(0.,2*np.pi,4)
    rough=np.zeros(width,np.float64)
    for frequency,weight,phase in zip((1.7,4.3,10.6,27.1),(.48,.28,.16,.08),phases):
        rough+=weight*np.sin(2*np.pi*frequency*x+phase)
    rough*=event['meniscus_roughness_mm']
    fraction=event['immersion_fraction'];rows=[]
    for begin,end,moving,rpm,shift,angle in motion_intervals(duration,event):
        curve=((1.-fraction+shift-event.get('liquid_level_gradient',0.)*x)*physical_h
               +x*physical_w*np.tan(angle)+rough)
        # Fixed for the entire interval, including partial reaction windows.
        # Fast continuous rotation averages exposure; no per-revolution loop.
        mix=-np.expm1(-(end-begin)*rpm/60.)
        if rpm>0:
            period=60./rpm
            rate=fraction+5./period*(-np.expm1(-(1.-fraction)*period/5.))
        else:rate=0.
        rows.append(np.concatenate(([begin,end,mix,rate],curve)))
    return np.asarray(rows,np.float64)


def wet_coverage(shape,extent,curve,event,start=0,stop=None,*,dtype=np.float64):
    stop=shape[0] if stop is None else stop
    y=((np.arange(start,stop,dtype=dtype)+.5)/shape[0])*extent[1]
    if event['contact_edge']=='top':y=extent[1]-y
    distance=y[:,None]-np.asarray(curve,dtype=dtype)[None,:]
    width=event['meniscus_width_mm']
    wet=np.clip(distance/width+.5,0.,1.)
    from .execution_topology import reference_execution_enabled
    if reference_execution_enabled():
        return wet*wet*(3.-2.*wet),distance
    factor=2.*wet
    np.subtract(3.,factor,out=factor)
    np.multiply(wet,wet,out=wet)
    wet*=factor
    return wet,distance


def contact_duration(shape,extent,start,stop,duration,event,window,*,precision='float64'):
    left,right=(duration*v for v in window);fraction=event['immersion_fraction']
    if fraction in (0.,1.):return np.full((stop-start,shape[1]),(right-left)*fraction,np.float32)
    plan=front_plan(shape,extent,duration,event)
    if precision not in ('float32','float64'):raise ValueError('Unsupported contact precision')
    # Keep very narrow fronts or tiny late reaction windows in double precision.
    # The tiny plan and time schedule always retain their original precision.
    single=(precision=='float32' and max(extent)/event['meniscus_width_mm']<=10000
            and right-left>=max(1.,duration)*1e-4)
    dtype=np.float32 if single else np.float64
    from .execution_topology import reference_execution_enabled
    if not reference_execution_enabled():
        from .desktop_compute import try_contact_plan
        parameters=(left,right,extent[1],event['meniscus_width_mm'],event.get('meniscus_retention_s',0.),
                    1. if event['contact_edge']=='bottom' else -1.)
        accelerated=try_contact_plan('edge_contact_f32' if single else 'edge_contact',shape,start,stop,plan,parameters)
        if accelerated is not None:return accelerated
    result=np.empty((stop-start,shape[1]),np.float32)
    reference=reference_execution_enabled()
    rows=max(1,262144//shape[1])
    for a in range(start,stop,rows):
        b=min(a+rows,stop);activity=np.zeros((b-a,shape[1]),dtype);dose=np.zeros_like(activity)
        for row in plan:
            begin,end,mix,rate=row[:4];end=min(end,right)
            if end<=begin:break
            wet,distance=wet_coverage(shape,extent,row[4:],event,a,b,dtype=dtype)
            target=wet*dtype(1.-mix)+dtype(mix*rate)
            tau=5.+(event.get('meniscus_retention_s',0.)*np.exp(-np.maximum(-distance,0.)/event['meniscus_width_mm']) if mix==0. else 0.)
            if not reference:
                # Reuse private fields, keeping the original operation order
                # and the selected arithmetic. No shortened motion history/grid.
                np.maximum(activity,target,out=activity)
                activity-=target
                before=max(0.,min(end,left)-begin)
                if before:
                    activity*=np.exp(-before/tau)
                    activity+=target
                    activity-=target
                seconds=end-begin-before
                if seconds>0:
                    increment=activity*tau
                    increment*=(-np.expm1(-seconds/tau))
                    increment+=target*seconds
                    dose+=increment
                    activity*=np.exp(-seconds/tau)
                    activity+=target
                else:
                    activity+=target
                continue
            # Wet contact replenishes immediately. The excess liquid activity
            # above the new contact fraction subsequently drains continuously.
            activity=np.maximum(activity,target)
            before=max(0.,min(end,left)-begin)
            if before:activity=target+(activity-target)*np.exp(-before/tau)
            seconds=end-begin-before
            if seconds>0:
                dose+=target*seconds+(activity-target)*tau*(-np.expm1(-seconds/tau))
                activity=target+(activity-target)*np.exp(-seconds/tau)
        result[a-start:b-start]=np.clip(dose,0.,right-left)
    return result


def residue_deposit(shape,extent,duration,event):
    """Redistribute a declared finite solute load toward a pinned contact line.

    Evaporation moves a fraction of the wet-region solute into a diffuse rim;
    it does not add solute. Per-column conservation also handles cropped fronts.
    Subsequent ordinary washing/binding operates on the resulting inventory.
    """
    fraction=event['immersion_fraction']
    load=event['residue_concentration_mg_l']*event['retained_ml_m2']/1000.
    if fraction==0. or load==0.:return np.zeros(shape,np.float32),{'redistributed_mg_m2':0.}
    plan=front_plan(shape,extent,duration,event)
    paused=plan[plan[:,2]==0.]
    row=paused[-1] if len(paused) else plan[-1]
    wet,distance=wet_coverage(shape,extent,row[4:],event)
    if fraction==1.:wet=np.ones(shape,np.float64)
    source=wet*load
    width=event['meniscus_width_mm']
    # A soft broad halo and a narrower accumulation line share the same front.
    weight=.7*np.exp(-.5*(distance/(.45*width))**2)+.3*np.exp(-.5*(distance/(1.8*width))**2)
    x=(np.arange(shape[1])+.5)/shape[1]
    modulation=.7+.3*np.sin(2*np.pi*3.7*x+event.get('rotation_seed',0.))**2
    evaporated=-np.expm1(-event['pause_s']/event['evaporation_time_s'])*modulation
    visible=(row[4:]>0.)&(row[4:]<extent[1])&(fraction<1.)
    evaporated*=visible
    moved=source*evaporated[None,:]
    total=weight.sum(axis=0)
    edge=weight*np.divide(moved.sum(axis=0),total,out=np.zeros_like(total),where=total>0)[None,:]
    # If no represented edge cell exists, leave its finite load in place.
    moved[:,total==0]=0.
    output=(source-moved+edge).astype(np.float32)
    return output,{'model':'contact_line_deposition_v1','supplied_mg_m2':float(np.mean(source)),
        'redistributed_mg_m2':float(np.mean(moved)),
        'evaporation_fraction_mean':float(np.mean(evaporated)),
        'contact_edge':event['contact_edge']}
