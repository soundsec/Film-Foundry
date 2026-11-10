"""Film exposure conditions and optional stock reciprocity response.

Input RGB already represents integrated dose. Duration never multiplies it.
These creative layer coefficients are not calibrated stock measurements.
"""
from film_foundry.contracts.film_time import number

MODEL = 'film_reciprocity_response_v1'
CONDITIONS_MODEL = 'film_exposure_conditions_v1'
SCALARS = {
    'short_limit_seconds': (0.0001, 1e-9, 86400.),
    'long_limit_seconds': (1., 1e-9, 86400.),
}
VECTORS = {
    'short_loss_per_stop': (0.05, 0.05, 0.05),
    'long_loss_per_stop': (0.15, 0.20, 0.25),
}


def validate_duration(value):
    return None if value is None else number(value, 1e-9, 86400., 'exposure duration_seconds')


def validate_response(value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('model') != MODEL or set(value) - {'model', *SCALARS, *VECTORS}:
        raise ValueError('Invalid film reciprocity response')
    result = {'model': MODEL}
    for key, (default, low, high) in SCALARS.items():
        result[key] = number(value.get(key, default), low, high, key)
    if result['short_limit_seconds'] > result['long_limit_seconds']:
        raise ValueError('Reciprocity short limit must not exceed long limit')
    for key, default in VECTORS.items():
        values = value.get(key, default)
        if not isinstance(values, (tuple, list)) or len(values) != 3:
            raise ValueError(f'{key} requires three material-layer values')
        # A slope below one preserves increasing effective dose when exposure
        # is lengthened under constant illumination, even in the long branch.
        result[key] = [number(v, 0., 0.95, key) for v in values]
    return result


def conditions_record(duration):
    duration = validate_duration(duration)
    return None if duration is None else {
        'model': CONDITIONS_MODEL, 'duration_seconds': duration,
        'input': 'integrated_relative_dose',
    }


def duration_from_record(record):
    if record is None:
        return None
    if not isinstance(record, dict) or set(record) != {'model', 'duration_seconds', 'input'}:
        raise ValueError('Invalid film exposure conditions')
    if record['model'] != CONDITIONS_MODEL or record['input'] != 'integrated_relative_dose':
        raise ValueError('Unsupported film exposure conditions')
    duration = validate_duration(record['duration_seconds'])
    if duration is None:
        raise ValueError('Recorded exposure requires a duration')
    return duration


def validate_config(config):
    duration = validate_duration(config.exposure.duration_seconds)
    validate_response(config.film.reciprocity_response)
    if duration is not None and config.chemistry.program_key == 'legacy_density':
        raise ValueError('Explicit exposure duration requires a material-pool program')
