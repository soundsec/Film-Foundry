"""Selective process operators for reduced silver-halide material pools.

The public unified film process model explains the stage boundaries. ``OP-*``
labels identify individual operators; they do not claim microscopic or
quantitatively calibrated chemistry.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from film_foundry.core.execution_topology import reference_execution_enabled
from film_foundry.core.film_process.model import (
    FilmFinalMedium,
    FilmProcessState,
    ReducedFilmMaterial,
)
from film_foundry.core.film_process.formation_history import BathFormationResponse, ensure_history, add_product, retain_product


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
    REMOVE_EMULSION = "remove_emulsion"
    SHEAR_EMULSION = "shear_emulsion"
    AGE_LATENT = 'age_latent'
    LIGHT_RESIDUAL_HALIDE = 'light_residual_halide'


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
    dye_attack_l_mmol_s: float = .002
    time_response: dict | None = None


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
    # ``compatibility`` derives dye loss from material--process mismatch;
    # ``explicit`` uses this step's strength as a declared process accident.
    dye_destruction_basis: str = "compatibility"
    # Explicit source row survives expansion into reaction substeps. None is
    # the historical one-operator-per-stage convention for built-in programs.
    bath_index: int | None = None
    bath_entry: bool = True
    reaction_window: tuple[float,float] | None = None
    chemical_contact: tuple[float,float,float] | None = None
    chemical_placement: tuple[float,float,float] | None = None
    process_event_index: int | None = None
    formation_response: BathFormationResponse | None = None
    time_history: dict | None = None


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
    dye_destruction_basis: str = ""


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
    local_development_rate: object | None = None,
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
    if step.formation_response is not None:
        private = state.copy()
        before = private.metallic_silver.copy()
        _develop_with_history(private, step, compatibility, False, local_development_rate)
        return private.metallic_silver - before
    reactive = state.developable_halide()
    rate = _development_rate_field(
        state,
        step,
        compatibility,
        local_development_rate=local_development_rate,
    )
    return np.minimum(state.halide, reactive * rate).astype(np.float32, copy=False)


def provisional_formed_silver_for_bath(latent_state,program,bath_index,compatibility,local_development_rate=None):
    """Private first-bath estimate including competing same-bath reactions."""
    state=latent_state.copy();state.validate();baseline=None
    for index,step in enumerate(program.steps):
        if step.action == FilmProcessAction.LIGHT_RESIDUAL_HALIDE:break
        if step.bath_index is not None and step.bath_index>bath_index:break
        if step.process_event_index is not None and local_development_rate is not None:
            event=local_development_rate.events[step.process_event_index]
            # Estimate formation within the bath, before subsequent removal.
            if step.bath_index==bath_index and not event.get('before_bath',0):break
        if step.bath_index==bath_index and baseline is None:baseline=state.metallic_silver.copy()
        resolver=getattr(local_development_rate,'contact_for_step',None)
        contact=resolver(index,step) if callable(resolver) else None
        _apply_step(state,step,compatibility,local_development_rate=local_development_rate,bath_contact=contact)
    return np.maximum(state.metallic_silver-baseline,0.)


def provisional_formed_dye_for_program(
    latent_state: FilmProcessState,
    program: FilmProcessProgram,
    compatibility: CompatibilityProfile,
    local_development_rate: object | None = None,
    *,
    consume_private_state: bool = False,
) -> tuple[np.ndarray, FilmProcessStep]:
    """Estimate the first colour-coupling amount without touching live pools.

    Reversal programs must pass through their first silver development,
    removal, and remaining-halide activation before colour coupling can be
    estimated.  Those preceding operations therefore consume only a private
    copied state.  The returned dye amount itself is still provisional; the
    authoritative program later consumes the original material exactly once.
    """

    state = latent_state if consume_private_state else latent_state.copy()
    state.validate()
    for index,step in enumerate(program.steps):
        resolver=getattr(local_development_rate,'contact_for_step',None)
        contact=resolver(index,step) if callable(resolver) else None
        if step.action != FilmProcessAction.DEVELOP_COLOR:
            _apply_step(
                state,
                step,
                compatibility,
                local_development_rate=local_development_rate,
                bath_contact=contact,
            )
            continue
        if state.coupler is None and state.dye is None:
            # A silver-only stock can enter colour chemistry without acquiring dye.
            return np.zeros_like(state.halide), step
        if state.coupler is None or state.dye is None:
            raise ValueError("provisional color development requires paired dye pools")
        delta_silver = provisional_developed_halide(
            state,
            step,
            compatibility,
            local_development_rate=local_development_rate,
        )
        layers = state.halide.shape[-1]
        ratio = np.ones(layers, dtype=np.float32)
        if step.dye_coupling_ratio is not None:
            ratio = np.asarray(step.dye_coupling_ratio, dtype=np.float32).reshape(
                layers
            )
        ratio *= max(float(compatibility.dye_coupling), 0.0)
        ratio = ratio.reshape((1,) * (state.halide.ndim - 1) + (layers,))
        delta_silver *= ratio
        np.minimum(state.coupler, delta_silver, out=delta_silver)
        return delta_silver.astype(np.float32, copy=False), step
    raise ValueError("DIR estimation requires a color-development step")


def _develop(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    color: bool,
    local_development_rate: object | None = None,
) -> float:
    if step.formation_response is not None:
        return _develop_with_history(state, step, compatibility, color, local_development_rate)
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
    if state.coupler is None and state.dye is None:
        return reacted
    if state.coupler is None or state.dye is None:
        raise ValueError("color development requires paired coupler and dye pools")
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


def _try_development_conversion(state, step, compatibility, local_development_rate):
    from film_foundry.core import development_compute
    if not development_compute.eligible(state.halide):
        return None
    # Only built-in frozen fields may be resolved before a fallible device
    # call. Stateful continuation and unknown callbacks keep the CPU order.
    from film_foundry.core.spatial_fields import (
        StepDevelopmentRateField, GlobalRateMultiplierField, GlobalLayerRateMultiplierField,
    )
    from film_foundry.core.process_operations import BathContactRate
    def frozen(field):
        if field is None or type(field) is np.ndarray:
            return True
        if type(field) is StepDevelopmentRateField:
            fields = (field.common_rate, field.first_step_rate, *(entry[2] for entry in field.step_rate_fields))
            return all(part is None or type(part) in (
                np.ndarray, GlobalRateMultiplierField, GlobalLayerRateMultiplierField) for part in fields)
        if type(field) is BathContactRate:
            return (field.surface_session is None and field.inhibitor_session is None and frozen(field.base))
        return False
    if not frozen(local_development_rate):
        return None
    local = _local_rate_for_step(local_development_rate, step)
    if local is not None:
        if local.shape not in {state.halide.shape, state.halide.shape[:-1], (*state.halide.shape[:-1], 1)}:
            raise ValueError('local_development_rate must match the image plane or material-pool shape')
        low, high = float(np.min(local)), float(np.max(local))
        if not np.isfinite(low) or not np.isfinite(high) or low < 0.:
            raise ValueError('local_development_rate contains invalid values')
    resolver = getattr(local_development_rate, 'fog_rate_for_step', None)
    fog_local = resolver(step.label, step.action.value) if callable(resolver) else local
    gamma = float(np.clip(step.developability_gamma, .25, 3.))
    exponent = gamma-1. if abs(gamma-1.) > 1e-6 else 0.
    compensation = .65*float(np.clip(step.highlight_compensation, 0., .95))
    rates = _rate_field(state, step, compatibility.silver_development, compatibility)
    fog_rates = _rate_field(state, replace(step, strength=1.), compatibility.silver_development, compatibility)
    return development_compute.try_conversion(
        state.halide, state.developability, rates, fog_rates, local, fog_local,
        exponent, compensation, -step.formation_response.fog_dose*state.chemical_fog_susceptibility)


def _develop_with_history(state, step, compatibility, color, local_development_rate):
    """Convert activated and chemically fogged shares into the same products."""
    ensure_history(state)
    response = step.formation_response
    np.clip(state.developability, 0., 1., out=state.developability)
    reference = reference_execution_enabled()
    accelerated = _try_development_conversion(state, step, compatibility, local_development_rate)
    if accelerated is None:
        reactive = state.halide * state.developability
        if reference:
            inactive = np.maximum(state.halide - reactive, 0.)
        else:
            inactive = state.halide - reactive
            np.maximum(inactive, 0., out=inactive)
        rate = _development_rate_field(state, step, compatibility, local_development_rate, developability_is_bounded=True)
        if reference:
            signal = np.minimum(reactive * rate, reactive)
        else:
            signal = reactive * rate
            np.minimum(signal, reactive, out=signal)
        # Fog bypasses image gamma/compensation, but shares contact and stock.
        fog_rate = _rate_field(state, replace(step, strength=1.), compatibility.silver_development, compatibility)
        fog_resolver = getattr(local_development_rate, 'fog_rate_for_step', None)
        local = (fog_resolver(step.label, step.action.value) if callable(fog_resolver)
                 else _local_rate_for_step(local_development_rate, step))
        if local is not None:
            if local.shape == state.halide.shape[:-1]: local = local[..., None]
            fog_rate = fog_rate * np.maximum(local, 0.)
        fraction = -np.expm1(-response.fog_dose * state.chemical_fog_susceptibility * fog_rate)
        if reference:
            fog = inactive * np.clip(fraction, 0., 1.)
        else:
            # The inactive share and fraction are private and have no later
            # consumer. Keep the original clip/multiply order in their buffers.
            np.clip(fraction, 0., 1., out=fraction)
            inactive *= fraction
            fog = inactive
        converted = signal + fog
        reactive -= signal
    else:
        converted, reactive, fog = accelerated
    state.halide -= converted
    np.maximum(state.halide, 0., out=state.halide)
    state.developability.fill(0.)
    np.divide(reactive, state.halide, out=state.developability, where=state.halide > 1e-8)
    np.clip(state.developability, 0., 1., out=state.developability)
    state.metallic_silver += converted
    add_product(state, 'silver', converted, fog, response)
    reacted = float(np.sum(converted, dtype=np.float64))
    if color and (state.coupler is not None or state.dye is not None):
        if state.coupler is None or state.dye is None:
            raise ValueError('color development requires paired coupler and dye pools')
        layers = state.halide.shape[-1]
        ratio = np.asarray(step.dye_coupling_ratio or (1.,)*layers, np.float32) * max(compatibility.dye_coupling, 0.)
        if reference:
            formed_dye = np.minimum(state.coupler, converted * ratio)
            fog_share = np.divide(fog, converted, out=np.zeros_like(fog), where=converted > 0.)
            formed_fog = formed_dye * fog_share
        else:
            # Activation and silver inventory/history have already consumed
            # reactive/converted. Reuse those private fields for dye demand
            # and its fog subset; no material-state array changes ownership.
            reactive.fill(0.)
            np.divide(fog, converted, out=reactive, where=converted > 0.)
            converted *= ratio
            np.minimum(state.coupler, converted, out=converted)
            formed_dye = converted
            np.multiply(formed_dye, reactive, out=reactive)
            formed_fog = reactive
        add_product(state, 'dye', formed_dye, formed_fog, response)
        state.coupler -= formed_dye; state.dye += formed_dye
    return reacted


def _apply_step(
    state: FilmProcessState,
    step: FilmProcessStep,
    compatibility: CompatibilityProfile,
    local_development_rate: object | None = None,
    bath_contact: np.ndarray | None = None,
) -> float:
    action = step.action
    reference_topology = reference_execution_enabled()
    if action == FilmProcessAction.AGE_LATENT:
        from film_foundry.core.film_process.time_history import age_latent
        return age_latent(state,step.time_history,compatibility.time_response)
    if action == FilmProcessAction.LIGHT_RESIDUAL_HALIDE:
        from film_foundry.core.film_process.time_history import light_residual_halide
        return light_residual_halide(state,step.time_history,compatibility.time_response)
    if action == FilmProcessAction.SHEAR_EMULSION:
        from film_foundry.core.emulsion_shear import transport_state
        resolver=getattr(local_development_rate,'shear_for_step',None)
        if not callable(resolver):raise ValueError('Emulsion shear requires an ordered timeline provider')
        return transport_state(state,resolver(step))
    if action == FilmProcessAction.REMOVE_EMULSION:
        resolver=getattr(local_development_rate,'coverage_for_step',None)
        if not callable(resolver):raise ValueError('Emulsion removal requires an ordered timeline provider')
        from film_foundry.core.film_process.emulsion import remove_emulsion
        target=resolver(step)
        if getattr(local_development_rate,'has_shear',False):
            target=target*(1. if state.emulsion_coverage is None else state.emulsion_coverage)
        return remove_emulsion(state,target)
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
        if bath_contact is not None:rate=rate*bath_contact[...,None]
        retain_product(state, 'silver', 1.-rate)
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
            retain_product(state, 'dye', 1.-rate*damage)
            dye_loss = state.dye * (rate * damage)
            state.dye -= dye_loss
            removed_dye = float(np.sum(dye_loss, dtype=np.float64))
        return float(removed_mask + removed_dye)
    if action == FilmProcessAction.FIX_HALIDE:
        # OP-FIX-01: fixing cannot remove unbleached metallic silver or dye.
        rate = _rate_field(state, step, compatibility.halide_fixing, compatibility)
        if bath_contact is not None:rate=rate*bath_contact[...,None]
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
        if bath_contact is not None:rate=rate*bath_contact[...,None]
        retain_product(state, 'silver', 1.-rate)
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
        basis = str(step.dye_destruction_basis).strip().lower()
        if basis == "compatibility":
            destruction = max(0.0, 1.0 - float(compatibility.dye_stability))
        elif basis == "explicit":
            destruction = 1.0
        elif basis == 'chemical':
            from film_foundry.core.bath_kinetics import dye_attack_fraction
            if step.chemical_contact is None:raise ValueError('Chemical dye attack requires contact conditions')
            destruction=dye_attack_fraction(compatibility.dye_attack_l_mmol_s,*step.chemical_contact)
            if step.chemical_placement is not None:
                if bath_contact is None:raise ValueError('Local chemical contact requires a spatial bath provider')
                destruction=destruction*np.asarray(bath_contact,dtype=np.float32)[...,None]
            # External dye attack is independent of the original process's
            # cross-process layer balance; it acts only on surviving dye.
            retain_product(state, 'dye', 1.-destruction)
            removed=state.dye*destruction
            state.dye-=removed
            return float(np.sum(removed,dtype=np.float64))
        else:
            raise ValueError(f"unsupported dye destruction basis: {basis}")
        rate = _rate_field(state, step, destruction, compatibility)
        retain_product(state, 'dye', 1.-rate)
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
    # A missing coating cannot react in subsequent baths. Keep that actual
    # inventory in state; derive conditional optical sources only at the end.
    optical_state=state
    if state.emulsion_coverage is not None:
        from film_foundry.core.film_process.emulsion import optical_sources
        optical_state=optical_sources(state)
        residual=optical_state.halide
        bleached=optical_state.bleached_halide
    return FilmFinalMedium(
        material_key=material.key,
        metallic_silver=np.array(
            optical_state.metallic_silver,
            dtype=np.float32,
            copy=not transfer_pool_ownership,
        ),
        dye=(
            None
            if optical_state.dye is None
            else np.array(
                optical_state.dye,
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
        emulsion_coverage=None if state.emulsion_coverage is None else state.emulsion_coverage.copy(),
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
    process_run=None,
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
    if material.time_response is not None:
        compatibility=replace(compatibility,time_response=material.time_response)
    validator=getattr(local_development_rate,'validate_program',None)
    if callable(validator):validator(program)
    binding = process_run.bind(material, program, compatibility, latent_state.halide.shape) if process_run is not None else None
    resumed = process_run.resume if process_run is not None else None
    state = resumed.state.copy() if resumed is not None else latent_state if consume_latent_state else latent_state.copy()
    state.chemical_fog_susceptibility = material.chemical_fog_susceptibility
    state.validate()
    if (state.coupler is None) != (state.dye is None):
        raise ValueError("coupler and dye pools must be paired")
    if (material.coupler_capacity is not None
            and any(value > 0.0 for value in material.coupler_capacity)
            and (state.coupler is None or state.dye is None)):
        raise ValueError("coupler-bearing material requires coupler and dye pools")
    reports: list[FilmProcessStepReport] = list(resumed.trace) if resumed is not None else []
    first_step = resumed.next_step if resumed is not None else 0
    surface=getattr(local_development_rate,'surface_session',None) if process_run is not None and process_run.include_surface else None
    if process_run is not None and process_run.include_surface and surface is None:
        raise ValueError('Surface continuation requires a film surface provider')
    if surface is not None:
        if resumed is not None and resumed.surface is None:raise ValueError('Missing surface checkpoint')
        surface.bind(program,None if resumed is None else resumed.surface,first_step)
    inhibitor=getattr(local_development_rate,'inhibitor_session',None) if process_run is not None and process_run.include_inhibitor else None
    if process_run is not None and process_run.include_inhibitor and inhibitor is None:
        raise ValueError('Inhibitor continuation requires a film contact provider')
    if inhibitor is not None:
        if resumed is not None and resumed.inhibitor is None:raise ValueError('Missing inhibitor checkpoint')
        inhibitor.bind(program,None if resumed is None else resumed.inhibitor,first_step)
    if process_run is not None:
        process_run.at_boundary(state, first_step, reports, binding, validated=True,surface=surface,inhibitor=inhibitor)
    for index in range(first_step, len(program.steps)):
        step = program.steps[index]
        if surface is not None:surface.before_step(index)
        if inhibitor is not None:inhibitor.before_step(index)
        bath_index=index if step.bath_index is None else step.bath_index
        fixing_resolver=getattr(local_development_rate,'fixing_before_step',None)
        premature_fix=fixing_resolver(bath_index) if callable(fixing_resolver) and step.bath_entry else None
        if premature_fix is not None:
            incident_step=FilmProcessStep(FilmProcessAction.FIX_HALIDE,strength=1.,label=f'fixer_contact_before_bath_{bath_index+1}')
            removed=_apply_step(state,incident_step,compatibility,bath_contact=premature_fix)
            reports.append(FilmProcessStepReport(label=incident_step.label,action=incident_step.action.value,
                reacted_amount=float(removed),strength=1.,developability_gamma=1.,highlight_compensation=0.))
        contact_resolver=getattr(local_development_rate,'contact_for_step',None)
        contact=contact_resolver(index,step) if callable(contact_resolver) else None
        reacted = _apply_step(
            state,
            step,
            compatibility,
            local_development_rate=local_development_rate,
            bath_contact=contact,
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
                dye_destruction_basis=(
                    str(step.dye_destruction_basis)
                    if step.action == FilmProcessAction.DESTROY_DYE
                    else ""
                ),
            )
        )
        if process_run is not None:
            if surface is not None:surface.after_step(index)
            if inhibitor is not None:inhibitor.after_step(index)
            process_run.at_boundary(state, index + 1, reports, binding, validated=validate_each_step,surface=surface,inhibitor=inhibitor)
    if not validate_each_step:
        state.validate()
    capture=getattr(local_development_rate,'capture_coverage',None)
    if callable(capture):capture(state.emulsion_coverage)
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
