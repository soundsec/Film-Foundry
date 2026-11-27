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


def validate_film_side(value):
    if value not in ('emulsion', 'base'):
        raise ValueError("film_side must be emulsion or base")
    return value


def rear_optics(value=None):
    """Geometry of the scene on an emulsion-side-up film coordinate grid."""
    value = {} if value is None else value
    limits = {'thickness_um': (125., 10., 350.), 'index': (1.48, 1., 2.),
              'f_number': (5.6, .7, 128.), 'focus_shift_um': (0., -500., 500.)}
    if not isinstance(value, dict) or set(value) - {*limits, 'mirror', 'profile','auto_refocus'}:
        raise ValueError('Invalid reverse-loading optics')
    mirror = value.get('mirror', 'horizontal')
    profile = value.get('profile', 'generic')
    if mirror not in ('horizontal', 'vertical', 'none') or profile not in ('generic', 'harman_red_125'):
        raise ValueError('Unknown reverse-loading orientation or reference')
    auto_refocus = value.get('auto_refocus',False)
    if not isinstance(auto_refocus,bool):
        raise ValueError('auto_refocus must be boolean')
    return {'mirror': mirror, 'profile': profile, 'auto_refocus': auto_refocus, **{
        k: number(value.get(k, default), low, high, k) for k, (default, low, high) in limits.items()}}


def validate_config(config):
    validate_film_side(config.exposure.film_side)
    if config.exposure.rear_optics is not None:
        rear_optics(config.exposure.rear_optics)
    from film_foundry.contracts.lens_filters import validate_filter
    validate_filter(config.exposure.lens_filter)
    duration = validate_duration(config.exposure.duration_seconds)
    validate_response(config.film.reciprocity_response)
    if duration is not None and config.chemistry.program_key == 'legacy_density':
        raise ValueError('Explicit exposure duration requires a material-pool program')
