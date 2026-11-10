"""Single-window reciprocity loss before the material latent-response curve."""
import math
import numpy as np
from film_foundry.contracts.film_exposure import (
    validate_duration, validate_response, conditions_record,
)


def layer_efficiency(response, duration_seconds, layers):
    duration = validate_duration(duration_seconds)
    response = validate_response(response)
    if layers not in (1, 3):
        raise ValueError('Film reciprocity requires one or three layers')
    if duration is None or response is None:
        return None
    if duration < response['short_limit_seconds']:
        stops = math.log2(response['short_limit_seconds'] / duration)
        slopes = response['short_loss_per_stop']
    elif duration > response['long_limit_seconds']:
        stops = math.log2(duration / response['long_limit_seconds'])
        slopes = response['long_loss_per_stop']
    else:
        return None
    slopes = np.asarray(slopes, dtype=np.float64)
    if layers == 1:
        slopes = np.array([slopes.mean()])
    if not np.any(slopes):
        return None
    return np.exp2(-stops * slopes).astype(np.float32)


def exposure_record(film, duration_seconds, layers):
    record = conditions_record(duration_seconds)
    if record is None:
        return None
    efficiency = layer_efficiency(film.reciprocity_response, duration_seconds, layers)
    return {**record, 'reciprocity_response': validate_response(film.reciprocity_response),
            'layer_efficiency': [1.] * layers if efficiency is None else efficiency.tolist(),
            'applied': efficiency is not None,
            'scope': 'single_uniform_window_all_pre_latent_dose',
            'stage': 'before_latent_curve', 'observation': 'readonly_final_medium'}


def duration_for_loss(response, loss_stops, *, branch='long'):
    """Resolve a desired mean-layer speed loss to the same explicit clock.

    Linear target is mean loss in stops, not displayed RGB or negative density.
    This does not compensate input exposure or change any chemistry setting.
    """
    from film_foundry.contracts.film_time import number
    target = number(loss_stops, 0., 32., 'reciprocity loss stops')
    response = validate_response(response)
    if response is None:
        raise ValueError('Enable a material reciprocity response first')
    if branch not in ('short', 'long'):
        raise ValueError('Reciprocity branch must be short or long')
    slope = float(np.mean(response[branch + '_loss_per_stop']))
    if target > 0. and slope == 0.:
        raise ValueError('This material cannot produce the requested reciprocity loss')
    log_seconds = math.log2(response[branch + '_limit_seconds'])
    if target:
        log_seconds += (1. if branch == 'long' else -1.) * target / slope
    if not math.log2(1e-9) <= log_seconds <= math.log2(86400.):
        raise ValueError('Requested loss is outside the supported exposure duration range')
    return validate_duration(2. ** log_seconds)
