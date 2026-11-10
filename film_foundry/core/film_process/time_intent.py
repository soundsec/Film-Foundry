"""Resolve linear time-response targets to the existing explicit clocks.

Only bounded layer vectors are evaluated. No scene, random stream, wall clock,
or chemical inventory is sampled. The resulting segment runs normally.
"""
import numpy as np
from film_foundry.contracts.film_time import MODELS, number, validate_history, validate_response
from film_foundry.contracts.film_time_targets import target_contract
from film_foundry.core.film_process.time_history import environment, vector, printout_dose


def _context(response, kind, conditions, layers):
    spec = target_contract(kind)
    if layers not in (1, 3):
        raise ValueError('Film time targets require one or three layers')
    conditions = dict(spec['conditions'] if conditions is None else conditions)
    row = {**conditions, spec['duration_field']: 0.}
    # Do not allow callers to smuggle another duration into the conditions.
    if spec['duration_field'] in conditions:
        raise ValueError('Target conditions must not contain a duration')
    row = validate_history({'model': MODELS[kind], 'segments': [row]}, kind)['segments'][0]
    response = validate_response(response)
    if response is None:
        return spec, row, response, None
    if kind == 'storage':
        rates = vector(response, 'storage_speed_loss_stops_per_year', layers) * environment(row, response) / 365.
    elif kind == 'latent':
        rates = (vector(response, 'latent_fade_rate_per_day', layers) +
                 vector(response, 'latent_fog_rate_per_day', layers)) * environment(row, response) / 24.
    else:
        rates = printout_dose({'segments': [{**row, 'duration_hours': 1.}]}, response, layers)
    return spec, row, response, rates


def _layer_target(kind, rates, duration):
    dose = rates * duration
    return np.minimum(100., dose) if kind == 'storage' else -np.expm1(-dose)


def describe_time_target(response, kind, *, conditions=None, layers=3):
    """Reachability at the selected row's conditions, without altering it."""
    spec, row, response, rates = _context(response, kind, conditions, layers)
    maximum = float(_layer_target(kind, rates, spec['duration_maximum']).mean()) if rates is not None else 0.
    reason = ('material_time_response_disabled' if response is None else
              'zero_response_at_selected_conditions' if maximum == 0. else None)
    ceiling = float(np.count_nonzero(rates) / layers) if rates is not None else 0.
    return {**spec, 'conditions': {k: v for k, v in row.items() if k != spec['duration_field']},
            'layers': layers, 'available': reason is None, 'reason': reason,
            'reachable': {'minimum': 0., 'maximum': maximum,
                          'maximum_inclusive': kind == 'storage' or maximum == 0. or maximum < ceiling}}


def resolve_time_segment(response, kind, target, *, conditions=None, layers=3):
    spec, row, response, rates = _context(response, kind, conditions, layers)
    target = number(target, 0., 100. if kind == 'storage' else 1., 'time response target')
    report = describe_time_target(response, kind, conditions=conditions, layers=layers)
    if response is None:
        raise ValueError('Enable a material time response first')
    maximum = report['reachable']['maximum']
    if target > maximum or (target == maximum and not report['reachable']['maximum_inclusive']):
        raise ValueError(f'Target is outside the reachable range: 0 to {maximum:.8g}')
    duration = 0.
    if target > 0.:
        # Tighten the bracket first so high-rate light exposure retains precision
        # even though the allowed clock spans a century.
        high = spec['duration_maximum']
        low = high / 2.
        while low > 0. and float(_layer_target(kind, rates, low).mean()) >= target:
            high = low
            low /= 2.
        for _ in range(52):
            mid = (low + high) / 2.
            if float(_layer_target(kind, rates, mid).mean()) < target:
                low = mid
            else:
                high = mid
        duration = (low + high) / 2.
    segment = validate_history({'model': MODELS[kind], 'segments': [
        {**row, spec['duration_field']: duration}]}, kind)['segments'][0]
    values = _layer_target(kind, rates, duration)
    linked = {}
    if kind == 'storage':
        years = duration / 365. * environment(row, response)
        linked['storage_fog_fraction'] = (-np.expm1(-vector(response, 'storage_fog_rate_per_year', layers) * years)).tolist()
    elif kind == 'latent':
        fog = vector(response, 'latent_fog_rate_per_day', layers)
        total = fog + vector(response, 'latent_fade_rate_per_day', layers)
        equilibrium = np.divide(fog, total, out=np.zeros_like(fog), where=total > 0.)
        linked['latent_activation_offset'] = (equilibrium * values).tolist()
        linked['latent_original_memory'] = (1. - values).tolist()
    else:
        ratio = response['bleached_printout_ratio']
        linked['bleached_halide_conversion'] = (-np.expm1(-rates * duration * ratio)).tolist()
        # Surface salt uses the RGB mean dose even for a monochrome body.
        if response['model'] == 'film_time_response_v2':
            linked['surface_halide_conversion'] = float(-np.expm1(-printout_dose({'segments': [segment]}, response, 3).mean() * ratio))
    return {'model': report['model'], 'segment': segment, 'capability': report,
            'target': target, 'achieved': float(values.mean()),
            'layer_response': values.tolist(), 'linked_response': linked}
