"""Selective process operators for reduced silver-halide material pools.

Evidence and reduction boundaries are registered in
``docs/internal/EVIDENCE_AND_VALIDATION.md``.  ``OP-*`` comments below point to that
registry; they do not claim microscopic or quantitatively calibrated chemistry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from half_frame_darkroom.core.execution_topology import reference_execution_enabled
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
    BLEACH_MASK_DYE = "bleach_mask_dye"
    FIX_HALIDE = "fix_halide"
    REMOVE_SILVER = "remove_silver"
    DESTROY_DYE = "destroy_dye"
    REMOVE_AUXILIARY = "remove_auxiliary"


@dataclass(frozen=True, slots=True)
class CompatibilityProfile:
    """Reduced material--process compatibility used for cross processing.

    Rate-like efficiencies are losses bounded to ``[0, 1]`` at use sites.
    ``dye_coupling`` and ``layer_balance`` are ratios and may exceed one;
    they remain capacity/rate bounded by the receiving material pools.
    """

    silver_development: float = 1.0
    dye_coupling: float = 1.0
    activation: float = 1.0
    silver_bleach: float = 1.0
    halide_fixing: float = 1.0
    silver_removal: float = 1.0
    dye_stability: float = 1.0
    auxiliary_removal: float = 1.0
    mask_bleach: float = 1.0
    mask_bleach_dye_damage: float = 0.16
    layer_balance: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class FilmProcessStep:
    action: FilmProcessAction
    strength: float = 1.0
    label: str = ""
    layer_selectivity: tuple[float, ...] | None = None
    dye_coupling_ratio: tuple[float, ...] | None = None
    # Reduced spatial kinetics evaluated from the already-frozen material
    # developability field.  These values alter conversion rate, never the
    # latent state itself.
    developability_gamma: float = 1.0
    highlight_compensation: float = 0.0


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
    strength: float = 1.0
    developability_gamma: float = 1.0
    highlight_compensation: float = 0.0


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
    compatibility_efficiency = float(np.clip(compatibility_value, 0.0, 1.0))
    rate = np.clip(
        float(step.strength) * compatibility_efficiency * selectivity,
        0.0,
        1.0,
    )
    return rate.reshape((1,) * (state.halide.ndim - 1) + (layers,))


def _local_rate_for_step(
    local_development_rate: object | None,
    step: FilmProcessStep,
) -> np.ndarray | None:
    if local_development_rate is None:
        return None
    resolver = getattr(local_development_rate, "rate_for_step", None)
    if callable(resolver):
        resolved = resolver(step.label, step.action.value)
        return None if resolved is None else np.asarray(resolved, dtype=np.float32)
    return np.asarray(local_development_rate, dtype=np.float32)


def _development_rate_field_optimized(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    local_development_rate: object | None = None,
    *,
    developability_is_bounded: bool = False,
) -> np.ndarray:
    """Resolve development kinetics without consuming a material pool."""
    rate = _rate_field(state, step, compatibility.silver_development, compatibility)

    # Developer contrast and compensation belong to reaction kinetics.  A
    # gamma above one makes weakly activated regions convert more slowly; a
    # gamma below one gives them relatively more completion.  Compensating
    # action then reduces local rate smoothly towards highly activated areas,
    # approximating developer exhaustion/adjacency without rewriting e(x,y).
    response_gamma = float(np.clip(step.developability_gamma, 0.25, 3.0))
    compensation = float(np.clip(step.highlight_compensation, 0.0, 0.95))
    activation = None
    activation_is_private = False
    if abs(response_gamma - 1.0) > 1e-6 or compensation > 0.0:
        source_activation = np.asarray(state.developability, dtype=np.float32)
        if developability_is_bounded:
            activation = source_activation
        else:
            activation = np.clip(source_activation, 0.0, 1.0)
            activation_is_private = True
    if abs(response_gamma - 1.0) > 1e-6:
        # Pivot at mid activation so gamma changes slope instead of merely
        # reducing every value below the mathematical endpoint e=1.
        # The bounded public fallback owns its activation copy when no
        # compensation still needs the original field.  The formal process
        # path instead keeps state.developability read-only here and creates
        # one private kinetics field.  In both cases the ufunc order remains
        # maximum -> divide -> power -> base-rate multiply.
        if activation_is_private and compensation <= 0.0:
            kinetics = activation
            np.maximum(kinetics, 1e-6, out=kinetics)
        else:
            kinetics = np.maximum(activation, 1e-6)
        np.divide(kinetics, 0.5, out=kinetics)
        np.power(kinetics, response_gamma - 1.0, out=kinetics)
        np.multiply(kinetics, rate, out=kinetics)
        rate = kinetics
    if compensation > 0.0:
        # Reuse a private bounded activation copy where possible.  The second
        # scratch is required to preserve the historical smoothstep
        # evaluation order g*g*(3-2*g) exactly; after that, the original gate
        # is dead and the squared field becomes the final multiplier.
        if activation_is_private:
            gate = activation
            np.subtract(gate, 0.35, out=gate)
        else:
            gate = np.subtract(activation, 0.35, dtype=np.float32)
        np.divide(gate, 0.65, out=gate)
        np.clip(gate, 0.0, 1.0, out=gate)
        multiplier = np.multiply(gate, gate, dtype=np.float32)
        np.multiply(gate, 2.0, out=gate)
        np.subtract(3.0, gate, out=gate)
        np.multiply(multiplier, gate, out=multiplier)
        np.multiply(multiplier, 0.65 * compensation, out=multiplier)
        np.subtract(1.0, multiplier, out=multiplier)
        if rate.shape == multiplier.shape:
            np.multiply(rate, multiplier, out=rate)
        else:
            np.multiply(multiplier, rate, out=multiplier)
            rate = multiplier
    local = _local_rate_for_step(local_development_rate, step)
    if local is not None:
        if local.shape == state.halide.shape[:-1]:
            local = local[..., None]
        if local.shape not in {state.halide.shape, (*state.halide.shape[:-1], 1)}:
            raise ValueError(
                "local_development_rate must match the image plane or material-pool shape"
            )
        local_minimum = float(np.min(local))
        local_maximum = float(np.max(local))
        if (
            not np.isfinite(local_minimum)
            or not np.isfinite(local_maximum)
            or local_minimum < 0.0
        ):
            raise ValueError("local_development_rate contains invalid values")
        if rate.shape == state.halide.shape:
            np.multiply(rate, local, out=rate)
        else:
            rate = np.multiply(rate, local, dtype=np.float32)
    np.clip(rate, 0.0, 1.0, out=rate)
    return rate.astype(np.float32, copy=False)


def _development_rate_field_reference(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    local_development_rate: object | None = None,
    *,
    developability_is_bounded: bool = False,
) -> np.ndarray:
    """Frozen allocation-heavy kinetics topology for developer A/B audits."""
    rate = _rate_field(state, step, compatibility.silver_development, compatibility)
    response_gamma = float(np.clip(step.developability_gamma, 0.25, 3.0))
    compensation = float(np.clip(step.highlight_compensation, 0.0, 0.95))
    activation = None
    if abs(response_gamma - 1.0) > 1e-6 or compensation > 0.0:
        source_activation = np.asarray(state.developability, dtype=np.float32)
        activation = (
            source_activation
            if developability_is_bounded
            else np.clip(source_activation, 0.0, 1.0)
        )
    if abs(response_gamma - 1.0) > 1e-6:
        rate = rate * np.power(
            np.maximum(activation, 1e-6) / 0.5,
            response_gamma - 1.0,
        )
    if compensation > 0.0:
        gate = np.clip((activation - 0.35) / 0.65, 0.0, 1.0)
        gate = gate * gate * (3.0 - 2.0 * gate)
        rate = rate * (1.0 - 0.65 * compensation * gate)
    local = _local_rate_for_step(local_development_rate, step)
    if local is not None:
        if local.shape == state.halide.shape[:-1]:
            local = local[..., None]
        if local.shape not in {state.halide.shape, (*state.halide.shape[:-1], 1)}:
            raise ValueError(
                "local_development_rate must match the image plane or material-pool shape"
            )
        local_minimum = float(np.min(local))
        local_maximum = float(np.max(local))
        if (
            not np.isfinite(local_minimum)
            or not np.isfinite(local_maximum)
            or local_minimum < 0.0
        ):
            raise ValueError("local_development_rate contains invalid values")
        rate = rate * local
    return np.clip(rate, 0.0, 1.0).astype(np.float32, copy=False)


def _development_rate_field(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    local_development_rate: object | None = None,
    *,
    developability_is_bounded: bool = False,
) -> np.ndarray:
    """Resolve identical kinetics through the selected developer topology."""
    implementation = (
        _development_rate_field_reference
        if reference_execution_enabled()
        else _development_rate_field_optimized
    )
    return implementation(
        state,
        step,
        compatibility,
        local_development_rate=local_development_rate,
        developability_is_bounded=developability_is_bounded,
    )


def provisional_developed_halide(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
) -> np.ndarray:
    """Estimate one development step without mutating ``state``.

    This is the sole source permitted for the reduced adjacency field. The
    formal process program later consumes the original state exactly once.
    """
    if step.action not in {
        FilmProcessAction.DEVELOP_SILVER,
        FilmProcessAction.DEVELOP_COLOR,
    }:
        raise ValueError("provisional development requires a development step")
    reactive = state.developable_halide()
    rate = _development_rate_field(state, step, compatibility)
    return np.minimum(state.halide, reactive * rate).astype(np.float32, copy=False)


def _develop(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    color: bool,
    local_development_rate: object | None = None,
) -> float:
    # OP-DEV-01 / OP-CDEV-01: consume only the currently developable share of
    # the single halide pool; color coupling is additionally capacity-bounded.
    # The process state is mutable and has just passed validation. Apply the
    # same public [0, 1] boundary to its owned activation buffer, then multiply
    # directly. This avoids materializing the clipped field that
    # ``developable_halide()`` must return for non-mutating public callers.
    np.clip(state.developability, 0.0, 1.0, out=state.developability)
    reactive = np.multiply(
        state.halide,
        state.developability,
        dtype=np.float32,
    )
    rate = _development_rate_field(
        state,
        step,
        compatibility,
        local_development_rate=local_development_rate,
        developability_is_bounded=True,
    )
    # Build the bounded silver conversion in its own private work field.  The
    # former expression held both ``reactive * rate`` and its minimum result;
    # consuming the product in place keeps the same multiply/minimum order.
    delta_silver = np.multiply(reactive, rate, dtype=np.float32)
    np.minimum(state.halide, delta_silver, out=delta_silver)
    # ``reactive`` is a private derived work field.  Reuse it for remaining
    # H^E instead of retaining another full material-layer temporary.
    np.subtract(reactive, delta_silver, out=reactive)
    np.maximum(reactive, 0.0, out=reactive)
    state.halide -= delta_silver
    # ``developability`` is the live fraction H^E / H, not a permanent label
    # copied from the initial exposure.  Once activated halide is developed,
    # both H and H^E shrink and the ratio must be recomputed.  Otherwise a
    # second development step can recreate a fraction of an already consumed
    # latent image without any reversal activation or new exposure.
    # The old activation fraction has reached its last consumer in
    # ``_development_rate_field``.  Its buffer is owned by this mutable state;
    # an optional original-exposure audit is a separate copy.  Clear and reuse
    # it as the H^E/H result instead of allocating a full zeros_like field.
    state.developability.fill(0.0)
    np.divide(
        reactive,
        state.halide,
        out=state.developability,
        where=state.halide > 1e-8,
    )
    np.clip(state.developability, 0.0, 1.0, out=state.developability)
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
    # Silver inventory and its report scalar have already consumed this
    # private conversion field, so it can become the dye-demand field without
    # retaining another pair of full material-layer temporaries.
    delta_silver *= ratio
    np.minimum(state.coupler, delta_silver, out=delta_silver)
    delta_dye = delta_silver
    state.coupler -= delta_dye
    state.dye += delta_dye
    return reacted


def _apply_step(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    local_development_rate: object | None = None,
) -> float:
    action = step.action
    reference_topology = reference_execution_enabled()
    if action == FilmProcessAction.DEVELOP_SILVER:
        return _develop(
            state,
            step,
            compatibility,
            color=False,
            local_development_rate=local_development_rate,
        )
    if action == FilmProcessAction.DEVELOP_COLOR:
        return _develop(
            state,
            step,
            compatibility,
            color=True,
            local_development_rate=local_development_rate,
        )
    if action == FilmProcessAction.ACTIVATE_REMAINING_HALIDE:
        # OP-ACT-01: activation changes readiness, never the halide inventory.
        rate = _rate_field(state, step, compatibility.activation, compatibility)
        delta = np.subtract(1.0, state.developability, dtype=np.float32)
        delta *= rate
        reacted_field = (
            delta * state.halide
            if reference_topology
            else None
        )
        state.developability += delta
        np.clip(state.developability, 0.0, 1.0, out=state.developability)
        if reacted_field is not None:
            return float(np.sum(reacted_field, dtype=np.float64))
        # Halide is unchanged by activation.  Once the readiness fraction has
        # been applied to state, the private delta field can become the
        # material-amount audit field instead of allocating delta*halide.
        delta *= state.halide
        return float(np.sum(delta, dtype=np.float64))
    if action == FilmProcessAction.BLEACH_SILVER:
        # OP-BLEACH-01: preserve inventory in a distinct fixable-silver pool.
        rate = _rate_field(state, step, compatibility.silver_bleach, compatibility)
        if (
            not reference_topology
            and state.bleached_halide is None
            and bool(np.all(rate == 1.0))
        ):
            # A complete first bleach transfers the whole owned silver pool
            # into the distinct fixable-salt state. The old implementation
            # formed an equal delta, subtracted it to zero, allocated another
            # zero pool, then added the delta. Ownership transfer preserves
            # exactly the same inventory and report while avoiding one full
            # material-layer temporary and two memory passes. Partial bleach,
            # pre-existing bleached salt, and cross-process efficiency retain
            # the general formula below.
            reacted = float(np.sum(state.metallic_silver, dtype=np.float64))
            state.bleached_halide = state.metallic_silver
            state.metallic_silver = np.zeros_like(
                state.bleached_halide,
                dtype=np.float32,
            )
            return reacted
        delta = (state.metallic_silver * rate).astype(np.float32, copy=False)
        state.metallic_silver -= delta
        if state.bleached_halide is None:
            state.bleached_halide = np.zeros_like(state.halide, dtype=np.float32)
        state.bleached_halide += delta
        return float(np.sum(delta, dtype=np.float64))
    if action == FilmProcessAction.BLEACH_MASK_DYE:
        # EXP-MASK-BLEACH-01: this is deliberately separate from silver
        # bleach.  It removes the coloured masking-coupler contribution and,
        # in reduced form, can also damage formed image dyes.
        rate = float(
            np.clip(
                float(step.strength) * float(np.clip(compatibility.mask_bleach, 0.0, 1.0)),
                0.0,
                1.0,
            )
        )
        removed_mask = float(state.masking_coupler_remaining) * rate
        state.masking_coupler_remaining -= removed_mask
        removed_dye = 0.0
        if state.dye is not None:
            damage = float(np.clip(compatibility.mask_bleach_dye_damage, 0.0, 1.0))
            dye_loss = state.dye * (rate * damage)
            state.dye -= dye_loss
            removed_dye = float(np.sum(dye_loss, dtype=np.float64))
        return float(removed_mask + removed_dye)
    if action == FilmProcessAction.FIX_HALIDE:
        # OP-FIX-01: fixing cannot remove unbleached metallic silver or dye.
        rate = _rate_field(state, step, compatibility.halide_fixing, compatibility)
        if not reference_topology and bool(np.all(rate == 1.0)):
            if state.bleached_halide is None:
                reacted = float(np.sum(state.halide, dtype=np.float64))
            else:
                # Preserve the old per-element float32 addition before the
                # float64 report reduction, but form only one combined removal
                # field instead of two full temporary pools.
                removed = np.add(
                    state.halide,
                    state.bleached_halide,
                    dtype=np.float32,
                )
                reacted = float(np.sum(removed, dtype=np.float64))
                del removed
                state.bleached_halide.fill(0.0)
            state.halide.fill(0.0)
            return reacted
        removed = state.halide * rate
        state.halide -= removed
        if state.bleached_halide is not None:
            removed_bleached = state.bleached_halide * rate
            state.bleached_halide -= removed_bleached
            removed += removed_bleached
        return float(np.sum(removed, dtype=np.float64))
    if action == FilmProcessAction.REMOVE_SILVER:
        # OP-RMS-01: reduced direct removal used by the B&W reversal program.
        rate = _rate_field(state, step, compatibility.silver_removal, compatibility)
        if not reference_topology and bool(np.all(rate == 1.0)):
            reacted = float(np.sum(state.metallic_silver, dtype=np.float64))
            state.metallic_silver.fill(0.0)
            return reacted
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
        rate = float(
            np.clip(
                float(step.strength)
                * float(np.clip(compatibility.auxiliary_removal, 0.0, 1.0)),
                0.0,
                1.0,
            )
        )
        removed = state.auxiliary_remaining * rate
        state.auxiliary_remaining -= removed
        return float(removed)
    raise ValueError(f"Unsupported film process action: {action}")


def _finalize(
    material: ReducedFilmMaterial,
    state: FilmProcessState,
    program: FilmProcessProgram,
    *,
    transfer_pool_ownership: bool = False,
) -> FilmFinalMedium:
    layers = material.layer_count
    silver_scale = material.silver_density_per_layer or tuple([1.0 / layers] * layers)
    residual_scale = material.residual_halide_density_per_layer or tuple([0.08 / layers] * layers)
    residual = np.array(
        state.halide,
        dtype=np.float32,
        copy=not transfer_pool_ownership,
    )
    bleached = (
        None
        if state.bleached_halide is None
        else np.array(
            state.bleached_halide,
            dtype=np.float32,
            copy=not transfer_pool_ownership,
        )
    )
    interpreters = program.compatible_interpreters
    if not interpreters:
        interpreters = (
            ("negative_scan",)
            if str(program.output_polarity).lower() == "negative"
            else ("positive_transparency_scan",)
        )
    clear_support = material.clear_support_density_rgb
    mask_density = material.masking_coupler_density_rgb
    if clear_support is None or mask_density is None:
        effective_base = material.base_density_rgb
    else:
        effective_base = tuple(
            float(clear_support[channel])
            + float(mask_density[channel]) * float(state.masking_coupler_remaining)
            for channel in range(3)
        )
    return FilmFinalMedium(
        material_key=material.key,
        metallic_silver=np.array(
            state.metallic_silver,
            dtype=np.float32,
            copy=not transfer_pool_ownership,
        ),
        dye=(
            None
            if state.dye is None
            else np.array(
                state.dye,
                dtype=np.float32,
                copy=not transfer_pool_ownership,
            )
        ),
        residual_halide=residual,
        bleached_halide=bleached,
        dye_absorption_matrix=material.dye_absorption_matrix,
        silver_density_per_layer=tuple(float(v) for v in silver_scale),
        residual_halide_density_per_layer=tuple(float(v) for v in residual_scale),
        base_density_rgb=effective_base,
        clear_support_density_rgb=clear_support,
        masking_coupler_density_rgb=mask_density,
        masking_coupler_remaining=float(state.masking_coupler_remaining),
        auxiliary_density_rgb=material.auxiliary_density_rgb,
        auxiliary_remaining=float(state.auxiliary_remaining),
        image_polarity=str(program.output_polarity),
        view_mode=str(program.view_mode),
        compatible_interpreters=tuple(str(v) for v in interpreters),
        process_key=program.key,
        retained_halide_density_rgb=tuple(
            float(v) for v in material.retained_halide_density_rgb
        ),
        bleached_halide_density_per_layer=(
            None
            if material.bleached_halide_density_per_layer is None
            else tuple(float(v) for v in material.bleached_halide_density_per_layer)
        ),
        bleached_halide_density_rgb=(
            None
            if material.bleached_halide_density_rgb is None
            else tuple(float(v) for v in material.bleached_halide_density_rgb)
        ),
    )


def apply_process_program(
    material: ReducedFilmMaterial,
    latent_state: FilmProcessState,
    program: FilmProcessProgram,
    compatibility: CompatibilityProfile | None = None,
    *,
    consume_latent_state: bool = False,
    transfer_final_pool_ownership: bool = False,
    local_development_rate: object | None = None,
    validate_each_step: bool = True,
) -> FilmProcessResult:
    """Apply a process program.

    Public callers retain the historical non-mutating default.  Internal
    formation adapters may explicitly consume a state they have just created,
    avoiding a complete duplicate of every full-resolution material pool.
    Such single-owner callers may additionally transfer the terminal pool
    buffers into the immutable final medium.  That opt-in makes the matching
    arrays in ``result.state`` read-only; disabling it restores the historical
    copied finalization path.  Public callers retain per-step validation by
    default.  The closed internal formation adapter may instead validate only
    the already-checked input and final state; its bounded operators cannot be
    externally mutated between steps, so this only merges redundant full-frame
    reductions and does not relax either system boundary.
    """
    if transfer_final_pool_ownership and not consume_latent_state:
        raise ValueError(
            "transfer_final_pool_ownership requires consume_latent_state=True"
        )
    if reference_execution_enabled():
        # Developer A/B mode restores the copied state/finalization topology.
        # This deliberately changes only buffer ownership: the same program,
        # operators, validation boundaries, values, and trace remain active.
        consume_latent_state = False
        transfer_final_pool_ownership = False
    compatibility = compatibility or CompatibilityProfile()
    state = latent_state if consume_latent_state else latent_state.copy()
    state.validate()
    reports: list[FilmProcessStepReport] = []
    for index, step in enumerate(program.steps):
        reacted = _apply_step(
            state,
            step,
            compatibility,
            local_development_rate=local_development_rate,
        )
        if validate_each_step:
            state.validate()
        reports.append(
            FilmProcessStepReport(
                label=step.label or f"step_{index + 1}",
                action=step.action.value,
                reacted_amount=float(reacted),
                strength=float(step.strength),
                developability_gamma=float(step.developability_gamma),
                highlight_compensation=float(step.highlight_compensation),
            )
        )
    if not validate_each_step:
        state.validate()
    return FilmProcessResult(
        final_medium=_finalize(
            material,
            state,
            program,
            transfer_pool_ownership=transfer_final_pool_ownership,
        ),
        state=state,
        trace=tuple(reports),
    )
