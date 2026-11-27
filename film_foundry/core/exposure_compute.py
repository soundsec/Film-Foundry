"""Device execution of the material's spectral mapping and latent activation."""
import numpy as np
from . import layer_compute

_calls = {'cuda': 0, 'opencl': 0}
_curve_calls = {'cuda': 0, 'opencl': 0}
_sheet_calls = {'cuda': 0, 'opencl': 0}


def status():
    return dict(_calls)


def curve_status():
    return dict(_curve_calls)


def sheet_status():
    return dict(_sheet_calls)


def try_sheet_response(dose, log_half, contrast):
    image = dose[..., None]
    if not layer_compute.eligible(image, 'FILM_FOUNDRY_EXPOSURE_GPU', input_channels=1):
        return None
    table = np.array([log_half, contrast], np.float32)
    result = layer_compute.run(image, table, 1, 'sheet_response', _sheet_calls)
    return None if result is None else result[..., 0]


def try_smits(image, rows, channels):
    if not layer_compute.eligible(image, 'FILM_FOUNDRY_EXPOSURE_GPU'):
        return None
    return layer_compute.run(image, rows, channels, 'smits_exposure', _calls)


def try_latent_curve(image, parameters, toe_width, shoulder_width):
    if not layer_compute.eligible(image, 'FILM_FOUNDRY_EXPOSURE_GPU'):
        return None
    widths = np.full((3, 2), (max(toe_width, 1e-6), max(shoulder_width, 1e-6)), np.float32)
    table = np.column_stack((*parameters, widths))
    return layer_compute.run(image, table, 3, 'latent_curve', _curve_calls)
