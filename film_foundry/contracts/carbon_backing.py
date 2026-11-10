"""Opt-in carbon backing; separate from the legacy generic auxiliary pool.

Creative response coefficients, not a measured stock or a chemical recipe.
"""
import math

MODEL = 'carbon_backing_v1'
MODEL_V2 = 'carbon_backing_v2'
DEFAULTS = {'model': MODEL, 'initial_mg_m2': 40.,
            'softening_l_g_s': .04, 'detachment_rate_s': .08,
            'redeposition_rate_s': .04, 'front_removal_rate_s': .04,
            'density_per_mg_m2': .02}
DEFAULTS_V2 = {**DEFAULTS, 'model': MODEL_V2}
RANGES = {'initial_mg_m2': (0., 1000.), 'softening_l_g_s': (0., 1.),
          'detachment_rate_s': (0., 1.), 'redeposition_rate_s': (0., 1.),
          'front_removal_rate_s': (0., 1.), 'density_per_mg_m2': (0., 10.)}
FIELDS = ('backing_bound_mg_m2', 'backing_loose_mg_m2',
          'backing_front_mg_m2', 'backing_removed_mg_m2', 'backing_softening')


def validate_carbon_backing(raw):
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw)-set(DEFAULTS):
        raise ValueError('Unknown carbon backing parameters')
    value = {**DEFAULTS, **raw}
    if value['model'] not in (MODEL, MODEL_V2):
        raise ValueError('Unsupported carbon backing model')
    for name, (low, high) in RANGES.items():
        number = value[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not low <= number <= high:
            raise ValueError(f'{name} must be finite and between {low} and {high}')
        value[name] = float(number)
    return value
