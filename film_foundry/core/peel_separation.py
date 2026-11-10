"""V4 peel ownership and cumulative surface work, not a finished-image overlay."""
from dataclasses import replace
import json
import math
import numpy as np
from film_foundry.core import material_defects as defects,material_structure as ms,layer_ownership as layers
from film_foundry.core.material_arrays import freeze_array

OWNERSHIP=('receiver_native','receiver_on_donor','donor_native','donor_on_receiver')
FIELDS=set(OWNERSHIP)|{'donor_gel','receiver_residue','donor_residue'}
MATERIAL_KEYS=('interlayer_adhesion','receiver_cohesion','donor_cohesion','separation_variation')


def validate(fields,shape):
    for key in FIELDS:
        expected=shape+(3,) if key.endswith('_residue') else shape
        if fields[key].shape!=expected or np.any(fields[key]<0):raise ValueError('Invalid separated layer field')
    layers.validate(fields['receiver_native'],fields['receiver_on_donor'])
    layers.validate(fields['donor_native'],fields['donor_on_receiver'])
    if np.any(fields['donor_gel']>1):raise ValueError('Invalid donor cleanup layer')


def separate(state,angle_degrees,speed_mm_s):
    if not math.isfinite(angle_degrees) or not 10<=angle_degrees<=180:raise ValueError('Peel angle must be in [10,180] degrees')
    if not math.isfinite(speed_mm_s) or not .1<=speed_mm_s<=1000:raise ValueError('Peel speed must be in [.1,1000] mm/s')
    f=dict(state.fields);m=state.material;shape=f['coverage'].shape
    zeros=freeze_array(np.zeros(shape,np.float32))
    lifted=zeros;picked=zeros
    temperature=json.loads(state.process_json)['temperature_c']
    rate=1+.35*math.log1p(speed_mm_s/25)
    angle=math.sqrt(math.sin(math.radians(angle_degrees)/2))
    softening=2**((temperature-20)/40)
    load_scale=m.interlayer_adhesion*rate*softening/angle
    max_load=load_scale*(1+m.separation_variation)
    min_cohesion=min(m.receiver_cohesion,m.donor_cohesion)*(1-.5*m.separation_variation)
    # Exact bound: if even maximal load cannot break the weakest possible
    # layer, no random structure or full-image load field is needed.
    if max_load>min_cohesion and np.any(f['contact']*f['coverage']):
        if ms.enabled(m):
            grid=ms.Grid(shape,state.seed,m)
            patch=grid.fit(grid.noise(6101,m.process_scale_mm))
            variation=grid.noise(6111,m.process_scale_mm*.55)
        else:
            patch=defects.clouds(shape,state.seed,6101,cells=9)
            variation=defects.clouds(shape,state.seed,6111,cells=17)
        load=load_scale*f['coverage']*f['contact']
        load*=1+m.separation_variation*2*(patch-.5)
        receiver_strength=m.receiver_cohesion*(1+m.separation_variation*(variation-.5))
        donor_strength=m.donor_cohesion*(1-m.separation_variation*(variation-.5))
        # Separation follows the weaker local layer. Increasing load increases
        # failure there; it must not retroactively give material back by changing
        # the relative weights of two competing failure probabilities.
        receiver_share=np.asarray(receiver_strength<=donor_strength,np.float32)
        strength=np.minimum(receiver_strength,donor_strength)
        if ms.enabled(m):
            # A boundary between failure planes is fractional pixel coverage,
            # not a new hard threshold at each output resolution.
            receiver_share=grid.fit(receiver_share);strength=grid.fit(strength)
        failed=-np.expm1(-np.maximum(load-strength,0)/(strength+.1))
        lifted=failed*receiver_share
        picked=failed*(1-receiver_share)
    intact=freeze_array(np.ones(shape,np.float32))
    f.update(receiver_native=intact if lifted is zeros else 1-lifted,receiver_on_donor=lifted,
             donor_native=intact if picked is zeros else 1-picked,donor_on_receiver=picked,donor_gel=intact)
    # The contacting residue was one optical layer; separate its two shares.
    receiver_share=np.clip(.5+.5*(picked-lifted),0,1)[...,None]
    f['receiver_residue']=f['stain_density']*receiver_share
    f['donor_residue']=f['stain_density']-f['receiver_residue']
    record=defects.record_event(state.process_json,'peel',angle_degrees=angle_degrees,speed_mm_s=speed_mm_s)
    return replace(state,stage='separated',fields=f,process_json=record)


def require_target(state,target):
    if state.schema_version<4 or state.stage not in ('separated','cleared'):
        raise ValueError('Independent sheet processing requires separated v4 material')
    if target not in ('receiver','donor'):raise ValueError('Choose receiver or donor')


def brush(state,marks,angle_degrees):
    defects.fraction(marks,'Brush marks')
    if not math.isfinite(angle_degrees):raise ValueError('Invalid brush direction')
    if not marks:return 0.
    shape=state.fields['coverage'].shape
    if ms.enabled(state.material):
        g=ms.Grid(shape,state.seed,state.material)
        return g.fit(np.clip((g.noise(6121,state.material.texture_scale_mm,10.,angle_degrees)-.25)*2,0,1))
    return defects.brush_field(shape,angle_degrees)


def abrade(fields,target,fraction):
    keys=('receiver_native','donor_on_receiver') if target=='receiver' else ('donor_native','receiver_on_donor')
    for key in keys:fields[key]=layers.remove(fields[key],fraction)


def clean(state,*,target,strength,abrasion,marks,angle_degrees):
    require_target(state,target)
    defects.fraction(strength,'Cleaning strength');defects.fraction(abrasion,'Abrasion')
    pattern=brush(state,marks,angle_degrees)
    f=dict(state.fields);removal=strength*(1-.65*marks*pattern)
    if strength:f[target+'_residue']=f[target+'_residue']*(1-removal if np.ndim(removal)==0 else (1-removal)[...,None])
    if abrasion:abrade(f,target,abrasion*(.35+.65*pattern) if marks else abrasion)
    return replace(state,fields=f,process_json=defects.record_event(state.process_json,'clean',
        target=target,strength=strength,abrasion=abrasion,marks=marks,angle_degrees=angle_degrees))


def clear_negative(state,*,strength,marks,angle_degrees):
    require_target(state,'donor')
    if state.material.donor_mode!='silver_negative':raise ValueError('Material does not declare a recoverable silver negative')
    defects.fraction(strength,'Clearing strength');pattern=brush(state,marks,angle_degrees)
    f=dict(state.fields);removal=strength*(1-.65*marks*pattern)
    if strength:
        f['donor_gel']=f['donor_gel']*(1-removal)
        f['donor_residue']=f['donor_residue']*(1-removal if np.ndim(removal)==0 else (1-removal)[...,None])
    if strength and marks:abrade(f,'donor',.6*strength*marks*pattern)
    return replace(state,stage='cleared',fields=f,process_json=defects.record_event(state.process_json,
        'clear_negative',strength=strength,marks=marks,angle_degrees=angle_degrees))


def image_amounts(fields):
    receiver=fields['received'];donor=fields['immobile']+fields['remaining']+fields['transfer_loss']
    return (layers.retained(receiver,fields['receiver_native'])+layers.retained(donor,fields['donor_on_receiver']),
            layers.retained(donor,fields['donor_native'])+layers.retained(receiver,fields['receiver_on_donor']))
