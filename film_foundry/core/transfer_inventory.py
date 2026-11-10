"""Finite developer and delayed acid capacity for new transfer materials.

Capacities are normalized equivalents, not pH or measured molar chemistry.
Dark reactions use a closed solution; illuminated competition uses bounded
quadrature in reaction extent. No per-second loop or display-side evolution.
"""
from dataclasses import dataclass, replace
import json
import math
import numpy as np
from film_foundry.core.material_structure import StructureMaterial
from film_foundry.core import transfer_environment as environment, material_defects as defects

FIELDS = {'developer_available', 'developer_used', 'developer_inactive',
          'alkali_remaining', 'alkali_neutralized', 'neutralizer_remaining', 'neutralization_clock'}
RGB_FIELDS = {'developer_available', 'developer_used'}
INITIAL_FIELDS = ('developer_initial', 'alkali_initial', 'neutralizer_initial')
MATERIAL_LIMITS = {'developer_capacity': (1.2, 0., 10.), 'alkali_capacity': (1., 0., 10.),
                   'neutralizer_capacity': (0., 0., 10.),
                   'neutralization_delay_seconds': (120., 0., 86400.),
                   'neutralization_seconds': (60., .1, 86400.)}
_NODES, _WEIGHTS = np.polynomial.legendre.leggauss(12)


@dataclass(frozen=True, kw_only=True)
class ReagentMaterial(StructureMaterial):
    developer_capacity: float = 1.2
    alkali_capacity: float = 1.
    neutralizer_capacity: float = 0.
    neutralization_delay_seconds: float = 120.
    neutralization_seconds: float = 60.

    def __post_init__(self):
        super().__post_init__()
        for key, (_, low, high) in MATERIAL_LIMITS.items():
            value = getattr(self, key)
            if isinstance(value, bool) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{key} must be between {low} and {high}')


def empty(shape):
    return {key: np.zeros(shape if key in RGB_FIELDS else shape[:2], np.float32) for key in FIELDS}


def capacities(fields, material):
    # Carrier outside the effective contact area remains inaccessible. Coverage
    # controls both accessible quantity and kinetics, not a late RGB mask.
    accessible = fields['reagent'] * fields['coverage']
    return (accessible * material.developer_capacity, accessible * material.alkali_capacity,
            fields['coverage'] * material.neutralizer_capacity)


def initialize(fields, material, inactive_fraction):
    developer, alkali, acid = capacities(fields, material)
    fields['developer_inactive'] = developer * inactive_fraction
    fields['developer_available'] = np.broadcast_to(
        (developer-fields['developer_inactive'])[..., None], fields['remaining'].shape).copy()
    fields['alkali_remaining'] = alkali
    fields['neutralizer_remaining'] = acid


def validate(fields, shape, material=None):
    for key in FIELDS:
        expected = shape if key in RGB_FIELDS else shape[:2]
        if fields[key].shape != expected or np.any(fields[key] < 0):
            raise ValueError('Invalid finite reagent field: '+key)
    if material is None:
        for key in INITIAL_FIELDS:
            if fields[key].shape != shape[:2] or np.any(fields[key] < 0):
                raise ValueError('Invalid reagent source: '+key)
        developer, alkali, acid = (fields[key] for key in INITIAL_FIELDS)
    else:
        developer, alkali, acid = capacities(fields, material)
    for actual, expected in (
        (fields['developer_available']+fields['developer_used']+fields['developer_inactive'][..., None], developer[..., None]),
        (fields['alkali_remaining']+fields['alkali_neutralized'], alkali),
        (fields['neutralizer_remaining']+fields['alkali_neutralized'], acid)):
        if not np.allclose(actual, expected, rtol=2e-6, atol=3e-6):
            raise ValueError('Finite reagent inventory is not conserved')
    if not np.allclose(fields['developer_used'], fields['received']+fields['transfer_loss']+fields['developed_donor'],rtol=2e-6,atol=3e-6):
        raise ValueError('Developer use disagrees with formed image material')
    if material is not None and np.any(fields['neutralization_clock']>material.neutralization_delay_seconds+material.neutralization_seconds+1e-3):
        raise ValueError('Neutralization clock exceeds the release window')


def _consumed(substrate, developer, exposure):
    gap = np.abs(substrate-developer)
    y = np.divide(-np.expm1(-gap*exposure), gap, out=np.array(exposure, copy=True), where=gap>0)
    return np.minimum(np.minimum(substrate, developer), substrate*developer*y/(1+np.minimum(substrate, developer)*y))


