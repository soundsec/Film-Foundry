"""Version-3 finite transfer under piecewise constant conditions.

Analytic competing rates, at most two spatial intervals (before/after reagent
arrival). No wall-clock loop or repeated rendering. Rates are creative proxies.
"""
from dataclasses import replace
import json
import math
import numpy as np
from film_foundry.core import material_defects as defects

FIELDS={'transfer_loss','inhibitor'}
RESIDUE_RGB=np.array([.25,.65,1.1],np.float32)


def validate(fields,shape):
    if fields['transfer_loss'].shape!=shape or fields['inhibitor'].shape!=shape[:2]:
        raise ValueError('Invalid segmented transfer fields')
    if any((fields[k]<0).any() or (fields[k]>1).any() for k in FIELDS):
        raise ValueError('Invalid segmented material amount')


def conditions(*,temperature_c=20.,light_protection=1.,ambient_light=1.,solar_warming=0.):
    for name,value,low,high in (('temperature_c',temperature_c,-10,50),('light_protection',light_protection,0,1),
                              ('ambient_light',ambient_light,0,10),('solar_warming',solar_warming,0,30)):
        if not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid environment '+name)
    return dict(temperature_c=min(50.,temperature_c+solar_warming),initial_temperature_c=temperature_c,
                light_protection=light_protection,ambient_light=ambient_light,solar_warming=solar_warming)


def environment(state,*,temperature_c=20.,light_protection=1.,ambient_light=1.,solar_warming=0.):
    if state.schema_version not in (3,4,5) or state.stage!='contact':raise ValueError('Segmented environment requires a reacting v3 material')
    values=conditions(temperature_c=temperature_c,light_protection=light_protection,
                      ambient_light=ambient_light,solar_warming=solar_warming)
    snapshot=json.dumps({**json.loads(state.process_json),**values},allow_nan=False)
    return replace(state,process_json=defects.record_event(snapshot,'environment',at_seconds=state.elapsed_seconds,**values))


def contamination_map(shape,seed,amount):
    if not amount:return np.zeros(shape,np.float32)
    return np.asarray(amount*np.clip(defects.clouds(shape,seed,531,cells=11)*1.5-.2,0,1),np.float32)


def contaminate(state,*,amount=0.,position_x=.5,position_y=.5,radius=.2):
    if state.schema_version not in (3,4,5) or state.stage!='exposed':raise ValueError('Contaminate the accessible v3 sheet before contact')
    for name,value in (('amount',amount),('position_x',position_x),('position_y',position_y)):defects.fraction(value,name)
    if not math.isfinite(radius) or not .01<=radius<=1:raise ValueError('Invalid contamination radius')
    if amount==0:return state
    y,x=defects.coordinates(state.fields['coverage'].shape)
    mask=np.exp(-((x-position_x)**2+(y-position_y)**2)/(2*radius**2)).astype(np.float32)
    fields=dict(state.fields);before=fields['inhibitor']
    fields['inhibitor']=1-(1-before)*(1-amount*mask)
    fields['stain_density']=fields['stain_density']+(fields['inhibitor']-before)[...,None]*RESIDUE_RGB
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,'contaminate',
        amount=amount,position_x=position_x,position_y=position_y,radius=radius))


def clean(state,*,strength=1.):
    if state.schema_version not in (3,4,5) or state.stage not in ('exposed','separated','cleared'):
        raise ValueError('Clean only accessible v3 surfaces before contact or after separation')
    defects.fraction(strength,'Cleaning strength')
    if strength==0:return state
    fields=dict(state.fields)
    removed=fields['inhibitor']*strength
    fields['inhibitor']=fields['inhibitor']-removed
    fields['stain_density']=np.maximum(fields['stain_density']-removed[...,None]*RESIDUE_RGB,0)
    # Neither prior exposure nor transferred/lost image-forming material returns.
    return replace(state,fields=fields,process_json=defects.record_event(state.process_json,'clean',strength=strength))


def _interval(fields,seconds,rate,light,yield_fraction,stain):
    dt=seconds[...,None]
    total_rate=rate+light
    survival=np.exp(-total_rate*dt)
    reacted=fields['remaining']*(-np.expm1(-total_rate*dt))
    share=np.divide(rate,total_rate,out=np.zeros_like(total_rate),where=total_rate>0)
    chemical=reacted*share;fogged=reacted-chemical
    retained=fields['remaining']-reacted
    developed_survival=np.exp(-rate*dt) if np.any(light) else survival
    # Exact solution for incoming fog centres and their subsequent development.
    undeveloped=(fields['immobile']-fields['developed_donor'])*developed_survival
    undeveloped+=fields['remaining']*(developed_survival-survival)
    immobile=fields['immobile']+fogged
    fields.update(remaining=retained,immobile=immobile,exposure_response=immobile,
        received=fields['received']+chemical*yield_fraction,
        transfer_loss=fields['transfer_loss']+chemical*(1-yield_fraction),
        developed_donor=np.clip(immobile-undeveloped,0,immobile))
    if np.any(stain):fields['stain_density']=fields['stain_density']+chemical.mean(axis=-1)[...,None]*stain


def advance(state,seconds):
    if not math.isfinite(seconds) or seconds<0 or not math.isfinite(state.elapsed_seconds+seconds):raise ValueError('Invalid time increment')
    if seconds==0:return state
    settings=json.loads(state.process_json);temperature=settings['temperature_c'];material=state.material
    warm=np.clip((temperature-20)/20,0,1)*material.temperature_sensitivity
    cold=np.clip((20-temperature)/20,0,1)*material.temperature_sensitivity
    fields=dict(state.fields);channels=fields['remaining'].shape[-1]
    activity=fields['channel_activity']
    rate=(2**((temperature-20)*material.temperature_sensitivity/10)/material.reaction_seconds)*activity
    rate=rate*(fields['coverage']*fields['contact']*np.exp(-3*material.inhibitor_sensitivity*fields['inhibitor']))[...,None]
    if channels==3:
        yield_fraction=1-warm*np.array([.28,.07,.015],np.float32)-cold*np.array([.015,.06,.27],np.float32)
    else:yield_fraction=1-.15*(warm+cold)
    yield_fraction=np.clip(yield_fraction,0,1)*fields['transfer_yield']
    stain=warm*np.array([0,.08,.13],np.float32)+cold*np.array([.05,.005,0],np.float32)
    illumination=(1-settings.get('light_protection',1.))*settings.get('ambient_light',1.)*material.light_sensitivity*.025
    # Residue attenuates incoming light but also inhibits subsequent chemistry.
    light=0.
    if illumination:
        transmission=10**(-fields['inhibitor'][...,None]*RESIDUE_RGB)
        if channels==1:transmission=transmission.mean(axis=-1,keepdims=True)
        light=np.asarray(illumination*transmission,np.float32)
    before=np.clip(fields['arrival_seconds']-state.elapsed_seconds,0,seconds)
    if illumination and np.any(before):_interval(fields,before,np.zeros_like(rate),light,yield_fraction,np.zeros(3,np.float32))
    after=np.full_like(before,seconds)-before
    protection=(.04+.6*(1-fields['coverage']))[...,None]
    _interval(fields,after,rate,light*protection,yield_fraction,stain)
    snapshot=defects.record_event(state.process_json,'advance',seconds=seconds,
        temperature_c=temperature,light_protection=settings.get('light_protection',1.),ambient_light=settings.get('ambient_light',1.))
    return replace(state,elapsed_seconds=state.elapsed_seconds+seconds,fields=fields,process_json=snapshot)
