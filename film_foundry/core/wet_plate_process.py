"""Wet-collodion access, surface transport and finite silver intensification.

Explicit process time, normalized equivalents, no image-space recoloring.
Rewetting does not undo the collodion's drying-induced loss of permeability.
"""
import math
import numpy as np
from film_foundry.core import sheet_common as s
from film_foundry.core.material_parallel import run_rows

SILVER_FIELDS=('enhancement_silver','enhancement_deposit','enhancement_removed','enhancement_supplied')
FIELDS={'collodion_access':0,**{key:0 for key in SILVER_FIELDS}}
LIMITS={key:None for key in SILVER_FIELDS}
_TURNS = {'down': 0, 'right': 3, 'up': 2, 'left': 1}


def _edge_profile(width, pitch, scale, seed, edge):
    """One reproducible contact line in material millimetres at every working size."""
    x = (np.arange(width) + .5) * pitch
    rng = np.random.default_rng(np.random.SeedSequence([seed, 4127, edge]))
    result = np.zeros(width)
    for spacing, amplitude in ((scale, .65), (scale*.32, .35)):
        u = x / spacing
        index = u.astype(int)
        t = u - index
        t = t*t*(3 - 2*t)
        # Use the physical width, not the last pixel centre, to size the lattice.
        samples = rng.random(math.ceil(width * pitch / spacing) + 2)
        result += amplitude * (samples[index] * (1-t) + samples[index+1] * t)
    return result


