"""Resolve the operator family selected by one execution mode.

Image-size policy remains in :mod:`film_foundry.core.execution`; this
plan records the independently resolved scaling, quality and lower-order axes
and selects component models from the latter. Semantics-equivalent common
optimizations are intentionally absent: they apply to every plan and are
audited through :mod:`film_foundry.core.execution_topology` instead of
being misreported as lower-order models.
"""

from __future__ import annotations

from dataclasses import dataclass

from film_foundry.core.density_grain import (
    FULL_DENSITY_GRAIN_MODEL,
    REDUCED_DENSITY_GRAIN_MODEL,
)
from film_foundry.core.execution import resolve_execution_axes
from film_foundry.core.halation import (
    FULL_HALATION_SOURCE_MODEL,
    REDUCED_HALATION_SOURCE_MODEL,
)
from film_foundry.core.mtf import (
    FULL_EMULSION_MTF_MODEL,
    REDUCED_EMULSION_MTF_MODEL,
)
from film_foundry.model.config import DarkroomConfig
from film_foundry.contracts.formation_profiles import (
    CURRENT_REDUCED_PROFILE, DETAILED_REDUCED_PROFILE, CONTACT_REDUCED_PROFILE, validate_reduced_profile,
)


FULL_IMPLEMENTATION_PROFILE = "canonical_full_v1"
BUILTIN_REDUCED_IMPLEMENTATION_PROFILE = CURRENT_REDUCED_PROFILE


@dataclass(frozen=True, slots=True)
class ImplementationPlan:
    """Immutable operator-family decision for one processing request."""

    execution_mode: str
    scaling_enabled: bool
    quality_mode: str
    profile_id: str
    lower_order_enabled: bool
    emulsion_mtf_model: str
    emulsion_grain_model: str
    halation_source_model: str
    bath_contact_model: str
    bath_contact_long_edge: int | None
    coupled_intervals: int
    spatial_chemistry_enabled: bool
    local_grain_response_enabled: bool
    basic_formation_only: bool
    contact_arithmetic: str = 'float64'

    def as_dict(self) -> dict[str, object]:
        return {
            "execution_mode": self.execution_mode,
            "scaling_enabled": self.scaling_enabled,
            "quality_mode": self.quality_mode,
            "profile_id": self.profile_id,
            "lower_order_enabled": self.lower_order_enabled,
            "component_models": {
                "emulsion_mtf": self.emulsion_mtf_model,
                "emulsion_grain": self.emulsion_grain_model,
                "halation_source": self.halation_source_model,
                "bath_contact": self.bath_contact_model,
                "wet_front_arithmetic": self.contact_arithmetic,
                "coupled_reactions": "primary_reaction_only_v1" if self.basic_formation_only else f"symmetric_split_{self.coupled_intervals}_v1",
                "spatial_chemistry": "declared_adjacency_dir_v1" if self.spatial_chemistry_enabled else "omitted_v1",
                "grain_response": "declared_formation_history_v1" if self.local_grain_response_enabled else "first_developer_global_v1",
                "process_incidents": "basic_stain_and_fog_v1" if self.basic_formation_only else "declared_process_events_v1",
            },
            "speed_estimate": None,
            "speed_estimate_basis": "not_available",
        }


def resolve_implementation_plan(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> ImplementationPlan:
    """Resolve a full or reduced family without inferring from image size.

    ``scaled_override`` is used by preview/explicit edge-scaled file runs. It
    changes only the scaling axis and deliberately preserves the independently
    selected lower-order family.
    """

    axes = resolve_execution_axes(config, scaled_override=scaled_override)
    from .compute_precision import contact_precision
    lower_order_enabled = axes.lower_order_enabled
    profile = validate_reduced_profile(config.processing.reduced_profile)
    basic = lower_order_enabled and profile == CURRENT_REDUCED_PROFILE
    sampled_contact = lower_order_enabled and profile in (DETAILED_REDUCED_PROFILE, CONTACT_REDUCED_PROFILE)
    reduced_budget = lower_order_enabled and profile in (CURRENT_REDUCED_PROFILE, DETAILED_REDUCED_PROFILE)
    contact_edge = (256 if axes.quality_mode in {"draft", "preview"} else
                    768 if axes.quality_mode in {"high", "full", "native"} else 512)
    return ImplementationPlan(
        execution_mode=axes.label,
        scaling_enabled=axes.scaling_enabled,
        quality_mode=axes.quality_mode,
        profile_id=(
            profile
            if lower_order_enabled
            else FULL_IMPLEMENTATION_PROFILE
        ),
        lower_order_enabled=lower_order_enabled,
        emulsion_mtf_model=(
            REDUCED_EMULSION_MTF_MODEL
            if lower_order_enabled
            else FULL_EMULSION_MTF_MODEL
        ),
        emulsion_grain_model=(
            REDUCED_DENSITY_GRAIN_MODEL
            if lower_order_enabled
            else FULL_DENSITY_GRAIN_MODEL
        ),
        halation_source_model=(
            REDUCED_HALATION_SOURCE_MODEL
            if lower_order_enabled
            else FULL_HALATION_SOURCE_MODEL
        ),
        bath_contact_model="uniform_bath_v1" if basic else "sampled_wet_time_v1" if sampled_contact else "native_wet_time_v1",
        bath_contact_long_edge=contact_edge if sampled_contact else None,
        coupled_intervals=(1 if axes.quality_mode in {"draft", "preview"} else
                           4 if axes.quality_mode in {"high", "full", "native"} else 2) if reduced_budget else 8,
        spatial_chemistry_enabled=not reduced_budget,
        local_grain_response_enabled=not reduced_budget,
        basic_formation_only=basic,
        contact_arithmetic=contact_precision(config.processing),
    )