def _interval(f, dt, rate, light, alkali, acid_rate, yield_fraction, stain, neutralizing=False):
    """Exact finite chemistry under constant or linearly falling alkali."""
    dt = np.asarray(dt, np.float64)[..., None]
    if not np.any(dt):return
    b = np.asarray(alkali, np.float64)[..., None]; acid = np.asarray(acid_rate, np.float64)[..., None]
    wet = np.minimum(dt, np.divide(b, acid, out=np.full_like(b, np.inf), where=acid>0))
    clock = b*wet-.5*acid*wet*wet
    remaining = f['remaining']; immobile = f['immobile']; developed = f['developed_donor']
    substrate = np.maximum(remaining+immobile-developed, 0.)
    developer = f['developer_available']
    consumed = _consumed(substrate, developer, rate*clock)
    fraction = np.divide(consumed, substrate, out=np.zeros_like(consumed), where=substrate>0)
    light_time = np.where(b>0, wet, 0.) if neutralizing else dt
    if np.any(light) and np.any(consumed):
        # Integrate chemical conversion of transferable material. Light changes
        # which pool is developable, but not the total undeveloped substrate.
        gap = np.abs(substrate-developer); least = np.minimum(substrate, developer)
        greatest = np.maximum(substrate, developer); integral = np.zeros_like(consumed)
        for node, weight in zip(_NODES, _WEIGHTS):
            extent = consumed*((node+1)*.5)
            denominator = np.maximum((least-extent)*greatest, 1e-300)
            q = np.divide(np.log1p(extent*gap/denominator), gap,
                          out=extent/denominator, where=gap>1e-12)
            q = np.divide(q, rate, out=np.zeros_like(q), where=rate>0)
            # Invert b*t - acid*t²/2 without cancellation; constant alkali is
            # the acid=0 case of the same expression.
            divisor = b+np.sqrt(np.maximum(b*b-2*acid*q, 0.))
            time = np.divide(2*q, divisor, out=np.zeros_like(q), where=divisor>0)
            integral += weight*np.exp(-light*time)
        transferred = remaining*np.divide(consumed*.5*integral, substrate,
                                           out=np.zeros_like(consumed), where=substrate>0)
        # Acid arrest also closes this model's light-sensitive reaction window.
        # This keeps one long advance consistent with stopping at neutralization.
        retained = remaining*(1-fraction)*np.exp(-light*light_time)
        drop = remaining-retained
        transferred = np.minimum(np.minimum(transferred, consumed), drop)
        fogged = drop-transferred
    else:
        transferred = remaining*fraction
        retained = (remaining-transferred)*np.exp(-light*light_time) if np.any(light) else remaining-transferred
        fogged = remaining-transferred-retained
    f.update(remaining=retained, immobile=immobile+fogged,
             developed_donor=developed+consumed-transferred,
             received=f['received']+transferred*yield_fraction,
             transfer_loss=f['transfer_loss']+transferred*(1-yield_fraction),
             developer_available=np.maximum(developer-consumed, 0.),
             developer_used=f['developer_used']+consumed)
    if np.any(stain): f['stain_density'] += transferred.mean(axis=-1)[..., None]*stain


