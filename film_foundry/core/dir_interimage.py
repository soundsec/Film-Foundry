"""Lightweight material response for DIR-bearing colour film.

DIR is represented here as a property of the film material, not as a free-
standing visual phenomenon. The implementation estimates per-layer colour
coupling on a private bounded work grid, applies the material's effective
source-to-target response and returns a layer-rate field for the one formal
colour-development step. It deliberately stops before reaction--diffusion
chemistry, and no scanner or final-RGB operation participates.
"""

from __future__ import annotations

import cv2
import numpy as np
from film_foundry.core.film_process.integration import (
    _color_material,
    _compatibility_for_program,
    _material_latent_state,
)
from film_foundry.core.film_process.operators import (
    provisional_formed_dye_for_program,
)
from film_foundry.core.film_process.recipe import (
    program_from_develop_recipe,
)
from film_foundry.core.spatial_fields import (
    GlobalLayerRateMultiplierField,
    GlobalRateMultiplierField,
    SpatialFieldPlan,
    global_field_grid,
    resample_layer_exposure_addition,
)
from film_foundry.model.config import DevelopRecipeConfig, FilmStockConfig


DIR_MATERIAL_RESPONSE_PLAN = SpatialFieldPlan(
    key="dir_material_response",
    stage="development_formation",
    quantity="development_rate_multiplier",
    field_kind="global_layer",
    requires_global_source=True,
    requires_tile_halo=False,
    random_field_policy="none",
    persistent_state=False,
)


def _validated_dir_material_parameters(
    film: FilmStockConfig,
) -> tuple[float, np.ndarray]:
    radius = float(film.dir_diffusion_radius)
    response = np.asarray(
        film.dir_material_response_matrix,
        dtype=np.float32,
    )
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError("film.dir_diffusion_radius must be finite and positive")
    if response.shape != (3, 3) or not np.all(np.isfinite(response)):
        raise ValueError("film.dir_material_response_matrix must be a finite 3x3 matrix")
    if np.any(response < 0.0):
        raise ValueError("film.dir_material_response_matrix must be non-negative")
    target_sums = np.sum(response, axis=0, dtype=np.float64)
    if np.any(target_sums > 0.95 + 1e-6):
        raise ValueError(
            "each target column of film.dir_material_response_matrix must sum to at most 0.95"
        )
    return radius, response


def _resolved_process_effectiveness(
    recipe: DevelopRecipeConfig,
    program_kind: str,
) -> tuple[float, str]:
    configured = recipe.dir_process_effectiveness
    if configured is None:
        if program_kind == "color_reversal":
            return 0.0, "default_exhaustive_reversal_suppression"
        return 1.0, "default_color_negative"
    effectiveness = float(configured)
    if not np.isfinite(effectiveness) or not 0.0 <= effectiveness <= 1.0:
        raise ValueError(
            "develop.dir_process_effectiveness must be between zero and one or None"
        )
    return effectiveness, "explicit"


