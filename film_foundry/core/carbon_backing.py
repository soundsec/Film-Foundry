"""Finite carbon routing on the film's existing bounded surface grid.

One washing event is one batch: mobilize -> fresh-water discharge -> mix the
retained suspension over the contacted area -> attach a fraction to the front.
This is not fluid dynamics, pH mixing, or transport between different frames.
"""
import math
import numpy as np
from film_foundry.contracts.carbon_backing import FIELDS, MODEL_V2, RANGES, validate_carbon_backing


def exposure_return(material):
    """Exposure-time uniform backing absorption, never the post-bath inventory.

    Only the modelled rear return traverses this neutral layer twice. Primary
    exposure, emulsion scatter and piping are separate paths. v1 is an exact
    bypass; this is not a wavelength/angle-resolved model or a stock calibration.
    """
    material = validate_carbon_backing(material)
    if material is None or material['model'] != MODEL_V2:
        return None
    density = material['initial_mg_m2'] * material['density_per_mg_m2']
    return {
        'model': MODEL_V2,
        'initial_mg_m2': material['initial_mg_m2'],
        'density_per_mg_m2': material['density_per_mg_m2'],
        'single_pass_density': density,
        'return_fraction': 10. ** (-2. * density),
        'loss_stops': 2. * density / math.log10(2.),
        'path': 'uniform_neutral_backing_double_pass',
        'scope': 'rear_halation_return_only',
        'later_washing': 'does_not_reexpose_latent',
    }


def initial_mass_for_halation_loss(material, loss_stops):
    """Invert the same double-pass law into a physical recipe input (mg/m²)."""
    material = validate_carbon_backing(material)
    if material is None or material['model'] != MODEL_V2:
        raise ValueError('Enable carbon backing v2 before resolving return loss')
    if isinstance(loss_stops, bool) or not isinstance(loss_stops, (int, float)) or not math.isfinite(loss_stops) or loss_stops < 0:
        raise ValueError('Return loss must be a finite non-negative number of stops')
    if loss_stops == 0:
        return 0.
    coefficient = material['density_per_mg_m2']
    if coefficient <= 0:
        raise ValueError('Zero carbon absorption cannot reduce the return')
    mass = loss_stops * math.log10(2.) / (2. * coefficient)
    if mass > RANGES['initial_mg_m2'][1]:
        raise ValueError('Desired return loss exceeds the allowed carbon load')
    return mass


def initialize(fields, material):
    shape = fields['emulsion_damage'].shape
    fields.update({key: np.zeros(shape, np.float32) for key in FIELDS})
    fields['backing_bound_mg_m2'].fill(material['initial_mg_m2'])


def process(fields, material, event, contact, *, front_owned=None, coverage=None):
    contact = np.clip(contact, 0., 1.)
    duration = event['duration_s']
    thermal = 2**((event['temperature_c']-20.)/20.)
    soft = fields['backing_softening']
    if event['kind'] == 'backing_condition':
        dose = material['softening_l_g_s']*event['alkalinity_g_l']*duration*thermal
        soft += (1-soft)*(-np.expm1(-dose*contact))
        return
    bound, loose, front, removed = (fields[key] for key in FIELDS[:4])
    dose = duration*thermal
    movement = event['agitation']+event['back_rubbing']
    released = bound*(-np.expm1(-material['detachment_rate_s']*dose*movement*
                              (.02+.98*soft)*contact))
    bound -= released
    loose += released
    released = front*(-np.expm1(-material['front_removal_rate_s']*dose*
                                event['front_rubbing']*contact))
    if front_owned is not None:
        front_owned*=1-np.divide(released,front,out=np.zeros_like(front),where=front>0)
    front -= released
    loose += released
    # Fresh water removes mass, not density. With zero time/exchanges there is
    # no discharge. Old detached particles participate in every later wash.
    exchange = -np.expm1(-min(duration*thermal/20., event['water_changes'])*contact)
    discharged = loose*exchange
    loose -= discharged
    removed += discharged
    # Only mobile particles in the contacted area can travel to the front.
    # Normalize by contacted area: spreading cannot create or lose carbon.
    if duration == 0 or event['agitation'] == 0 or not np.any(contact):
        return
    accessible = loose*contact*(-math.expm1(-duration*event['agitation']/10.))
    amount = float(np.mean(accessible, dtype=np.float64))
    if amount <= 0:
        return
    loose -= accessible
    suspension = contact*(amount/float(np.mean(contact, dtype=np.float64)))
    attach = -math.expm1(-material['redeposition_rate_s']*dose*event['emulsion_contact'])
    front += suspension*attach
    if front_owned is not None:front_owned+=suspension*attach*coverage
    loose += suspension*(1-attach)


def ordinary_wash(fields, fraction):
    """A generic rinse flushes loose carbon; it does not dissolve the binder."""
    loose = fields['backing_loose_mg_m2']
    removed = loose*fraction
    loose -= removed
    fields['backing_removed_mg_m2'] += removed


def balance(fields, material):
    result = {'carbon_initial_mg_m2': material['initial_mg_m2']}
    result.update({key: float(np.mean(fields[key], dtype=np.float64)) for key in FIELDS[:4]})
    return result


def optical_mass(fields):
    return fields['backing_bound_mg_m2']+fields['backing_loose_mg_m2']+fields['backing_front_mg_m2']
