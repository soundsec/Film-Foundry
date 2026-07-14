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
class FixerProfile:
    exhaustion_sensitivity: float = 1.0
    baseline_failure: float = 0.0
    residue_bias: float = 1.0


@dataclass(frozen=True, slots=True)
class EffectiveDevelopmentState:
    developer_profile: str
    fixer_profile: str
    activity: float
    push_activity_factor: float
    developer_capacity: float
    progress: float
    progress_ratio: float
    underdevelopment: float
    overdevelopment: float
    agitation_deficit: float
    concentration_stress: float
    temperature_stress: float
    gamma_factor: float
    developer_fog_shift: float
    d_min_shift: float
    d_max_factor: float
    toe_shift: float
    shoulder_shift: float
    grain_factor: float
    grain_radius_factor: float
    exhaustion: float
    fixer_exhaustion: float
    silver_plating: float
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
}

FIXER_PROFILES: dict[str, FixerProfile] = {
    "standard": FixerProfile(),
    "rapid": FixerProfile(exhaustion_sensitivity=0.70),
    # A hardener is not a developer family. At the same abbreviated process
    # time it needs more fixing/washing allowance, so it must not be modelled as
    # improving clearing efficiency.
    "hardening": FixerProfile(exhaustion_sensitivity=1.15, residue_bias=1.10),
    # Development and fixing compete in a monobath. Keep a bounded baseline
    # clearing penalty even when the user has not added explicit exhaustion.
    "monobath": FixerProfile(
        exhaustion_sensitivity=0.92,
        baseline_failure=0.24,
        residue_bias=1.35,
    ),
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


def _normalized_key(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _developer_profile_for(recipe: DevelopRecipeConfig) -> tuple[str, DeveloperProfile]:
    key = _normalized_key(recipe.developer_type)
    # ``hardening`` used to appear in the developer menu, but hardening belongs
    # to fixer/gelatin handling. Preserve old files without inventing a special
    # developer response.
    if key in {"hardening", "hardener"}:
        key = "standard"
    if key not in DEVELOPER_PROFILES:
        key = "standard"
    return key, DEVELOPER_PROFILES[key]


def _fixer_profile_for(recipe: DevelopRecipeConfig, *, monobath: bool) -> tuple[str, FixerProfile]:
    key = _normalized_key(recipe.fixer_type)
    if key in {"fresh_rapid"}:
        key = "rapid"
    elif key in {"hardener"}:
        key = "hardening"
    if monobath or key == "monobath":
        key = "monobath"
    if key not in FIXER_PROFILES:
        key = "standard"
    return key, FIXER_PROFILES[key]


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
    developer_profile_key, profile = _developer_profile_for(recipe)

    concentration = _clamp_recipe_value(recipe.concentration, *CONCENTRATION_RANGE, default=1.0)
    agitation = _clamp_recipe_value(recipe.agitation, *AGITATION_RANGE, default=1.0)
    time_min = _clamp_recipe_value(recipe.time_min, *TIME_MIN_RANGE, default=8.0)
    temp_c = _clamp_recipe_value(recipe.temperature_c, *TEMPERATURE_C_RANGE, default=20.0)
    push = _clamp_recipe_value(recipe.push_stops, *PUSH_STOPS_RANGE, default=0.0)
    exhaustion = _clamp_recipe_value(recipe.developer_exhaustion, 0.0, 1.0, default=0.0)
    fixer_exhaustion = _clamp_recipe_value(recipe.fixer_exhaustion, 0.0, 1.0, default=0.0)
    silver_retention = _clamp_recipe_value(recipe.silver_retention, 0.0, 1.0, default=0.0)
    silver_plating = _clamp_recipe_value(getattr(recipe, "silver_plating", 0.0), 0.0, 1.0, default=0.0)
    compensation = _clamp_recipe_value(recipe.compensation, 0.0, 1.0, default=0.0)
    light_leak_strength = _clamp_recipe_value(getattr(recipe, "light_leak_strength", 0.0), 0.0, 1.0, default=0.0)
    chemical_stain = _clamp_recipe_value(getattr(recipe, "chemical_stain", 0.0), 0.0, 1.0, default=0.0)
    uneven_development = _clamp_recipe_value(getattr(recipe, "uneven_development", 0.0), 0.0, 1.0, default=0.0)
    frame_grain_factor, frame_radius_factor = _frame_grain_factors(recipe)
    process_mode = _normalized_key(recipe.process_mode)
    is_monobath = process_mode == "monobath" or developer_profile_key == "monobath"
    fixer_profile_key, fixer_profile = _fixer_profile_for(recipe, monobath=is_monobath)
    is_monobath = fixer_profile_key == "monobath"

    q10 = 2.05
    temp_factor = q10 ** ((temp_c - 20.0) / 10.0)
    concentration_factor = concentration**0.92
    agitation_factor = 0.62 + 0.38 * min(agitation, 3.5)
    # Exhausted developer must reduce the material conversion itself, not only
    # add fog/grain after a nominally complete reaction.  ``developer_capacity``
    # is a reduced-order reagent-capacity term: even a fully exhausted setting
    # retains a small bounded activity so the toy model stays numerically useful.
    developer_capacity = float(np.clip(1.0 - 0.60 * exhaustion, 0.40, 1.0))
    # Push/pull is an instruction to depart from normal development. Explicit
    # time and temperature remain available, but the stop control must also
    # change the conversion state on its own. The bounded factor is deliberately
    # conservative so using a documented time change does not double the full
    # effect when both controls are specified.
    push_activity_factor = float(np.clip(1.12**push, 0.60, 1.75))
    activity = (
        profile.activity_rate
        * temp_factor
        * concentration_factor
        * agitation_factor
        * developer_capacity
        * push_activity_factor
    )

    rate_constant = 0.16
    kinetic_exposure = min(rate_constant * activity * time_min, KINETIC_EXPOSURE_MAX)
    progress = 1.0 - math.exp(-kinetic_exposure)
    reference_progress = 1.0 - math.exp(-rate_constant * 8.0)
    progress_ratio = progress / max(reference_progress, 1e-6)
    activity_time = min(activity * time_min / 8.0, ACTIVITY_TIME_MAX)
    overdevelopment = max(activity_time - 1.0, 0.0)
    underdevelopment = max(1.0 - activity_time, 0.0)
    agitation_deficit = max(1.0 - agitation, 0.0)
    concentration_stress = float(np.clip((concentration - 1.0) / 3.0, 0.0, 1.0))
    temperature_stress = float(np.clip((temp_c - 20.0) / 20.0, 0.0, 1.0))

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
    developer_fog_shift = max(0.0, fog * profile.fog_bias)

    d_max_factor = profile.dmax_bias
    d_max_factor *= 1.0 - 0.18 * exhaustion
    d_max_factor *= 1.0 - 0.34 * underdevelopment
    if is_monobath:
        d_max_factor *= 0.88
    d_max_factor = float(np.clip(d_max_factor, 0.22, 1.35))

    toe_shift = -0.14 * push - 0.08 * (progress_ratio - 1.0) + 0.12 * underdevelopment
    shoulder_shift = -0.10 * push - 0.08 * overdevelopment
    shoulder_shift -= 0.20 * profile.shoulder_compensation
    shoulder_shift -= 0.14 * compensation
    shoulder_shift += 0.10 * underdevelopment

    grain_factor = profile.grain_bias
    grain_factor *= frame_grain_factor
    grain_factor *= max(0.55, 1.0 + 0.32 * max(push, 0.0) + 0.14 * min(push, 0.0))
    grain_factor *= 1.0 + 0.62 * exhaustion
    grain_factor *= 1.0 + 0.32 * overdevelopment
    grain_factor *= 1.0 + 0.18 * max(temp_c - 20.0, 0.0) / 10.0
    grain_factor *= 1.0 + 0.30 * fixer_exhaustion + 0.26 * silver_retention
    grain_factor *= 1.0 + 0.85 * chemical_stain + 0.45 * uneven_development
    if is_monobath:
        grain_factor *= 1.14
    grain_factor = max(grain_factor, 0.05)

    grain_radius_factor = profile.grain_radius_bias
    grain_radius_factor *= frame_radius_factor
    grain_radius_factor *= 1.0 + 0.12 * max(push, 0.0) + 0.16 * exhaustion
    grain_radius_factor *= 1.0 + 0.16 * silver_retention
    grain_radius_factor = max(grain_radius_factor, 0.05)

    clearing_failure = fixer_profile.baseline_failure + (
        (1.0 - fixer_profile.baseline_failure)
        * fixer_profile.exhaustion_sensitivity
        * fixer_exhaustion
    )
    clearing_failure = float(np.clip(clearing_failure, 0.0, 1.0))
    residue_factor = float(
        np.clip(
            (clearing_failure + 1.10 * chemical_stain)
            * profile.residue_bias
            * fixer_profile.residue_bias,
            0.0,
            3.0,
        )
    )
    # No explicit plating request means no invented plating. Exhaustion and a
    # monobath only amplify the tendency once the accident is present.
    plating_amplification = 1.0 + 0.90 * fixer_exhaustion + (0.65 if is_monobath else 0.0)
    silvering_factor = float(np.clip(silver_plating * plating_amplification, 0.0, 3.0))

    # Fixing and post-process contamination cannot undo dye or metallic silver
    # already formed by development. Retained silver/salts are observed from
    # the final material pools, while stain and unevenness are applied once in
    # accidents.py. Therefore D-min here contains developer fog only.
    d_min_shift = developer_fog_shift

    return EffectiveDevelopmentState(
        developer_profile=developer_profile_key,
        fixer_profile=fixer_profile_key,
        activity=float(activity),
        push_activity_factor=float(push_activity_factor),
        developer_capacity=float(developer_capacity),
        progress=float(progress),
        progress_ratio=float(progress_ratio),
        underdevelopment=float(underdevelopment),
        overdevelopment=float(overdevelopment),
        agitation_deficit=float(agitation_deficit),
        concentration_stress=float(concentration_stress),
        temperature_stress=float(temperature_stress),
        gamma_factor=float(gamma_factor),
        developer_fog_shift=float(developer_fog_shift),
        d_min_shift=float(d_min_shift),
        d_max_factor=float(d_max_factor),
        toe_shift=float(toe_shift),
        shoulder_shift=float(shoulder_shift),
        grain_factor=float(grain_factor),
        grain_radius_factor=float(grain_radius_factor),
        exhaustion=float(exhaustion),
        fixer_exhaustion=float(fixer_exhaustion),
        silver_plating=float(silver_plating),
        silvering_factor=float(silvering_factor),
        residue_factor=float(residue_factor),
        clearing_failure=float(clearing_failure),
        light_leak_strength=float(light_leak_strength),
        chemical_stain=float(chemical_stain),
        uneven_development=float(uneven_development),
        process_mode=str(recipe.process_mode),
    )
