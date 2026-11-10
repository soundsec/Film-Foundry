"""Final-pool optics and read-only, bounded-memory spectral transmission."""

from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from film_foundry.contracts.emulsion_integrity import SPECTRAL_MODEL as EMULSION_MODEL, SPECTRAL_COVERAGE_KEY, SPECTRAL_COVERED_KEY, EMULSION_COMPONENTS

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
    emulsion_coverage: np.ndarray | None = field(default=None, kw_only=True)
    covered_density_rgb: np.ndarray | None = field(default=None, kw_only=True)

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
        if self.emulsion_coverage is not None:
            coverage=np.asarray(self.emulsion_coverage,dtype=np.float32)
            if coverage.shape!=values.shape[:2] or not np.isfinite(coverage).all() or np.any(coverage<0) or np.any(coverage>1):
                raise ValueError('Spectral emulsion coverage must be HxW in [0,1]')
            coverage.setflags(write=False);object.__setattr__(self,'emulsion_coverage',coverage)
        if self.covered_density_rgb is not None:
            covered=np.asarray(self.covered_density_rgb,dtype=np.float32)
            if self.emulsion_coverage is None or covered.shape!=values.shape[:2]+(3,) or not np.isfinite(covered).all():
                raise ValueError('Covered spectral density requires matching emulsion coverage')
            covered.setflags(write=False);object.__setattr__(self,'covered_density_rgb',covered)

    def retained_component_rows(self, row_slice):
        """Actual retained material; archived source arrays remain immutable."""
        values=self.component_amounts[row_slice]
        if self.emulsion_coverage is None:return values
        result=values.copy();coverage=self.emulsion_coverage[row_slice]
        for index in EMULSION_COMPONENTS:result[...,index]*=coverage
        return result

    def metadata(self):
        payload = {"profile": self.profile.as_dict(), "base_component_amounts": list(self.base_component_amounts)}
        if self.post_process_density_rgb is not None:
            payload["post_process_model"] = SPECTRAL_POST_PROCESS_MODEL
        if self.emulsion_coverage is not None:payload['emulsion_model']=EMULSION_MODEL
        if self.covered_density_rgb is not None:payload['covered_density_model']=SPECTRAL_POST_PROCESS_MODEL
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
    if not clear_base and medium.emulsion_coverage is not None:
        from film_foundry.core.emulsion_spectral import capture_coverage
        coverage=medium.emulsion_coverage if row_slice is None else medium.emulsion_coverage[row_slice]
        covered=medium.covered_density_rgb
        if covered is not None and row_slice is not None:covered=covered[row_slice]
        return capture_coverage(values,coverage,covered,overlay,weights,absorbance,observer.wavelengths_nm)
    if not reference_execution_enabled() and not clear_base:
        from .desktop_compute import try_spectral, reject
        from .execution_topology import execution_topology
        basis = np.asarray([post_process_rgb_weights(w) for w in observer.wavelengths_nm], dtype=np.float64)
        accelerated = try_spectral(values, absorbance, weights, overlay, basis)
        if accelerated is not None:
            # Sample without flattening/copying a full noncontiguous medium.
            ys = np.linspace(0, values.shape[0]-1, min(values.shape[0], 32), dtype=int)
            xs = np.linspace(0, values.shape[1]-1, min(values.shape[1], 32), dtype=int)
            indices = np.ix_(ys, xs)
            sample = SpectralMedium(medium.profile, values[indices], medium.base_component_amounts,
                                    None if overlay is None else overlay[indices])
            with execution_topology('reference'):
                expected = capture_spectral_medium(sample, observer)
            if np.allclose(accelerated[indices], expected, rtol=2e-7, atol=1e-35):
                return accelerated
            reject('Spectral kernel exceeded CPU tolerance')
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
    coverage_present = SPECTRAL_COVERAGE_KEY in data.files
    covered_present = SPECTRAL_COVERED_KEY in data.files
    if definition is None and not present and not overlay_present and not coverage_present and not covered_present:
        return None
    required = {"profile", "base_component_amounts"}
    if not isinstance(definition, dict) or not required<=set(definition) or set(definition)-required-{'post_process_model','emulsion_model','covered_density_model'} or not present:
        raise ValueError("spectral medium requires paired definition and component array")
    declared = "post_process_model" in definition
    if declared != overlay_present or (declared and definition["post_process_model"] != SPECTRAL_POST_PROCESS_MODEL):
        raise ValueError("spectral post-process model and density array must be paired and supported")
    if ('emulsion_model' in definition)!=coverage_present or coverage_present and definition['emulsion_model']!=EMULSION_MODEL:
        raise ValueError('Spectral emulsion model and coverage must be paired')
    if ('covered_density_model' in definition)!=covered_present or covered_present and (not coverage_present or definition['covered_density_model']!=SPECTRAL_POST_PROCESS_MODEL):
        raise ValueError('Covered spectral density requires its declared model and coverage')
    medium = SpectralMedium(SpectralMaterialProfile.from_value(definition["profile"]),
                            data["spectral_component_amounts"], definition["base_component_amounts"],
                            data[SPECTRAL_POST_PROCESS_ARRAY_KEY] if overlay_present else None,
                            emulsion_coverage=data[SPECTRAL_COVERAGE_KEY] if coverage_present else None,
                            covered_density_rgb=data[SPECTRAL_COVERED_KEY] if covered_present else None)
    if medium.component_amounts.shape[:2] != tuple(frame_shape[:2]):
        raise ValueError("spectral components must match optical master geometry")
    return medium
