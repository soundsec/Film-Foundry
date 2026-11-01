"""Portable spectral material and observer definitions; standard library only."""

from dataclasses import dataclass

from .spectral_exposure import _vector
from .data_kernel import CAPTURE_WORKING_SPACE

SPECTRAL_COMPONENTS = (
    "dye_c", "dye_m", "dye_y", "metallic_silver", "residual_halide",
    "bleached_halide", "clear_support", "masking_coupler", "auxiliary_layer",
)
SPECTRAL_MATERIAL_MODEL = "normalized_pool_absorbance_v1"
SPECTRAL_OBSERVER_MODEL = "spectral_transmission_observer_v1"
SPECTRAL_POST_PROCESS_MODEL = "piecewise_rgb_density_overlay_v1"
SPECTRAL_POST_PROCESS_ARRAY_KEY = "spectral_post_process_density_rgb"


def post_process_rgb_weights(wavelength_nm):
    """Creative density proxy: B/G/R anchors at 450/550/650 nm, flat tails.

    This defines an engineering basis, not measured dye or deposit spectra.
    Weights sum to one, so neutral density remains neutral at every wavelength.
    """
    w = float(wavelength_nm)
    if w <= 450.0:
        return (0.0, 0.0, 1.0)
    if w < 550.0:
        t = (w - 450.0) / 100.0
        return (0.0, t, 1.0-t)
    if w < 650.0:
        t = (w - 550.0) / 100.0
        return (t, 1.0-t, 0.0)
    return (1.0, 0.0, 0.0)


def _wave(value):
    if not isinstance(value, (list, tuple)) or not 2 <= len(value) <= 256:
        raise ValueError("wavelengths_nm requires 2 to 256 samples")
    wave = _vector(value, len(value), "wavelengths_nm", positive=True)
    if any(b <= a for a, b in zip(wave, wave[1:])):
        raise ValueError("wavelengths_nm must be strictly increasing")
    return wave


def _rows(value, count, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ValueError(f"{name} requires {count} spectra")
    return tuple(_vector(row, size, name) for row in value)


class _Profile:
    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError(f"{cls.__name__} fields are incomplete or unsupported")
        return cls(**value)

    def as_dict(self):
        def portable(value):
            return [portable(v) for v in value] if isinstance(value, tuple) else value
        return {key: portable(getattr(self, key)) for key in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class SpectralMaterialProfile(_Profile):
    profile_id: str
    wavelengths_nm: tuple[float, ...]
    component_absorbance: tuple[tuple[float, ...], ...]
    component_names: tuple[str, ...] = SPECTRAL_COMPONENTS
    coefficient_unit: str = "normalized_final_pool_v1"
    model: str = SPECTRAL_MATERIAL_MODEL

    def __post_init__(self):
        if self.model != SPECTRAL_MATERIAL_MODEL or self.coefficient_unit != "normalized_final_pool_v1":
            raise ValueError("unsupported spectral material model or coefficient unit")
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("spectral material profile_id must be nonempty")
        if tuple(self.component_names) != SPECTRAL_COMPONENTS:
            raise ValueError("spectral material component order is fixed")
        wave = _wave(self.wavelengths_nm)
        object.__setattr__(self, "wavelengths_nm", wave)
        object.__setattr__(self, "component_names", SPECTRAL_COMPONENTS)
        object.__setattr__(self, "component_absorbance", _rows(self.component_absorbance, 9, len(wave), "component_absorbance"))


@dataclass(frozen=True, slots=True)
class SpectralObservationProfile(_Profile):
    profile_id: str
    wavelengths_nm: tuple[float, ...]
    illuminant: tuple[float, ...]
    response_rgb: tuple[tuple[float, ...], ...]
    working_space: str = CAPTURE_WORKING_SPACE
    normalization: str = "open_gate_per_channel_v1"
    model: str = SPECTRAL_OBSERVER_MODEL

    def __post_init__(self):
        if self.model != SPECTRAL_OBSERVER_MODEL or self.normalization != "open_gate_per_channel_v1":
            raise ValueError("unsupported spectral observation model or normalization")
        if self.working_space != CAPTURE_WORKING_SPACE:
            raise ValueError("spectral response must declare the linear sRGB proxy working space")
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("spectral observer profile_id must be nonempty")
        wave = _wave(self.wavelengths_nm)
        light = _vector(self.illuminant, len(wave), "illuminant")
        response = _rows(self.response_rgb, 3, len(wave), "response_rgb")
        if any(not any(i * r > 0 for i, r in zip(light, row)) for row in response):
            raise ValueError("each spectral response needs illuminant overlap")
        object.__setattr__(self, "wavelengths_nm", wave)
        object.__setattr__(self, "illuminant", light)
        object.__setattr__(self, "response_rgb", response)
