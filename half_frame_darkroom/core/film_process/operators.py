"""Selective process operators for reduced silver-halide material pools.

Evidence and reduction boundaries are registered in
``docs/EVIDENCE_AND_VALIDATION.md``.  ``OP-*`` comments below point to that
registry; they do not claim microscopic or quantitatively calibrated chemistry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from half_frame_darkroom.core.film_process.model import (
    FilmFinalMedium,
    FilmProcessState,
    ReducedFilmMaterial,
)


class FilmProcessAction(str, Enum):
    DEVELOP_SILVER = "develop_silver"
    DEVELOP_COLOR = "develop_color"
    ACTIVATE_REMAINING_HALIDE = "activate_remaining_halide"
    BLEACH_SILVER = "bleach_silver"
    FIX_HALIDE = "fix_halide"
    REMOVE_SILVER = "remove_silver"
    DESTROY_DYE = "destroy_dye"
    REMOVE_AUXILIARY = "remove_auxiliary"


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    """Reduced material--process compatibility used for cross processing."""

    silver_development: float = 1.0
    dye_coupling: float = 1.0
    activation: float = 1.0
    silver_bleach: float = 1.0
    halide_fixing: float = 1.0
    silver_removal: float = 1.0
    dye_stability: float = 1.0
    auxiliary_removal: float = 1.0
    layer_balance: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class FilmProcessStep:
    action: FilmProcessAction
    strength: float = 1.0
    label: str = ""
    layer_selectivity: tuple[float, ...] | None = None
    dye_coupling_ratio: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class FilmProcessProgram:
    key: str
    steps: tuple[FilmProcessStep, ...]
    output_polarity: str
    view_mode: str = "transmissive"
    compatible_interpreters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FilmProcessStepReport:
    label: str
    action: str
    reacted_amount: float


@dataclass(frozen=True, slots=True)
class FilmProcessResult:
    final_medium: FilmFinalMedium
    state: FilmProcessState
    trace: tuple[FilmProcessStepReport, ...]


def _rate_field(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility_value: float,
    compatibility: CompatibilityProfile,
) -> np.ndarray:
    layers = state.halide.shape[-1]
    selectivity = np.ones(layers, dtype=np.float32)
    if step.layer_selectivity is not None:
        selectivity *= np.asarray(step.layer_selectivity, dtype=np.float32).reshape(layers)
    if compatibility.layer_balance is not None:
        selectivity *= np.asarray(compatibility.layer_balance, dtype=np.float32).reshape(layers)
    rate = np.clip(float(step.strength) * float(compatibility_value) * selectivity, 0.0, 1.0)
    return rate.reshape((1,) * (state.halide.ndim - 1) + (layers,))


def _develop(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    color: bool,
) -> float:
    # OP-DEV-01 / OP-CDEV-01: consume only the currently developable share of
    # the single halide pool; color coupling is additionally capacity-bounded.
    rate = _rate_field(state, step, compatibility.silver_development, compatibility)
    reactive = state.developable_halide()
    delta_silver = np.minimum(state.halide, reactive * rate).astype(np.float32)
    state.halide -= delta_silver
    state.metallic_silver += delta_silver
    reacted = float(np.sum(delta_silver, dtype=np.float64))
    if not color:
        return reacted
    if state.coupler is None or state.dye is None:
        raise ValueError("color development requires coupler and dye pools")
    layers = state.halide.shape[-1]
    ratio = np.ones(layers, dtype=np.float32)
    if step.dye_coupling_ratio is not None:
        ratio = np.asarray(step.dye_coupling_ratio, dtype=np.float32).reshape(layers)
    ratio *= max(float(compatibility.dye_coupling), 0.0)
    ratio = ratio.reshape((1,) * (state.halide.ndim - 1) + (layers,))
    delta_dye = np.minimum(state.coupler, delta_silver * ratio).astype(np.float32)
    state.coupler -= delta_dye
    state.dye += delta_dye
    return reacted


def _apply_step(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
) -> float:
    action = step.action
    if action == FilmProcessAction.DEVELOP_SILVER:
        return _develop(state, step, compatibility, color=False)
    if action == FilmProcessAction.DEVELOP_COLOR:
        return _develop(state, step, compatibility, color=True)
    if action == FilmProcessAction.ACTIVATE_REMAINING_HALIDE:
        # OP-ACT-01: activation changes readiness, never the halide inventory.
        rate = _rate_field(state, step, compatibility.activation, compatibility)
        delta = (1.0 - state.developability) * rate
        state.developability = np.clip(state.developability + delta, 0.0, 1.0).astype(np.float32)
        return float(np.sum(delta * state.halide, dtype=np.float64))
    if action == FilmProcessAction.BLEACH_SILVER:
        # OP-BLEACH-01: preserve inventory in a distinct fixable-silver pool.
        rate = _rate_field(state, step, compatibility.silver_bleach, compatibility)
        delta = (state.metallic_silver * rate).astype(np.float32)
        state.metallic_silver -= delta
        if state.bleached_halide is None:
            state.bleached_halide = np.zeros_like(state.halide, dtype=np.float32)
        state.bleached_halide += delta
        return float(np.sum(delta, dtype=np.float64))
    if action == FilmProcessAction.FIX_HALIDE:
        # OP-FIX-01: fixing cannot remove unbleached metallic silver or dye.
        rate = _rate_field(state, step, compatibility.halide_fixing, compatibility)
        removed = state.halide * rate
        state.halide -= removed
        if state.bleached_halide is not None:
            removed_bleached = state.bleached_halide * rate
            state.bleached_halide -= removed_bleached
            removed = removed + removed_bleached
        return float(np.sum(removed, dtype=np.float64))
    if action == FilmProcessAction.REMOVE_SILVER:
        # OP-RMS-01: reduced direct removal used by the B&W reversal program.
        rate = _rate_field(state, step, compatibility.silver_removal, compatibility)
        removed = state.metallic_silver * rate
        state.metallic_silver -= removed
        return float(np.sum(removed, dtype=np.float64))
    if action == FilmProcessAction.DESTROY_DYE:
        if state.dye is None:
            return 0.0
        destruction = max(0.0, 1.0 - float(compatibility.dye_stability))
        rate = _rate_field(state, step, destruction, compatibility)
        removed = state.dye * rate
        state.dye -= removed
        return float(np.sum(removed, dtype=np.float64))
    if action == FilmProcessAction.REMOVE_AUXILIARY:
        rate = float(np.clip(float(step.strength) * compatibility.auxiliary_removal, 0.0, 1.0))
        removed = state.auxiliary_remaining * rate
        state.auxiliary_remaining -= removed
        return float(removed)
    raise ValueError(f"Unsupported film process action: {action}")


def _finalize(
    material: ReducedFilmMaterial,
    state: FilmProcessState,
    program: FilmProcessProgram,
) -> FilmFinalMedium:
    layers = material.layer_count
    silver_scale = material.silver_density_per_layer or tuple([1.0 / layers] * layers)
    residual_scale = material.residual_halide_density_per_layer or tuple([0.08 / layers] * layers)
    residual = np.array(state.halide, dtype=np.float32, copy=True)
    bleached = (
        None
        if state.bleached_halide is None
        else np.array(state.bleached_halide, dtype=np.float32, copy=True)
    )
    interpreters = program.compatible_interpreters
    if not interpreters:
        interpreters = (
            ("negative_scan",)
            if str(program.output_polarity).lower() == "negative"
            else ("positive_transparency_scan",)
        )
    return FilmFinalMedium(
        material_key=material.key,
        metallic_silver=np.array(state.metallic_silver, dtype=np.float32, copy=True),
        dye=None if state.dye is None else np.array(state.dye, dtype=np.float32, copy=True),
        residual_halide=residual,
        bleached_halide=bleached,
        dye_absorption_matrix=material.dye_absorption_matrix,
        silver_density_per_layer=tuple(float(v) for v in silver_scale),
        residual_halide_density_per_layer=tuple(float(v) for v in residual_scale),
        base_density_rgb=material.base_density_rgb,
        auxiliary_density_rgb=material.auxiliary_density_rgb,
        auxiliary_remaining=float(state.auxiliary_remaining),
        image_polarity=str(program.output_polarity),
        view_mode=str(program.view_mode),
        compatible_interpreters=tuple(str(v) for v in interpreters),
        process_key=program.key,
        retained_halide_density_rgb=tuple(
            float(v) for v in material.retained_halide_density_rgb
        ),
    )


def apply_process_program(
    material: ReducedFilmMaterial,
    latent_state: FilmProcessState,
    program: FilmProcessProgram,
    compatibility: CompatibilityProfile | None = None,
    *,
    consume_latent_state: bool = False,
) -> FilmProcessResult:
    """Apply a process program.

    Public callers retain the historical non-mutating default.  Internal
    formation adapters may explicitly consume a state they have just created,
    avoiding a complete duplicate of every full-resolution material pool.
    """
    compatibility = compatibility or CompatibilityProfile()
    state = latent_state if consume_latent_state else latent_state.copy()
    state.validate()
    reports: list[FilmProcessStepReport] = []
    for index, step in enumerate(program.steps):
        reacted = _apply_step(state, step, compatibility)
        state.validate()
        reports.append(
            FilmProcessStepReport(
                label=step.label or f"step_{index + 1}",
                action=step.action.value,
                reacted_amount=float(reacted),
            )
        )
    return FilmProcessResult(
        final_medium=_finalize(material, state, program),
        state=state,
        trace=tuple(reports),
    )
