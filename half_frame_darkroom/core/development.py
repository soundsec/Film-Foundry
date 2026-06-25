"""Simplified development kinetics for darkroom-style controls.

This module turns darkroom-facing recipe values such as time, temperature,
concentration, agitation, and developer type into effective density-formation
state. The derived values are used by sensitometry and density grain; they are
not direct RGB image-processing sliders.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from half_frame_darkroom.model.config import DevelopRecipeConfig


@dataclass(frozen=True, slots=True)
class DeveloperProfile:
    activity_rate: float = 1.0
    gamma_bias: float = 1.0
    fog_bias: float = 1.0
    dmax_bias: float = 1.0
    grain_bias: float = 1.0
    grain_radius_bias: float = 1.0
    shoulder_compensation: float = 0.0
    residue_bias: float = 1.0


@dataclass(frozen=True, slots=True)
class EffectiveDevelopmentState:
    activity: float
    progress: float
    progress_ratio: float
    gamma_factor: float
    d_min_shift: float
    d_max_factor: float
    toe_shift: float
    shoulder_shift: float
    grain_factor: float
    grain_radius_factor: float
    exhaustion: float
    fixer_exhaustion: float
    silvering_factor: float
    residue_factor: float
    clearing_failure: float
    light_leak_strength: float
    chemical_stain: float
    uneven_development: float
    process_mode: str


DEVELOPER_PROFILES: dict[str, DeveloperProfile] = {
    "standard": DeveloperProfile(),
    "fine_grain": DeveloperProfile(activity_rate=0.90, gamma_bias=0.94, fog_bias=0.78, grain_bias=0.62, grain_radius_bias=0.78),
    "compensating": DeveloperProfile(activity_rate=0.82, gamma_bias=0.86, dmax_bias=0.93, grain_bias=0.86, shoulder_compensation=0.62),
    "high_contrast": DeveloperProfile(activity_rate=1.08, gamma_bias=1.28, fog_bias=1.18, dmax_bias=1.03, grain_bias=1.18),
    "push": DeveloperProfile(activity_rate=1.18, gamma_bias=1.34, fog_bias=1.55, dmax_bias=1.01, grain_bias=1.55, grain_radius_bias=1.16),
    "exhausted": DeveloperProfile(activity_rate=0.50, gamma_bias=0.68, fog_bias=2.25, dmax_bias=0.70, grain_bias=1.45, grain_radius_bias=1.18),
    "monobath": DeveloperProfile(activity_rate=0.76, gamma_bias=0.84, fog_bias=1.45, dmax_bias=0.84, grain_bias=1.18, residue_bias=1.70),
    "hardening": DeveloperProfile(activity_rate=0.94, gamma_bias=0.96, fog_bias=0.92, grain_bias=0.84, grain_radius_bias=0.84),
}

FRAME_SIZE_GRAIN_FACTORS: dict[str, tuple[float, float]] = {
    "half_frame": (1.18, 1.22),
    "35mm": (1.00, 1.00),
    "6x6": (0.82, 0.78),
    "6x7": (0.78, 0.74),
    "4x5": (0.58, 0.55),
}

TIME_MIN_RANGE = (0.0, 240.0)
TEMPERATURE_C_RANGE = (0.0, 100.0)
CONCENTRATION_RANGE = (0.05, 10.0)
AGITATION_RANGE = (0.0, 5.0)
PUSH_STOPS_RANGE = (-6.0, 6.0)
ACTIVITY_TIME_MAX = 12.0
KINETIC_EXPOSURE_MAX = 80.0


def _profile_for(recipe: DevelopRecipeConfig) -> DeveloperProfile:
    key = str(recipe.developer_type).strip().lower().replace("-", "_").replace(" ", "_")
    return DEVELOPER_PROFILES.get(key, DEVELOPER_PROFILES["standard"])


def _frame_grain_factors(recipe: DevelopRecipeConfig) -> tuple[float, float]:
    key = str(recipe.frame_size).strip().lower().replace("-", "_").replace(" ", "_")
    return FRAME_SIZE_GRAIN_FACTORS.get(key, FRAME_SIZE_GRAIN_FACTORS["35mm"])


def _clamp_recipe_value(value: float, lower: float, upper: float, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return float(default)
    if math.isnan(numeric):
        return float(default)
    if numeric == math.inf:
        return float(upper)
    if numeric == -math.inf:
        return float(lower)
    return float(np.clip(numeric, lower, upper))


def build_effective_development(recipe: DevelopRecipeConfig) -> EffectiveDevelopmentState:
    """Derive an effective development state from a darkroom recipe."""
    profile = _profile_for(recipe)

    concentration = _clamp_recipe_value(recipe.concentration, *CONCENTRATION_RANGE, default=1.0)
    agitation = _clamp_recipe_value(recipe.agitation, *AGITATION_RANGE, default=1.0)
    time_min = _clamp_recipe_value(recipe.time_min, *TIME_MIN_RANGE, default=8.0)
    temp_c = _clamp_recipe_value(recipe.temperature_c, *TEMPERATURE_C_RANGE, default=20.0)
    push = _clamp_recipe_value(recipe.push_stops, *PUSH_STOPS_RANGE, default=0.0)
    exhaustion = _clamp_recipe_value(recipe.developer_exhaustion, 0.0, 1.0, default=0.0)
    fixer_exhaustion = _clamp_recipe_value(recipe.fixer_exhaustion, 0.0, 1.0, default=0.0)
    silver_retention = _clamp_recipe_value(recipe.silver_retention, 0.0, 1.0, default=0.0)
    compensation = _clamp_recipe_value(recipe.compensation, 0.0, 1.0, default=0.0)
    light_leak_strength = _clamp_recipe_value(getattr(recipe, "light_leak_strength", 0.0), 0.0, 1.0, default=0.0)
    chemical_stain = _clamp_recipe_value(getattr(recipe, "chemical_stain", 0.0), 0.0, 1.0, default=0.0)
    uneven_development = _clamp_recipe_value(getattr(recipe, "uneven_development", 0.0), 0.0, 1.0, default=0.0)
    frame_grain_factor, frame_radius_factor = _frame_grain_factors(recipe)
    process_mode = str(recipe.process_mode).strip().lower().replace("-", "_").replace(" ", "_")
    fixer_type = str(recipe.fixer_type).strip().lower().replace("-", "_").replace(" ", "_")
    is_monobath = process_mode == "monobath" or str(recipe.developer_type).strip().lower() == "monobath"

    q10 = 2.05
    temp_factor = q10 ** ((temp_c - 20.0) / 10.0)
    concentration_factor = concentration**0.92
    agitation_factor = 0.62 + 0.38 * min(agitation, 3.5)
    activity = profile.activity_rate * temp_factor * concentration_factor * agitation_factor

    rate_constant = 0.16
    kinetic_exposure = min(rate_constant * activity * time_min, KINETIC_EXPOSURE_MAX)
    progress = 1.0 - math.exp(-kinetic_exposure)
    reference_progress = 1.0 - math.exp(-rate_constant * 8.0)
    progress_ratio = progress / max(reference_progress, 1e-6)
    activity_time = min(activity * time_min / 8.0, ACTIVITY_TIME_MAX)
    overdevelopment = max(activity_time - 1.0, 0.0)
    underdevelopment = max(1.0 - activity_time, 0.0)

    gamma_factor = profile.gamma_bias
    gamma_factor *= 1.0 + 0.16 * push
    gamma_factor *= 1.0 - 0.24 * exhaustion
    gamma_factor *= 1.0 + 0.34 * (progress_ratio - 1.0)
    gamma_factor *= 1.0 + 0.10 * overdevelopment - 0.22 * underdevelopment
    gamma_factor = max(gamma_factor, 0.15)

    fog = 0.018 * max(push, 0.0)
    fog += 0.060 * exhaustion
    fog += 0.026 * overdevelopment
    fog += 0.012 * max(concentration - 1.0, 0.0)
    fog += 0.010 * max(temp_c - 20.0, 0.0) / 10.0
    d_min_shift = max(0.0, fog * profile.fog_bias)

    d_max_factor = profile.dmax_bias
    d_max_factor *= 1.0 - 0.18 * exhaustion
    d_max_factor *= 1.0 - 0.34 * underdevelopment
    d_max_factor *= 1.0 - 0.22 * fixer_exhaustion
    d_max_factor *= 1.0 - 0.32 * chemical_stain
    if is_monobath:
        d_max_factor *= 0.88 - 0.14 * fixer_exhaustion
    d_max_factor = float(np.clip(d_max_factor, 0.22, 1.35))

    toe_shift = -0.14 * push - 0.08 * (progress_ratio - 1.0) + 0.12 * underdevelopment
    shoulder_shift = -0.10 * push - 0.08 * overdevelopment
    shoulder_shift -= 0.20 * profile.shoulder_compensation
    shoulder_shift -= 0.14 * compensation
    shoulder_shift += 0.10 * underdevelopment

    grain_factor = profile.grain_bias
    grain_factor *= frame_grain_factor
    grain_factor *= 1.0 + 0.32 * max(push, 0.0)
    grain_factor *= 1.0 + 0.62 * exhaustion
    grain_factor *= 1.0 + 0.32 * overdevelopment
    grain_factor *= 1.0 + 0.18 * max(temp_c - 20.0, 0.0) / 10.0
    grain_factor *= 1.0 + 0.30 * fixer_exhaustion + 0.42 * silver_retention
    grain_factor *= 1.0 + 0.85 * chemical_stain + 0.45 * uneven_development
    if is_monobath:
        grain_factor *= 1.14
    grain_factor = max(grain_factor, 0.05)

    grain_radius_factor = profile.grain_radius_bias
    grain_radius_factor *= frame_radius_factor
    grain_radius_factor *= 1.0 + 0.12 * max(push, 0.0) + 0.16 * exhaustion
    grain_radius_factor *= 1.0 + 0.16 * silver_retention
    grain_radius_factor = max(grain_radius_factor, 0.05)

    clearing_failure = fixer_exhaustion
    if fixer_type in {"rapid", "fresh_rapid"}:
        clearing_failure *= 0.70
    if fixer_type in {"hardening", "hardener"}:
        clearing_failure *= 0.85
    if is_monobath:
        clearing_failure = max(clearing_failure, 0.24 + 0.70 * fixer_exhaustion)
    residue_factor = float(np.clip((clearing_failure + 1.25 * silver_retention + 1.10 * chemical_stain) * profile.residue_bias, 0.0, 3.0))
    silvering_factor = float(np.clip(1.15 * silver_retention + 0.82 * fixer_exhaustion + (0.28 if is_monobath else 0.0), 0.0, 3.0))

    d_min_shift += 0.030 * residue_factor + 0.018 * silvering_factor
    d_min_shift += 0.12 * chemical_stain + 0.025 * uneven_development

    return EffectiveDevelopmentState(
        activity=float(activity),
        progress=float(progress),
        progress_ratio=float(progress_ratio),
        gamma_factor=float(gamma_factor),
        d_min_shift=float(d_min_shift),
        d_max_factor=float(d_max_factor),
        toe_shift=float(toe_shift),
        shoulder_shift=float(shoulder_shift),
        grain_factor=float(grain_factor),
        grain_radius_factor=float(grain_radius_factor),
        exhaustion=float(exhaustion),
        fixer_exhaustion=float(fixer_exhaustion),
        silvering_factor=float(silvering_factor),
        residue_factor=float(residue_factor),
        clearing_failure=float(clearing_failure),
        light_leak_strength=float(light_leak_strength),
        chemical_stain=float(chemical_stain),
        uneven_development=float(uneven_development),
        process_mode=str(recipe.process_mode),
    )
