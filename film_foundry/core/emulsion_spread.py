"""Compact film-emulsion highlight spread before latent-state formation.

This is deliberately separate from halation. It represents short-range
lateral exposure inside emulsion/interlayer material before light reaches the
support or backing. The result therefore couples to material layers and never
becomes a scanner-side glow or a warm RGB overlay.
"""

from __future__ import annotations

import cv2
import numpy as np

from film_foundry.core.halation import soft_threshold
from film_foundry.core.spectral_exposure import exposure_sensitivity_matrix, map_layer_exposure
from film_foundry.contracts.data_kernel import SMITS_EXPOSURE_MODEL_ID
from film_foundry.core.spatial_fields import (
    GlobalLayerExposureAdditionField,
    GlobalRateMultiplierField,
    SpatialFieldPlan,
    global_field_grid,
    resample_global_scalar_field,
)
from film_foundry.model.config import FilmStockConfig


COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN = SpatialFieldPlan(
    key="compact_emulsion_highlight_spread",
    stage="pre_latent_exposure",
    quantity="exposure_addition",
    field_kind="global_scalar",
    requires_global_source=True,
    requires_tile_halo=False,
    random_field_policy="none",
    persistent_state=False,
    disabled_identity_guarantee=True,
)


def _validated_vector(
    value,
    name: str,
    *,
    strictly_positive: bool,
) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32)
    if vector.shape != (3,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"film.{name} must contain three finite values")
    if strictly_positive and np.any(vector <= 0.0):
        raise ValueError(f"film.{name} must be positive")
    if not strictly_positive and np.any(vector < 0.0):
        raise ValueError(f"film.{name} must be non-negative")
    return vector


def _layer_source(
    image: np.ndarray,
    sensitivity: np.ndarray,
    threshold: float,
    softness: float,
) -> np.ndarray:
    """Extract full-resolution excess exposure for one material layer."""
    source = np.multiply(image[..., 0], sensitivity[0], dtype=np.float32)
    source += image[..., 1] * sensitivity[1]
    source += image[..., 2] * sensitivity[2]
    return _excess_source(source, threshold, softness)


def _excess_source(source, threshold, softness):
    gate = soft_threshold(source, threshold, softness)
    source -= np.float32(threshold)
    np.maximum(source, 0.0, out=source)
    source *= gate
    return source


def emulsion_highlight_spread_field(
    image_linear: np.ndarray,
    film: FilmStockConfig,
    *,
    layer_count: int,
    work_long_edge: int,
) -> GlobalLayerExposureAdditionField | None:
    """Build a bounded, layer-specific compact exposure-spread field.

    Highlight evidence is extracted at full resolution one layer at a time.
    Only the already-gated scalar source is reduced to the global work grid,
    where a compact Gaussian models lateral emulsion/interlayer propagation.
    Full-resolution layer rows are reconstructed lazily during material-pool
    consumption.
    """
    strength = float(film.emulsion_highlight_spread_strength)
    if not np.isfinite(strength) or strength < 0.0:
        raise ValueError(
            "film.emulsion_highlight_spread_strength must be finite and non-negative"
        )
    if strength <= 0.0:
        return None

    image = np.asarray(image_linear, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or image.size == 0:
        raise ValueError("emulsion highlight spread image must be a non-empty HxWx3 array")
    minimum = float(np.min(image))
    maximum = float(np.max(image))
    if not np.isfinite(minimum) or not np.isfinite(maximum) or minimum < 0.0:
        raise ValueError("emulsion highlight spread image must be finite and non-negative")
    if int(layer_count) not in {1, 3}:
        raise ValueError("emulsion highlight spread requires one or three material layers")

    radius = float(film.emulsion_highlight_spread_radius)
    threshold = float(film.emulsion_highlight_spread_threshold)
    softness = float(film.emulsion_highlight_spread_softness)
    if not np.isfinite(radius) or radius <= 0.0:
        raise ValueError("film.emulsion_highlight_spread_radius must be finite and positive")
    if not np.isfinite(threshold) or threshold < 0.0:
        raise ValueError(
            "film.emulsion_highlight_spread_threshold must be finite and non-negative"
        )
    if not np.isfinite(softness) or softness <= 0.0:
        raise ValueError(
            "film.emulsion_highlight_spread_softness must be finite and positive"
        )
    radius_scales = _validated_vector(
        film.emulsion_highlight_spread_layer_radius_scales,
        "emulsion_highlight_spread_layer_radius_scales",
        strictly_positive=True,
    )
    layer_strengths = _validated_vector(
        film.emulsion_highlight_spread_layer_strengths,
        "emulsion_highlight_spread_layer_strengths",
        strictly_positive=False,
    )
    smits = film.spectral_exposure_profile is not None and film.spectral_exposure_profile.model == SMITS_EXPOSURE_MODEL_ID
    sensitivity_matrix = None if smits else exposure_sensitivity_matrix(film)
    if layer_count == 1:
        sensitivities = None if smits else sensitivity_matrix.mean(axis=0, keepdims=True)
        radius_scales = np.asarray((float(np.mean(radius_scales)),), dtype=np.float32)
        layer_strengths = np.asarray((float(np.mean(layer_strengths)),), dtype=np.float32)
    else:
        sensitivities = sensitivity_matrix

    edge = int(work_long_edge)
    if edge <= 0:
        raise ValueError("emulsion highlight spread work_long_edge must be positive")
    grid = global_field_grid(image.shape, edge)
    layer_fields: list[GlobalRateMultiplierField] = []
    for layer in range(int(layer_count)):
        if smits:
            mapped = map_layer_exposure(image, film, layer=layer if layer_count == 3 else None)
            source = mapped[..., 0] if layer_count == 3 else mapped.mean(axis=-1)
            source = _excess_source(source, threshold, softness)
            del mapped
        else:
            source = _layer_source(image, sensitivities[layer], threshold, softness)
        work_source = resample_global_scalar_field(source, grid, to_work_grid=True)
        del source
        sigma = max(
            0.35,
            radius * float(radius_scales[layer]) * float(min(grid.work_shape)),
        )
        spread = cv2.GaussianBlur(
            work_source,
            (0, 0),
            sigmaX=sigma,
            sigmaY=sigma,
            borderType=cv2.BORDER_REFLECT_101,
        ).astype(np.float32, copy=False)
        del work_source
        spread *= np.float32(layer_strengths[layer])
        np.maximum(spread, 0.0, out=spread)
        layer_fields.append(GlobalRateMultiplierField(spread, grid))

    return GlobalLayerExposureAdditionField(tuple(layer_fields), strength)
