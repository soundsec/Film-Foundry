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


FULL_IMPLEMENTATION_PROFILE = "canonical_full_v1"
BUILTIN_REDUCED_IMPLEMENTATION_PROFILE = "builtin_reduced_fast_v1"


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
    lower_order_enabled = axes.lower_order_enabled
    return ImplementationPlan(
        execution_mode=axes.label,
        scaling_enabled=axes.scaling_enabled,
        quality_mode=axes.quality_mode,
        profile_id=(
            BUILTIN_REDUCED_IMPLEMENTATION_PROFILE
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
    )
