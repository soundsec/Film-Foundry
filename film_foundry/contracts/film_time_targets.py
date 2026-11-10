"""UI-neutral targets for one explicit time segment, not persistent effects."""
from copy import deepcopy
from film_foundry.contracts.film_time import DURATION_LIMITS

MODEL = 'film_time_targets_v1'
TARGETS = {
    'storage': {
        'id': 'storage_speed_loss', 'unit': 'mean_layer_stops',
        'stage': 'before_exposure', 'duration_field': 'duration_days',
        'conditions': {'temperature_c': 20., 'relative_humidity': .5},
        'couplings': ['storage_fog', 'layer_speed_balance'],
    },
    'latent': {
        'id': 'latent_memory_loss', 'unit': 'mean_layer_fraction',
        'stage': 'before_first_bath', 'duration_field': 'duration_hours',
        'conditions': {'temperature_c': 20., 'relative_humidity': .5},
        'couplings': ['latent_fog', 'layer_activation_balance'],
    },
    'light': {
        'id': 'residual_halide_conversion', 'unit': 'mean_layer_fraction',
        'stage': 'after_last_bath', 'duration_field': 'duration_hours',
        'conditions': {'relative_illuminance': 1., 'light_rgb': [1., 1., 1.]},
        'couplings': ['retained_salt_only', 'bleached_salt_response',
                      'surface_salt_response', 'silver_grain', 'no_new_dye'],
    },
}


def target_contract(kind):
    if kind not in TARGETS:
        raise ValueError('Unknown film time target')
    return {**deepcopy(TARGETS[kind]), 'model': MODEL, 'kind': kind,
            'duration_maximum': DURATION_LIMITS[kind],
            'scope': 'one_segment_equal_layer_reference',
            'persistence': 'resolved_history_only', 'display_rgb_linear': False,
            'observation_changes_formation': False}
