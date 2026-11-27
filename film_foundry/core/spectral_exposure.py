"""Preintegrate RGB/Smits bases into a small three/seven-column layer operator."""

from __future__ import annotations

from functools import lru_cache
from dataclasses import replace

import numpy as np

from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.data_kernel import SMITS_EXPOSURE_MODEL_ID


def stock_lift_profile(film, saved_profile=None):
    """Choose a portable seven-basis exposure profile for the current stock."""
    profile = film.spectral_exposure_profile or saved_profile
    if profile is not None and profile.layer_count == film.effective_record_count:
        if profile.model == SMITS_EXPOSURE_MODEL_ID:
            return profile
        # This expansion reconstructs exactly the same incident spectrum for
        # every RGB input, even on an irregular grid or with non-unit gains.
        r, g, b = np.asarray(profile.rgb_basis)
        return replace(profile, model=SMITS_EXPOSURE_MODEL_ID,
                       rgb_basis=np.array([r+g+b, g+b, r+b, r+g, r, g, b]).tolist())
    return rgb_calibrated_lift_profile(film)


def rgb_calibrated_lift_profile(film):
    """Build the RGB-matched spectral approximation, including for an existing recipe."""
    from film_foundry.contracts.data_kernel import CAPTURE_WORKING_SPACE
    from film_foundry.model.config import MONO_LEGACY_EXPOSURE
    matrix = np.asarray(film.layer_sensitivity_matrix, dtype=float)
    if film.effective_record_count == 1:
        matrix = (np.array([[.2126, .7152, .0722]]) if film.monochrome_exposure_model == MONO_LEGACY_EXPOSURE
                  else matrix.mean(axis=0, keepdims=True))
    if not np.isfinite(matrix).all() or np.any(matrix < 0) or np.any(matrix.sum(axis=1) <= 0):
        raise ValueError("Each record needs nonnegative RGB sensitivity with at least one positive weight.")

    wave = np.arange(380., 781., 5.)
    def transition(start, end):
        t = np.clip((wave-start)/(end-start), 0., 1.)
        return t*t*(3.-2.*t)

    red = transition(575, 615)
    green = transition(475, 520) * (1.-transition(565, 610))
    blue = 1.-transition(465, 515)
    white = np.ones_like(wave)
    basis = np.array([white, 1.-red, 1.-green, 1.-blue, red, green, blue])
    # Each unit-area receptor lies inside the pure part of its source primary.
    # Its integral against that primary is one and against the others zero.
    receptors = np.array([np.maximum(0., 1.-np.abs(wave-center)/width)
                          for center, width in ((655.,40.), (545.,20.), (435.,30.))])
    receptors /= 5. * receptors.sum(axis=1, keepdims=True)
    response = matrix @ receptors
    return SpectralExposureProfile("ff_stock_rgb_calibrated_v1", CAPTURE_WORKING_SPACE, wave.tolist(),
                                   basis.tolist(), response.tolist(), (1.,)*len(matrix), SMITS_EXPOSURE_MODEL_ID)


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
        return map_profile_exposure(image, SpectralExposureProfile.from_value(profile), layer=layer)
    matrix = exposure_sensitivity_matrix(film)
    if layer is not None:
        matrix = matrix[layer:layer+1]
    return np.einsum("...c,lc->...l", np.clip(image, 0.0, None), matrix)


def map_profile_exposure(image, profile: SpectralExposureProfile, *, layer=None):
    """Map incident linear RGB using the declared wavelength basis and sensitivity."""
    matrix = np.asarray(compile_spectral_exposure(profile), dtype=np.float32)
    if profile.layer_count == 1:
        matrix = np.repeat(matrix, 3, axis=0)
    if layer is not None:
        matrix = matrix[layer:layer+1]
    return map_spectral_coefficients(image, matrix, profile.model, profile.layer_count == 1)


def map_spectral_coefficients(image, matrix, model, repeated=False):
    if model != SMITS_EXPOSURE_MODEL_ID:
        return np.einsum("...c,lc->...l", np.clip(image, 0.0, None), matrix)
    image = np.asarray(image, dtype=np.float32)
    # Read contiguous pixel runs directly. Strided inputs use a bounded copy.
    from .execution_topology import reference_execution_enabled
    reference = reference_execution_enabled()
    contiguous = image.reshape(-1, 3) if image.flags.c_contiguous and not reference else None
    rows = matrix[:1] if repeated and not reference else matrix
    if image.ndim == 3 and not reference and matrix.shape[0]:
        from .exposure_compute import try_smits
        accelerated = try_smits(image, rows, len(matrix))
        if accelerated is not None:
            return accelerated
    result = np.empty((*image.shape[:-1], matrix.shape[0]), dtype=np.float32)
    target = result.reshape(-1, matrix.shape[0])
    for start in range(0, target.shape[0], 16384):
        stop = min(start + 16384, target.shape[0])
        block = contiguous[start:stop] if contiguous is not None else image.flat[start*3:stop*3].reshape(-1, 3)
        rgb = np.maximum(block, 0.0)
        if reference:
            low_index, high_index = np.argmin(rgb, axis=1), np.argmax(rgb, axis=1)
            rgb.sort(axis=1)
            low, median, peak = rgb.T
        else:
            red, green, blue = rgb.T
            rg_low, rg_high = np.minimum(red, green), np.maximum(red, green)
            low, peak = np.minimum(rg_low, blue), np.maximum(rg_high, blue)
            median = np.maximum(rg_low, np.minimum(rg_high, blue))
            # Strict comparisons retain NumPy's original first-channel tie order.
            low_index = np.where(blue < rg_low, 2, green < red)
            high_index = np.where(blue > rg_high, 2, green > red)
        middle, high = median - low, peak - median
        for channel, row in enumerate(rows):
            target[start:stop, channel] = low * row[0] + middle * row[1+low_index] + high * row[4+high_index]
        if len(rows) < len(matrix):
            target[start:stop, 1:] = target[start:stop, :1]
    return result
