"""Relative sensitometry using a neutral linear step wedge and the active baths."""
from dataclasses import dataclass
import numpy as np
from film_foundry.core.engine import develop_negative


@dataclass
class CharacteristicCurve:
    log_h: np.ndarray
    densities: np.ndarray
    unexposed: np.ndarray
    positive: bool
    thresholds: tuple | None


def measure_curve(config):
    log_h = np.linspace(-5, 2, 43)
    steps = np.r_[0., 10.**log_h].astype(np.float32)
    width = 12
    wedge = np.tile(np.repeat(steps, width)[None, :, None], (32, 1, 3))
    seed = config.random_seed if config.random_seed is not None else 0
    medium = develop_negative(wedge, config, np.random.default_rng(seed), input_encoding='linear_rgb')
    density = medium.optical_density_rgb
    if density is None or density.shape[1:] != wedge.shape[1:] or density.shape[0]<wedge.shape[0]:
        raise ValueError('An RGB optical density master is required for this measurement')
    center = density.shape[0]//2
    blocks = density[center-8:center+8].reshape(16, len(steps), width, 3)
    measured = np.median(blocks[:, :, 3:-3], axis=(0,2))
    values = measured[1:]
    # Determine the direction from the measured response, including reversal stocks.
    neutral = values.mean(axis=1)
    descending = neutral[-1] < neutral[0]
    response = -neutral if descending else neutral
    response = np.maximum.accumulate(response)
    span = response[-1] - response[0]
    thresholds = None
    if span > .03:
        limits = response[0] + span * np.array([.1,.2,.8,.9])
        toe, low, high, shoulder = np.interp(limits, response, log_h)
        if high > low:
            slope = float(.6 * span / (high - low))
            thresholds = (toe, shoulder, -slope if descending else slope)
    return CharacteristicCurve(log_h, values, measured[0], medium.image_polarity=='positive', thresholds)