def build_dir_material_response_field(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    program_kind: str,
    *,
    latent_layer_exposure_addition=None,
    work_long_edge: int = 1800,
) -> tuple[GlobalLayerRateMultiplierField | None, dict[str, object]]:
    """Build a colour-step-only layer inhibitor field without live pool use."""

    radius, material_response = _validated_dir_material_parameters(film)
    program_kind = str(program_kind).strip().lower()
    process_effectiveness, effectiveness_source = _resolved_process_effectiveness(
        recipe,
        program_kind,
    )
    material_present = bool(np.any(material_response > 0.0))
    effective_response = material_response * process_effectiveness
    base_audit: dict[str, object] = {
        "plan": DIR_MATERIAL_RESPONSE_PLAN.as_dict(),
        "material_dir_present": material_present,
        "enabled": bool(material_present and process_effectiveness > 0.0),
        "applied": False,
        "process_effectiveness": process_effectiveness,
        "process_effectiveness_source": effectiveness_source,
        "response_scope": "material_color_development_rate",
        "requires_joint_characteristic_curve_calibration": True,
        "diffusion_radius_relative_to_short_edge": radius,
        "material_response_matrix": tuple(
            tuple(float(value) for value in row) for row in material_response
        ),
        "effective_response_matrix": tuple(
            tuple(float(value) for value in row) for row in effective_response
        ),
        "matrix_normalization": "per_target_effective_response_sum_le_0.95",
        "authoritative_pool_consumptions": 1,
        "provisional_authoritative_pool_consumptions": 0,
    }
    if not material_present:
        base_audit["skip_reason"] = "no_material_dir_response"
        return None, base_audit
    if process_effectiveness <= 0.0:
        base_audit["skip_reason"] = "process_suppresses_dir_inhibitor"
        return None, base_audit
    if program_kind not in {"color_negative", "color_reversal"}:
        raise ValueError("DIR interimage requires a unified color material program")
    if int(work_long_edge) <= 0:
        raise ValueError("DIR work_long_edge must be positive")

    image = np.asarray(image_linear, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError("DIR interimage requires an HxWx3 linear image")
    image_minimum = float(np.min(image))
    image_maximum = float(np.max(image))
    if (
        not np.isfinite(image_minimum)
        or not np.isfinite(image_maximum)
        or image_minimum < 0.0
    ):
        raise ValueError("DIR interimage image must be finite and non-negative")

    grid = global_field_grid(image.shape, int(work_long_edge))
    if grid.reduced:
        work_image = cv2.resize(
            image,
            (grid.work_shape[1], grid.work_shape[0]),
            interpolation=cv2.INTER_AREA,
        ).astype(np.float32)
    else:
        work_image = image
    work_addition = resample_layer_exposure_addition(
        latent_layer_exposure_addition,
        grid,
    )
    material = _color_material(film)
    state = _material_latent_state(
        work_image,
        film,
        material,
        latent_layer_exposure_addition=work_addition,
    )
    material_process = "reversal" if program_kind.endswith("reversal") else "negative"
    program = program_from_develop_recipe(
        recipe,
        mode=program_kind,
        material_process=material_process,
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)
    provisional_dye, color_step = provisional_formed_dye_for_program(
        state,
        program,
        compatibility,
        consume_private_state=True,
    )
    capacity = np.asarray(material.coupler_capacity, dtype=np.float32).reshape(1, 1, 3)
    np.divide(
        provisional_dye,
        np.maximum(capacity, 1e-6),
        out=provisional_dye,
    )
    np.clip(provisional_dye, 0.0, 1.0, out=provisional_dye)
    active_source_layers = tuple(
        int(source)
        for source in np.flatnonzero(np.any(effective_response > 0.0, axis=1))
    )
    source_maximum = max(
        float(np.max(provisional_dye[..., source]))
        for source in active_source_layers
    )
    base_audit.update(
        {
            "work_shape": tuple(int(value) for value in grid.work_shape),
            "color_development_step": str(color_step.label),
            "color_development_action": str(color_step.action.value),
            "source_mean_per_layer": tuple(
                float(np.mean(provisional_dye[..., layer], dtype=np.float64))
                for layer in range(3)
            ),
            "source_max_per_layer": tuple(
                float(np.max(provisional_dye[..., layer])) for layer in range(3)
            ),
            "active_source_layers": active_source_layers,
        }
    )
    # The provisional material state is private and is no longer needed after
    # its colour-coupling source has been reduced to the bounded work grid.
    # Drop the large formation buffers before allocating three inhibitor maps.
    del state, program, compatibility, material, capacity, work_addition, work_image
    if source_maximum <= 1e-8:
        base_audit["skip_reason"] = "zero_provisional_colour_coupling"
        return None, base_audit

    sigma = max(0.45, radius * float(min(grid.work_shape)))
    inhibitors = [np.zeros(grid.work_shape, dtype=np.float32) for _ in range(3)]
    spread = np.empty(grid.work_shape, dtype=np.float32)
    for source_layer in active_source_layers:
        cv2.GaussianBlur(
            provisional_dye[..., source_layer],
            (0, 0),
            sigmaX=sigma,
            sigmaY=sigma,
            dst=spread,
            borderType=cv2.BORDER_REFLECT_101,
        )
        for target_layer in range(3):
            weight = float(effective_response[source_layer, target_layer])
            if weight > 0.0:
                inhibitors[target_layer] += spread * weight
    del spread, provisional_dye

    layer_fields: list[GlobalRateMultiplierField] = []
    rate_means: list[float] = []
    rate_minima: list[float] = []
    rate_maxima: list[float] = []
    for inhibitor in inhibitors:
        np.clip(inhibitor, 0.0, 1.0, out=inhibitor)
        inhibitor *= -1.0
        inhibitor += 1.0
        rate_means.append(float(np.mean(inhibitor, dtype=np.float64)))
        rate_minima.append(float(np.min(inhibitor)))
        rate_maxima.append(float(np.max(inhibitor)))
        layer_fields.append(GlobalRateMultiplierField(inhibitor, grid))

    field = GlobalLayerRateMultiplierField(tuple(layer_fields))
    base_audit.update(
        {
            "applied": True,
            "blur_sigma_work_pixels": float(sigma),
            "rate_mean_per_layer": tuple(rate_means),
            "rate_min_per_layer": tuple(rate_minima),
            "rate_max_per_layer": tuple(rate_maxima),
            "step_scope": "declared_color_development_only",
            "representation": "lightweight_dir_material_response_v2",
        }
    )
    return field, base_audit
