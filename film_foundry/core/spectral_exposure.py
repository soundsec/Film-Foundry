"""Preintegrate RGB/Smits bases into a small three/seven-column layer operator."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.data_kernel import SMITS_EXPOSURE_MODEL_ID


@lru_cache(maxsize=32)
def compile_spectral_exposure(profile: SpectralExposureProfile) -> tuple[tuple[float, ...], ...]:
    """Trapezoidal quadrature on the declared (possibly irregular) nm grid.

    Cache only immutable profile values and the tiny resulting matrix.
    No image, full spectral volume, device or process recipe enters setup.
    """
    wave = np.asarray(profile.wavelengths_nm, dtype=np.float64)
    steps = np.diff(wave)
    weights = np.empty_like(wave)
    weights[0], weights[-1] = steps[0] * 0.5, steps[-1] * 0.5
    weights[1:-1] = (steps[:-1] + steps[1:]) * 0.5
    with np.errstate(over="ignore", invalid="ignore"):
        matrix = np.einsum(
            "ln,cn,n,l->lc",
            np.asarray(profile.layer_sensitivity, dtype=np.float64),
            np.asarray(profile.rgb_basis, dtype=np.float64),
            weights,
            np.asarray(profile.layer_gain, dtype=np.float64),
        )
    if not np.isfinite(matrix).all() or np.max(matrix) > np.finfo(np.float32).max:
        raise ValueError("spectral exposure matrix exceeds the finite float32 range")
    matrix = matrix.astype(np.float32)
    if np.any(matrix.sum(axis=1, dtype=np.float64) <= 0.0):
        raise ValueError("spectral sensitivity has no representable RGB-basis overlap")
    return tuple(tuple(float(value) for value in row) for row in matrix)


def exposure_sensitivity_matrix(film) -> np.ndarray:
    """Resolve optional spectra; absence preserves the legacy matrix exactly."""
    if film.spectral_exposure_profile is None:
        return np.asarray(film.layer_sensitivity_matrix, dtype=np.float32).reshape(3, 3)
    profile = SpectralExposureProfile.from_value(film.spectral_exposure_profile)
    if profile.model == SMITS_EXPOSURE_MODEL_ID:
        raise ValueError("Smits exposure is piecewise; use map_layer_exposure, not a fixed RGB matrix")
    matrix = np.asarray(compile_spectral_exposure(profile), dtype=np.float32)
    return np.repeat(matrix, 3, axis=0) if profile.layer_count == 1 else matrix


def map_layer_exposure(image: np.ndarray, film, *, layer: int | None = None) -> np.ndarray:
    """Shared pointwise map, with bounded Smits scratch and no wavelength volume.

    Negative working RGB is clipped at the existing material-exposure boundary;
    HDR values above one are retained. Smits weights are positively homogeneous.
    """
    profile = film.spectral_exposure_profile
    if profile is not None:
        profile = SpectralExposureProfile.from_value(profile)
    if profile is None or profile.model != SMITS_EXPOSURE_MODEL_ID:
        matrix = exposure_sensitivity_matrix(film)
        if layer is not None:
            matrix = matrix[layer:layer+1]
        return np.einsum("...c,lc->...l", np.clip(image, 0.0, None), matrix)
    matrix = np.asarray(compile_spectral_exposure(profile), dtype=np.float32)
    if profile.layer_count == 1:
        matrix = np.repeat(matrix, 3, axis=0)
    if layer is not None:
        matrix = matrix[layer:layer+1]
    image = np.asarray(image, dtype=np.float32)
    result = np.empty((*image.shape[:-1], matrix.shape[0]), dtype=np.float32)
    # Iterate bounded pixel runs; flatiter avoids copying a strided full image.
    source = image.flat
    target = result.reshape(-1, matrix.shape[0])
    for start in range(0, target.shape[0], 16384):
        stop = min(start + 16384, target.shape[0])
        rgb = np.maximum(source[start*3:stop*3].reshape(-1, 3), 0.0)
        low_index = np.argmin(rgb, axis=1)
        high_index = np.argmax(rgb, axis=1)
        rgb.sort(axis=1)
        low = rgb[:, 0]
        middle = rgb[:, 1] - low
        high = rgb[:, 2] - rgb[:, 1]
        for channel, row in enumerate(matrix):
            target[start:stop, channel] = low * row[0] + middle * row[1+low_index] + high * row[4+high_index]
    return result
