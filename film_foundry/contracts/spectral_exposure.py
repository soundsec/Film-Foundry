"""Portable, immutable exposure-profile values; no numerical runtime imports."""

from __future__ import annotations

from dataclasses import dataclass
from math import exp, isfinite

from film_foundry.contracts.data_kernel import (
    CAPTURE_WORKING_SPACE, SPECTRAL_EXPOSURE_MODEL_ID, SPECTRAL_EXPOSURE_PROFILE_FIELDS,
    SMITS_EXPOSURE_MODEL_ID,
)


def _number(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must contain numeric values")
    number = float(value)
    if not isfinite(number) or number < 0.0 or positive and number == 0.0:
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'non-negative'}")
    return number


def _vector(value: object, length: int, name: str, *, positive: bool = False) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} values")
    return tuple(_number(item, name, positive=positive) for item in value)


def _spectra(value: object, rows: int, size: int, name: str) -> tuple[tuple[float, ...], ...]:
    if not isinstance(value, (tuple, list)) or len(value) != rows:
        raise ValueError(f"{name} must contain {rows} spectra")
    spectra = tuple(_vector(row, size, name) for row in value)
    if any(not any(sample > 0.0 for sample in row) for row in spectra):
        raise ValueError(f"{name} spectra must have nonzero support")
    return spectra


@dataclass(frozen=True, slots=True)
class SpectralExposureProfile:
    """Declared RGB/Smits spectral basis and one or three material sensitivities.

    Spectra use relative exposure per nm. The basis includes the assumed
    source light; it is not a unique recovery of the original scene spectrum.
    Gains explicitly set the layer exposure scale, with no hidden white fit.
    The versioned model fixes rgb_basis order: RGB, or W/C/M/Y/R/G/B.
    """

    profile_id: str
    working_space: str
    wavelengths_nm: tuple[float, ...]
    rgb_basis: tuple[tuple[float, ...], ...]
    layer_sensitivity: tuple[tuple[float, ...], ...]
    layer_gain: tuple[float, ...]
    model: str = SPECTRAL_EXPOSURE_MODEL_ID

    def __post_init__(self) -> None:
        if self.model not in {SPECTRAL_EXPOSURE_MODEL_ID, SMITS_EXPOSURE_MODEL_ID}:
            raise ValueError("unsupported spectral_exposure_profile model")
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("spectral_exposure_profile profile_id must be a nonempty string")
        if self.working_space != CAPTURE_WORKING_SPACE:
            raise ValueError("spectral_exposure_profile requires the declared linear sRGB working space")
        if not isinstance(self.wavelengths_nm, (list, tuple)) or not 2 <= len(self.wavelengths_nm) <= 256:
            raise ValueError("wavelengths_nm must contain 2 to 256 samples")
        size = len(self.wavelengths_nm)
        wave = _vector(self.wavelengths_nm, size, "wavelengths_nm", positive=True)
        if any(right <= left for left, right in zip(wave, wave[1:])):
            raise ValueError("wavelengths_nm must be strictly increasing")
        if not isinstance(self.layer_sensitivity, (tuple, list)) or len(self.layer_sensitivity) not in {1, 3}:
            raise ValueError("layer_sensitivity must contain one or three layer spectra")
        layers = len(self.layer_sensitivity)
        object.__setattr__(self, "wavelengths_nm", wave)
        basis_count = 7 if self.model == SMITS_EXPOSURE_MODEL_ID else 3
        object.__setattr__(self, "rgb_basis", _spectra(self.rgb_basis, basis_count, size, "rgb_basis"))
        object.__setattr__(self, "layer_sensitivity", _spectra(self.layer_sensitivity, layers, size, "layer_sensitivity"))
        object.__setattr__(self, "layer_gain", _vector(self.layer_gain, layers, "layer_gain", positive=True))

    @property
    def layer_count(self) -> int:
        return len(self.layer_sensitivity)

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "profile_id": self.profile_id,
            "working_space": self.working_space,
            "wavelengths_nm": list(self.wavelengths_nm),
            "rgb_basis": [list(row) for row in self.rgb_basis],
            "layer_sensitivity": [list(row) for row in self.layer_sensitivity],
            "layer_gain": list(self.layer_gain),
        }

    @classmethod
    def from_value(cls, value: object) -> "SpectralExposureProfile":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != set(SPECTRAL_EXPOSURE_PROFILE_FIELDS):
            raise ValueError("spectral_exposure_profile fields are incomplete or unsupported")
        return cls(**value)


def make_smits_exposure_profile(layers: int = 3) -> SpectralExposureProfile:
    """Smits decomposition with original analytic creative basis, not published tables.

    The relative-light basis is defined for this project's linear sRGB proxy.
    It does not claim colorimetric inversion, D65 recovery or measured stock data.
    The historical rgb_basis field carries W/C/M/Y/R/G/B in this model only.
    Explicit gains make a unit neutral input give unit layer exposure.
    """
    if layers not in {1, 3}:
        raise ValueError("material must have one or three layers")
    wave = tuple(float(n) for n in range(380, 781, 5))
    red = tuple(1 / (1 + exp(-(n - 595) / 18)) for n in wave)
    blue = tuple(1 / (1 + exp((n - 495) / 18)) for n in wave)
    green = tuple(exp(-0.5 * ((n - 545) / 42) ** 2) for n in wave)
    basis = (tuple(1. for _ in wave), tuple(1-r for r in red),
             tuple(1-g for g in green), tuple(1-b for b in blue), red, green, blue)
    centers = (610, 545, 460) if layers == 3 else (545,)
    sensitivity = tuple(tuple(exp(-0.5 * ((n-center) / 45) ** 2) for n in wave) for center in centers)
    gain = tuple(1 / sum((row[i] + row[i+1]) * (wave[i+1]-wave[i]) * .5
                        for i in range(len(wave)-1)) for row in sensitivity)
    return SpectralExposureProfile("ff_smits7_analytic_light_v1", CAPTURE_WORKING_SPACE,
        wave, basis, sensitivity, gain, SMITS_EXPOSURE_MODEL_ID)