def recede_film(state, seconds, direction=None, mobility_scale=1.):
    """Move unset film from the contact line into an adjacent rim; spill only at the drain."""
    m = state.material
    # Integrate an exponentially setting film over this interval. The clock stops
    # mattering once the silver bath starts, so loaded silver never moves.
    gel_time = .12 * m.drying_seconds
    mobility = mobility_scale * gel_time * math.exp(-state.elapsed_seconds / gel_time) * -math.expm1(-seconds / gel_time)
    coat = state.fields['coating'].copy()
    turns = _TURNS[direction or m.pour_direction]
    face = np.rot90(coat, turns)
    flow = np.rot90(state.fields['flow'], turns)
    pitch = m.material_long_edge_mm / max(coat.shape)
    reach = min(.30 * m.process_scale_mm, face.shape[0] * pitch / 12)
    depth = min(face.shape[0] // 2, math.ceil(5 * reach / pitch))
    runoff = 0.
    if depth < 2 or mobility == 0:
        return coat, runoff
    distance = (np.arange(depth, dtype=np.float64)[:, None] + .5) * pitch
    for drain in (False, True):
        strip = (face[::-1] if drain else face)[:depth]
        texture = (flow[::-1] if drain else flow)[:depth]
        boundary = _edge_profile(face.shape[1], pitch, m.process_scale_mm, state.seed, (turns + 2*drain) % 4)
        if turns >= 2:
            boundary = boundary[::-1]
        front = reach * (.12 + 1.65 * boundary + .28 * texture)
        # Pixel-area coverage at a curved boundary, including subpixel edges.
        edge = np.clip((front - distance) / pitch + .5, 0, 1)
        rim = (edge == 0) & (distance <= front + reach * (.8 + 1.2 * boundary))
        room = (1. - strip) * rim
        available = room.sum(axis=0, dtype=np.float64)
        release = np.minimum(strip, edge * (m.film_retraction * mobility / 9))
        requested = release.sum(axis=0)
        # At the open edge only the pinned share remains in the meniscus.
        retained = .08 + .5*m.edge_pooling if drain else 1.
        deposit = np.minimum(requested * retained, available)
        if drain:
            runoff += (requested - deposit).sum()
        else:
            release *= np.divide(deposit, requested, out=np.zeros_like(deposit), where=requested > 0)
        share = np.divide(deposit, available, out=np.zeros_like(deposit), where=available > 0)
        strip[:] = np.clip(strip - release + room * share, 0, 1)
    return coat, float(runoff / coat.size)


def _swept(values, travel):
    """Downhill arrivals from a linear depth-velocity profile. Excludes fluid staying in its cell."""
    n = int(travel)
    r = travel - n
    swept = np.zeros_like(values, dtype=np.float64)
    if n > 1:
        prefix = np.cumsum(values, axis=0, dtype=np.float64)
        swept[1:] = prefix[:-1]
        swept[n:] -= prefix[:-n]
        swept /= travel
    if n == 0:
        swept[1:] = .5 * travel * values[:-1]
        return swept
    tails = ((n, (.5+r-.5*r*r)/travel), (n+1, .5*r*r/travel))
    for offset, weight in tails:
        if 0 < offset < len(values) and weight:
            swept[offset:] += weight * values[:-offset]
    return swept


def _transport(source, wet, travel):
    # Forward distribution and its transpose account for each donor, destination,
    # and open boundary. Column blocks bound temporary storage on full plates.
    h, w = source.shape
    arrivals, departures, runoff = (np.empty_like(source) for _ in range(3))
    leaving = travel/2 if travel < 1 else 1-.5/travel
    spill = np.clip(leaving - _swept(np.ones((h, 1)), travel)[::-1], 0, 1)
    block = max(1, 131072 // h)
    for x in range(0, w, block):
        cols = slice(x,x+block)
        silver = source[:,cols]
        mobile = silver * wet[:,cols] * .8
        offered = _swept(mobile, travel)
        received = np.minimum(offered, np.maximum(0., 1.-silver))
        accepted = np.divide(received, offered, out=np.zeros_like(offered), where=offered>0)
        departures[:,cols] = mobile * _swept(accepted[::-1], travel)[::-1]
        arrivals[:,cols] = received
        runoff[:,cols] = mobile * spill
    return arrivals, departures, runoff


def tilt(state, *, direction='down', tilt_degrees=35., seconds=6., motion_mm=0., temperature_c=20.):
    """Hold or move a wet plate. An impulse is expressed as equivalent liquid travel in mm."""
    s.require(state, 'coated', 'sensitized', 'exposed')
    if state.schema_version < 5 or direction not in ('up','down','left','right'):
        raise ValueError('Directional handling requires a v5 plate and a drain direction')
    slope = math.sin(math.radians(s.number(tilt_degrees, 'tilt_degrees', 0, 90)))
    impulse = s.number(motion_mm, 'motion_mm', 0, 100)
    if impulse and seconds == 0:
        raise ValueError('A movement requires a positive duration')
    f, _ = wet_interval(state, seconds, temperature_c=temperature_c)
    record = dict(direction=direction, tilt_degrees=tilt_degrees, motion_mm=motion_mm, temperature_c=temperature_c)
    if state.stage == 'coated':
        # Gravity and a start/stop movement accelerate the still-mobile front.
        # Flat, undisturbed holding follows the same path as waiting.
        if state.material.film_retraction:
            scale = 1 + slope + impulse / state.material.process_scale_mm
            f['coating'], record['drained_coat_mean'] = recede_film(
                state, seconds, direction if slope or impulse else state.material.pour_direction, scale)
    else:
        turns = _TURNS[direction]
        source = np.rot90(state.fields['external_silver'], turns)
        # Trapped solution stays in the coating. Only a wet surface fraction moves.
        pitch = state.material.material_long_edge_mm / max(source.shape)
        drying = (1-.9*state.material.humidity)*2**((temperature_c-20)/10)/state.material.drying_seconds
        mobile_seconds = -math.expm1(-drying*seconds)/drying
        distance = min(source.shape[0], (slope * mobile_seconds * .8 + impulse) / pitch)
        if distance > 0:
            wet = np.rot90(state.fields['moisture'], turns)
            incoming, outgoing, drained = _transport(source, wet, distance)
            def face(array):return np.rot90(array, -turns)
            f['external_silver'] = face(np.maximum(0., source - outgoing - drained) + incoming)
            f['external_removed'] = f['external_removed'] + face(drained)
            f['surface_allocation'] = np.maximum(0., f['surface_allocation'] - face(outgoing)) + face(incoming)
            record['drained_silver_mean'] = float(drained.mean(dtype=np.float64))
    return s.change(state, 'tilt', f, seconds=seconds, **record)


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
    air_only = np.ndim(coverage) == 0 and float(coverage) == 0.
    def rows(start,stop):
        c=np.asarray(contact[start:stop],np.float64)
        w=np.asarray(state.fields['moisture'][start:stop],np.float64)
        p=np.asarray(state.fields['collodion_access'][start:stop],np.float64)
        drying=(1-c)*(1-.9*humidity)*thermal/m.drying_seconds*(2-state.fields['coating'][start:stop])
        if air_only:
            f['moisture'][start:stop] = w*np.exp(-drying*seconds)
            f['collodion_access'][start:stop] = p*np.exp(-(drying*m.drying_lock_strength)*seconds)
            return
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
