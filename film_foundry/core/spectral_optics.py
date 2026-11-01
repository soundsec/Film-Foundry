"""Final-pool optics and read-only, bounded-memory spectral transmission."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from film_foundry.contracts.spectral_optics import (
    SpectralMaterialProfile, SpectralObservationProfile,
    SPECTRAL_POST_PROCESS_MODEL, SPECTRAL_POST_PROCESS_ARRAY_KEY, post_process_rgb_weights,
)
from film_foundry.core.execution_topology import reference_execution_enabled


@dataclass(frozen=True, slots=True)
class SpectralMedium:
    profile: SpectralMaterialProfile
    component_amounts: np.ndarray
    base_component_amounts: tuple[float, ...]
    post_process_density_rgb: np.ndarray | None = None

    def __post_init__(self):
        profile = SpectralMaterialProfile.from_value(self.profile)
        values = np.asarray(self.component_amounts, dtype=np.float32)
        base = np.asarray(self.base_component_amounts, dtype=np.float64)
        if values.ndim != 3 or values.shape[-1] != 9 or min(values.shape[:2]) <= 0:
            raise ValueError("spectral_component_amounts must have shape HxWx9")
        if base.shape != (9,) or not np.isfinite(base).all() or np.any(base < 0):
            raise ValueError("spectral base amounts require nine finite nonnegative values")
        if not np.isfinite(values).all() or np.any(values < 0):
            raise ValueError("spectral component amounts must be finite and nonnegative")
        values.setflags(write=False)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "component_amounts", values)
        object.__setattr__(self, "base_component_amounts", tuple(float(v) for v in base))
        if self.post_process_density_rgb is not None:
            overlay = np.asarray(self.post_process_density_rgb, dtype=np.float32)
            if overlay.shape != values.shape[:2] + (3,) or not np.isfinite(overlay).all():
                raise ValueError("spectral post-process density requires finite signed HxWx3 matching components")
            overlay.setflags(write=False)
            object.__setattr__(self, "post_process_density_rgb", overlay)

    def metadata(self):
        payload = {"profile": self.profile.as_dict(), "base_component_amounts": list(self.base_component_amounts)}
        if self.post_process_density_rgb is not None:
            payload["post_process_model"] = SPECTRAL_POST_PROCESS_MODEL
        return payload


def final_pool_amounts(final, out=None):
    """Normalized dye pools, mean silver/salt pools, and remaining support layers.

    No legacy density scaling or RGB inversion participates in this model.
    """
    shape = final.metallic_silver.shape[:2] + (9,)
    result = np.zeros(shape, dtype=np.float32) if out is None else out
    result.fill(0)
    if final.dye is not None:
        dye = np.asarray(final.dye)
        if dye.shape[-1] == 3:
            result[..., :3] = dye
        elif np.any(dye != 0):
            raise ValueError("spectral dye components require three material layers")
    result[..., 3] = np.mean(final.metallic_silver, axis=-1)
    result[..., 4] = np.mean(final.residual_halide, axis=-1)
    if final.bleached_halide is not None:
        result[..., 5] = np.mean(final.bleached_halide, axis=-1)
    result[..., 6] = 1
    result[..., 7] = final.masking_coupler_remaining
    result[..., 8] = final.auxiliary_remaining
    return (0., 0., 0., 0., 0., 0., 1., float(final.masking_coupler_remaining), float(final.auxiliary_remaining)), result


@lru_cache(maxsize=32)
def _observer_weights(profile):
    wave = np.asarray(profile.wavelengths_nm, dtype=np.float64)
    delta = np.diff(wave)
    quadrature = np.zeros_like(wave)
    quadrature[:-1] += delta / 2
    quadrature[1:] += delta / 2
    weights = np.asarray(profile.response_rgb) * np.asarray(profile.illuminant) * quadrature
    with np.errstate(over="ignore", invalid="ignore"):
        weights /= np.sum(weights, axis=1, keepdims=True)
    if not np.isfinite(weights).all() or np.any(np.sum(weights, axis=1) <= 0):
        raise ValueError("spectral observer weights are not representable")
    return tuple(tuple(float(v) for v in row) for row in weights)


@lru_cache(maxsize=32)
def _spectral_setup(material, observer):
    """Small immutable tables keyed on every spectral parameter, never names."""
    weights = np.asarray(_observer_weights(observer), dtype=np.float64)
    absorbance = np.asarray(material.component_absorbance, dtype=np.float64)
    weights.setflags(write=False)
    absorbance.setflags(write=False)
    return weights, absorbance


def capture_spectral_medium(medium, observer, *, row_slice=None, clear_base=False):
    """Relative open-gate RGB throughput; no pixel-by-wavelength volume."""
    observer = SpectralObservationProfile.from_value(observer)
    if medium is None:
        raise ValueError("spectral observation requires archived final component amounts")
    if observer.wavelengths_nm != medium.profile.wavelengths_nm:
        raise ValueError("spectral material and observer wavelength grids must match")
    weights, absorbance = _spectral_setup(medium.profile, observer)
    values = (np.asarray(medium.base_component_amounts).reshape(1, 1, 9) if clear_base
              else medium.component_amounts if row_slice is None else medium.component_amounts[row_slice])
    overlay = None if clear_base else medium.post_process_density_rgb
    if overlay is not None and row_slice is not None:
        overlay = overlay[row_slice]
    result = np.zeros(values.shape[:2] + (3,), dtype=np.float32)
    for start in range(0, values.shape[0], 128):
        stop = min(start + 128, values.shape[0])
        accum = np.zeros((stop-start, values.shape[1], 3), dtype=np.float64)
        reference = reference_execution_enabled()
        if not reference:
            density = np.empty(accum.shape[:2], dtype=np.float64)
            work = np.empty_like(density)
        for n in range(absorbance.shape[1]):
            if reference:
                with np.errstate(over="ignore"):
                    density = np.einsum("...k,k->...", values[start:stop], absorbance[:, n])
                if overlay is not None:
                    density += np.einsum("...c,c->...", overlay[start:stop],
                                         post_process_rgb_weights(observer.wavelengths_nm[n]))
                    np.maximum(density, 0.0, out=density)
                transmittance = np.power(10., -np.minimum(density, 80.))
                accum += transmittance[..., None] * weights[:, n]
            else:
                with np.errstate(over="ignore"):
                    np.einsum("...k,k->...", values[start:stop], absorbance[:, n], out=density)
                if overlay is not None:
                    np.einsum("...c,c->...", overlay[start:stop],
                              post_process_rgb_weights(observer.wavelengths_nm[n]), out=work)
                    np.add(density, work, out=density)
                    np.maximum(density, 0.0, out=density)
                np.minimum(density, 80., out=density)
                np.negative(density, out=density)
                np.power(10., density, out=density)
                for channel in range(3):
                    np.multiply(density, weights[channel, n], out=work)
                    np.add(accum[..., channel], work, out=accum[..., channel])
        result[start:stop] = accum
    return result


def spectral_observation_density(medium, observer, **kwargs):
    response = capture_spectral_medium(medium, observer, **kwargs)
    return -np.log10(np.maximum(response, np.float32(1e-35)))


def spectral_capture_record(scanner):
    profile = scanner.spectral_observation_profile
    if profile is None:
        return {"model": "rgb_optical_density_capture_v1"}
    profile = SpectralObservationProfile.from_value(profile)
    return {"model": "spectral_beer_lambert_capture_v1", "observer_profile_id": profile.profile_id,
            "normalization": profile.normalization}


def load_spectral_medium(data, payload, frame_shape):
    definition = payload.get("spectral_medium")
    present = "spectral_component_amounts" in data.files
    overlay_present = SPECTRAL_POST_PROCESS_ARRAY_KEY in data.files
    if definition is None and not present and not overlay_present:
        return None
    required = {"profile", "base_component_amounts"}
    if not isinstance(definition, dict) or set(definition) not in (required, required | {"post_process_model"}) or not present:
        raise ValueError("spectral medium requires paired definition and component array")
    declared = "post_process_model" in definition
    if declared != overlay_present or (declared and definition["post_process_model"] != SPECTRAL_POST_PROCESS_MODEL):
        raise ValueError("spectral post-process model and density array must be paired and supported")
    medium = SpectralMedium(SpectralMaterialProfile.from_value(definition["profile"]),
                            data["spectral_component_amounts"], definition["base_component_amounts"],
                            data[SPECTRAL_POST_PROCESS_ARRAY_KEY] if overlay_present else None)
    if medium.component_amounts.shape[:2] != tuple(frame_shape[:2]):
        raise ValueError("spectral components must match optical master geometry")
    return medium