def advance(state, seconds):
    if not math.isfinite(seconds) or not 0 <= seconds <= 31536000:
        raise ValueError('Finite transfer increments must be within one year')
    if seconds == 0: return state
    settings = json.loads(state.process_json); m = state.material; temperature = settings['temperature_c']
    thermal = 2**((temperature-20)*m.temperature_sensitivity/10)
    warm = np.clip((temperature-20)/20, 0, 1)*m.temperature_sensitivity
    cold = np.clip((20-temperature)/20, 0, 1)*m.temperature_sensitivity
    channels = state.fields['remaining'].shape[-1]
    yields = (1-warm*np.array([.28,.07,.015])-cold*np.array([.015,.06,.27]) if channels==3 else 1-.15*(warm+cold))
    stain = warm*np.array([0,.08,.13])+cold*np.array([.05,.005,0])
    illumination = (1-settings.get('light_protection',1.))*settings.get('ambient_light',1.)*m.light_sensitivity*.025
    changed = ('remaining','immobile','received','developed_donor','transfer_loss','stain_density',
               'developer_available','developer_used','alkali_remaining','alkali_neutralized','neutralizer_remaining','neutralization_clock')
    fields = dict(state.fields)
    for key in changed: fields[key] = np.empty_like(state.fields[key])
    h,w = state.fields['coverage'].shape
    def rows(start, stop):
        f = {key: np.array(state.fields[key][start:stop], np.float64, copy=True) for key in changed}
        original = {key: value[start:stop] for key,value in state.fields.items()}
        coverage = original['coverage']; contact = original['contact']
        rate = thermal/m.reaction_seconds*original['channel_activity']*(coverage*contact*np.exp(-3*m.inhibitor_sensitivity*original['inhibitor']))[...,None]
        yield_fraction = np.clip(yields,0,1)*original['transfer_yield']
        light = illumination*10**(-original['inhibitor'][...,None]*environment.RESIDUE_RGB)
        if channels == 1: light = light.mean(axis=-1,keepdims=True)
        before = np.clip(original['arrival_seconds']-state.elapsed_seconds,0,seconds)
        if illumination and np.any(before):
            _interval(f,before,np.zeros_like(rate),light,f['alkali_remaining'],np.zeros_like(before),yield_fraction,0.)
        duration = seconds-before
        light = light*(.04+.6*(1-coverage))[...,None]
        timer_rate = thermal*contact
        clock = f['neutralization_clock']
        # Three analytic intervals: before acid access, linear acid release,
        # after release. Frame splitting cannot change these material boundaries.
        to_delay = np.divide(np.maximum(m.neutralization_delay_seconds-clock,0),timer_rate,
                             out=np.full_like(clock,np.inf),where=timer_rate>0)
        pre = np.minimum(duration,to_delay)
        _interval(f,pre,rate,light,f['alkali_remaining'],np.zeros_like(pre),yield_fraction,stain,m.neutralizer_capacity>0)
        left = duration-pre; clock = clock+pre*timer_rate
        to_end = np.divide(np.maximum(m.neutralization_delay_seconds+m.neutralization_seconds-clock,0),timer_rate,
                           out=np.full_like(clock,np.inf),where=timer_rate>0)
        release = np.minimum(left,to_end)
        acid_rate = coverage*m.neutralizer_capacity/m.neutralization_seconds*timer_rate
        _interval(f,release,rate,light,f['alkali_remaining'],acid_rate,yield_fraction,stain,m.neutralizer_capacity>0)
        neutralized = np.minimum(f['alkali_remaining'], np.minimum(f['neutralizer_remaining'],acid_rate*release))
        f['alkali_remaining'] -= neutralized; f['alkali_neutralized'] += neutralized; f['neutralizer_remaining'] -= neutralized
        _interval(f,left-release,rate,light,f['alkali_remaining'],np.zeros_like(release),yield_fraction,stain,m.neutralizer_capacity>0)
        f['neutralization_clock'] = np.minimum(clock+(left)*timer_rate,m.neutralization_delay_seconds+m.neutralization_seconds)
        for key in changed: fields[key][start:stop] = f[key]
    from film_foundry.core.material_parallel import run_rows
    run_rows(h,w,rows,chunk_pixels=131072)
    fields['exposure_response'] = fields['immobile']
    snapshot = defects.record_event(state.process_json,'advance',seconds=seconds,temperature_c=temperature,
        light_protection=settings.get('light_protection',1.),ambient_light=settings.get('ambient_light',1.))
    return replace(state,elapsed_seconds=state.elapsed_seconds+seconds,fields=fields,process_json=snapshot)


def optical_progress(fields, material):
    _,initial,_ = capacities(fields,material)
    residual = np.divide(fields['alkali_remaining'],initial,out=np.zeros_like(initial),where=initial>0)
    return np.clip(1-residual,0,1), np.clip(residual,0,1)**2


def budget(fields, material):
    developer,alkali,acid=capacities(fields,material)
    mean=lambda value:float(np.mean(value,dtype=np.float64))
    return {'model':'finite_transfer_reagent_v1','units':'normalized_equivalents_per_layer',
            'developer_accessible':mean(developer),'developer_available':mean(fields['developer_available']),
            'developer_used':mean(fields['developer_used']),'developer_inactive':mean(fields['developer_inactive']),
            'alkali_supplied':mean(alkali),'alkali_remaining':mean(fields['alkali_remaining']),
            'neutralized':mean(fields['alkali_neutralized']),'neutralizer_supplied':mean(acid),
            'neutralizer_remaining':mean(fields['neutralizer_remaining']),
            'carrier_retained':mean(fields['reagent']),'carrier_escaped':mean(fields['escaped_reagent'])}
