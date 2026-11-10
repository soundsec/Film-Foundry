"""One-pass reduced adjacency kinetics for silver-halide development.

The model intentionally stops before reaction--diffusion simulation. It uses
one provisional, non-consuming estimate of the first development step to
derive a bounded global rate correction, then the formal process program runs
once against the authoritative material pools.
"""

from __future__ import annotations

import cv2
import numpy as np

from film_foundry.core.film_process.integration import (
    _compatibility_for_program,
)
from film_foundry.core.material_latent_grid import MaterialLatentSource
from film_foundry.core.film_process.operators import (
    FilmProcessAction,
    provisional_developed_halide,
)
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.spatial_fields import (
    GlobalRateMultiplierField,
    SpatialFieldPlan,
)
from film_foundry.model.config import DevelopRecipeConfig, FilmStockConfig


REDUCED_DEVELOPMENT_ADJACENCY_PLAN = SpatialFieldPlan(
    key="reduced_first_development_adjacency",
    stage="development_formation",
    quantity="development_rate_multiplier",
    field_kind="global_scalar",
    requires_global_source=True,
    requires_tile_halo=False,
    random_field_policy="none",
    persistent_state=False,
)


def _provisional_first_development_source(
    state,
    material,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    program_kind: str,
    process_contact=None,
) -> tuple[np.ndarray, str]:
    if not program_kind.startswith(("bw_", "color_")):
        raise ValueError(f"unsupported adjacency material program: {program_kind}")
    layer_count = material.layer_count
    material_process = "reversal" if program_kind.endswith("reversal") else "negative"
    program = program_from_develop_recipe(
        recipe,
        mode=program_kind,
        material_process=material_process,
        layer_count=layer_count,
    )
    first_step = next(
        (
            step
            for step in program.steps
            if step.action
            in {FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR}
        ),
        None,
    )
    if first_step is None:
        raise ValueError("adjacency requires a process program with development")
    compatibility = _compatibility_for_program(film, program.key, layer_count, program=program)
    if first_step.reaction_window is not None or process_contact is not None or recipe.latent_wait is not None:
        from film_foundry.core.film_process.operators import provisional_formed_silver_for_bath
        provisional=provisional_formed_silver_for_bath(state,program,first_step.bath_index,compatibility,process_contact)
    else:
        provisional = provisional_developed_halide(state, first_step, compatibility)
    capacity = np.asarray(material.halide_capacity, dtype=np.float32).reshape(
        (1,) * (provisional.ndim - 1) + (layer_count,)
    )
    normalized = np.divide(
        provisional,
        np.maximum(capacity, 1e-6),
        out=np.zeros_like(provisional, dtype=np.float32),
    )
    source = np.mean(normalized, axis=-1, dtype=np.float32)
    return np.clip(source, 0.0, 1.0).astype(np.float32), str(first_step.label)


def build_development_adjacency_field(
    image_linear: np.ndarray | None,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    program_kind: str,
    *,
    latent_layer_exposure_addition=None,
    work_long_edge: int = 1800,
    exposed_latent=None,
    material_source: MaterialLatentSource | None = None,
    process_contact=None,
) -> tuple[GlobalRateMultiplierField | None, dict[str, object]]:
    """Build one first-development-only rate field without consuming pools."""
    strength = float(recipe.development_adjacency_strength)
    radius = float(recipe.development_adjacency_radius)
    if not np.isfinite(strength) or not 0.0 <= strength <= 1.0:
        raise ValueError("development adjacency strength must be between zero and one")
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError("development adjacency radius must be finite and positive")
    base_audit: dict[str, object] = {
        "plan": REDUCED_DEVELOPMENT_ADJACENCY_PLAN.as_dict(),
        "enabled": bool(strength > 0.0),
        "applied": False,
        "strength": strength,
        "radius_relative_to_short_edge": radius,
        "formal_pool_consumptions": 1,
        "provisional_pool_consumptions": 0,
    }
    if strength <= 0.0:
        return None, base_audit

    material_source = material_source or MaterialLatentSource(film,
        image_linear=image_linear, exposed_latent=exposed_latent,
        latent_layer_exposure_addition=latent_layer_exposure_addition)
    state, grid = material_source.working_state(work_long_edge)
    source, first_label = _provisional_first_development_source(
        state,
        material_source.material,
        film,
        recipe,
        program_kind,
        process_contact.at_shape(grid.work_shape) if process_contact is not None else None,
    )

    sigma = max(0.45, radius * float(min(grid.work_shape)))
    local_mean = cv2.GaussianBlur(
        source,
        (0, 0),
        sigmaX=sigma,
        sigmaY=sigma,
        borderType=cv2.BORDER_REFLECT_101,
    ).astype(np.float32)
    # ``local_mean`` is private and has no independent consumer.  Reuse it as
    # the residual instead of retaining a third work-grid scalar frame.
    local_mean *= -1.0
    local_mean += source
    residual = local_mean
    residual -= float(np.mean(residual, dtype=np.float64))
    rms = float(np.sqrt(np.mean(np.square(residual), dtype=np.float64)))
    base_audit.update(
        {
            "work_shape": tuple(int(value) for value in grid.work_shape),
            "first_development_step": first_label,
            "source_mean": float(np.mean(source, dtype=np.float64)),
            "source_residual_rms": rms,
            "blur_sigma_work_pixels": float(sigma),
            "source_model": MaterialLatentSource.MODEL,
        }
    )
    if rms <= 1e-7:
        base_audit["skip_reason"] = "uniform_provisional_reaction"
        return None, base_audit

    np.divide(residual, max(2.0 * rms, 1e-7), out=residual)
    np.tanh(residual, out=residual)
    residual -= float(np.mean(residual, dtype=np.float64))
    maximum = float(np.max(np.abs(residual), initial=0.0))
    if maximum <= 1e-7:
        base_audit["skip_reason"] = "zero_centered_adjacency_signal"
        return None, base_audit
    residual /= maximum

    agitation = float(np.clip(recipe.agitation, 0.0, 2.5))
    agitation_factor = float(np.clip(1.4 / (0.4 + agitation), 0.45, 1.5))
    amplitude = float(0.28 * strength * agitation_factor)
    # The normalized residual has reached its final consumer; turn the same
    # buffer into the rate multiplier in place.
    residual *= amplitude
    residual += 1.0
    multiplier = residual.astype(np.float32, copy=False)
    field = GlobalRateMultiplierField(multiplier, grid)
    base_audit.update(
        {
            "applied": True,
            "agitation_factor": agitation_factor,
            "rate_amplitude": amplitude,
            "rate_mean_work_grid": float(np.mean(multiplier, dtype=np.float64)),
            "rate_min_work_grid": float(np.min(multiplier)),
            "rate_max_work_grid": float(np.max(multiplier)),
            "normalization": "global_rms_tanh_zero_mean",
            "step_scope": "first_development_only",
        }
    )
    return field, base_audit
