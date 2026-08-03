"""One-pass reduced adjacency kinetics for silver-halide development.

The model intentionally stops before reaction--diffusion simulation. It uses
one provisional, non-consuming estimate of the first development step to
derive a bounded global rate correction, then the formal process program runs
once against the authoritative material pools.
"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.film_process.integration import (
    _color_material,
    _compatibility_for_program,
    _is_monochrome_material,
    _material_latent_state,
    _silver_material,
)
from half_frame_darkroom.core.film_process.operators import (
    FilmProcessAction,
    provisional_developed_halide,
)
from half_frame_darkroom.core.film_process.recipe import program_from_develop_recipe
from half_frame_darkroom.core.spatial_fields import (
    CompositeLayerExposureAdditionField,
    EdgeExposureAdditionField,
    GlobalRateMultiplierField,
    LayerExposureAdditionField,
    SpatialFieldPlan,
    combine_layer_exposure_addition_fields,
    global_field_grid,
)
from half_frame_darkroom.model.config import DevelopRecipeConfig, FilmStockConfig


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


def _resample_layer_addition(field, grid):
    """Project an exposure-addition declaration onto one global work grid."""
    if field is None or grid.work_shape == grid.full_shape:
        return field
    work_h, work_w = grid.work_shape
    if isinstance(field, LayerExposureAdditionField):
        scalar = cv2.resize(
            field.scalar_field,
            (work_w, work_h),
            interpolation=cv2.INTER_AREA,
        ).astype(np.float32)
        return LayerExposureAdditionField(scalar, field.layer_weights, field.strength)
    if isinstance(field, EdgeExposureAdditionField):
        return EdgeExposureAdditionField(
            full_shape=grid.work_shape,
            layer_weights=field.layer_weights,
            edge_weights=field.edge_weights,
            strength=field.strength,
            depth_scale=field.depth_scale,
            row_chunk=min(field.row_chunk, work_h),
        )
    if isinstance(field, CompositeLayerExposureAdditionField):
        return combine_layer_exposure_addition_fields(
            *(_resample_layer_addition(item, grid) for item in field.fields)
        )
    values = np.asarray(field, dtype=np.float32)
    if values.ndim != 3 or values.shape[:2] != grid.full_shape:
        raise ValueError("adjacency exposure addition must match the full frame")
    return cv2.resize(
        values,
        (work_w, work_h),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float32)


def _provisional_first_development_source(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    program_kind: str,
    *,
    latent_layer_exposure_addition=None,
) -> tuple[np.ndarray, str]:
    material_is_mono = _is_monochrome_material(film)
    if program_kind.startswith("bw_"):
        layer_count = 1 if material_is_mono else 3
        material = _silver_material(film, layer_count)
    elif program_kind.startswith("color_"):
        layer_count = 3
        material = _color_material(film)
    else:
        raise ValueError(f"unsupported adjacency material program: {program_kind}")

    state = _material_latent_state(
        image_linear,
        film,
        material,
        latent_layer_exposure_addition=latent_layer_exposure_addition,
    )
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
    compatibility = _compatibility_for_program(film, program.key, layer_count)
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
    image_linear: np.ndarray,
    film: FilmStockConfig,
    recipe: DevelopRecipeConfig,
    program_kind: str,
    *,
    latent_layer_exposure_addition=None,
    work_long_edge: int = 1800,
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

    image = np.asarray(image_linear, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError("development adjacency requires an HxWx3 linear image")
    image_minimum = float(np.min(image))
    image_maximum = float(np.max(image))
    if (
        not np.isfinite(image_minimum)
        or not np.isfinite(image_maximum)
        or image_minimum < 0.0
    ):
        raise ValueError("development adjacency image must be finite and non-negative")
    grid = global_field_grid(image.shape, max(1, int(work_long_edge)))
    if grid.reduced:
        work_image = cv2.resize(
            image,
            (grid.work_shape[1], grid.work_shape[0]),
            interpolation=cv2.INTER_AREA,
        ).astype(np.float32)
    else:
        work_image = image
    work_addition = _resample_layer_addition(latent_layer_exposure_addition, grid)
    source, first_label = _provisional_first_development_source(
        work_image,
        film,
        recipe,
        program_kind,
        latent_layer_exposure_addition=work_addition,
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
