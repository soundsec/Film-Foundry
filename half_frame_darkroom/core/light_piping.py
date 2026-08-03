"""Reduced material-side light piping from explicitly declared film edges."""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.core.spatial_fields import (
    EdgeExposureAdditionField,
    SpatialFieldPlan,
)
from half_frame_darkroom.model.config import FilmStockConfig


REDUCED_LIGHT_PIPING_PLAN = SpatialFieldPlan(
    key="reduced_support_light_piping",
    stage="pre_latent_exposure",
    quantity="exposure_addition",
    field_kind="tile_local",
    requires_global_source=False,
    requires_tile_halo=False,
    random_field_policy="none",
    persistent_state=False,
    disabled_identity_guarantee=True,
)


LIGHT_PIPING_EDGE_MODES = {
    "none",
    "top",
    "right",
    "bottom",
    "left",
    "long_edges",
    "short_edges",
    "all_edges",
}


def _edge_weights(mode: str, shape: tuple[int, int]) -> tuple[float, float, float, float]:
    height, width = shape
    mode = str(mode).strip().lower()
    if mode == "none":
        return (0.0, 0.0, 0.0, 0.0)
    if mode == "top":
        return (1.0, 0.0, 0.0, 0.0)
    if mode == "right":
        return (0.0, 1.0, 0.0, 0.0)
    if mode == "bottom":
        return (0.0, 0.0, 1.0, 0.0)
    if mode == "left":
        return (0.0, 0.0, 0.0, 1.0)
    if mode == "all_edges":
        return (1.0, 1.0, 1.0, 1.0)
    horizontal_are_long = width >= height
    if mode == "long_edges":
        return (1.0, 0.0, 1.0, 0.0) if horizontal_are_long else (0.0, 1.0, 0.0, 1.0)
    if mode == "short_edges":
        return (0.0, 1.0, 0.0, 1.0) if horizontal_are_long else (1.0, 0.0, 1.0, 0.0)
    raise ValueError(f"unsupported light-piping edge mode: {mode}")


def light_piping_exposure_field(
    image_shape: tuple[int, ...],
    film: FilmStockConfig,
    *,
    layer_count: int,
) -> EdgeExposureAdditionField | None:
    height, width = int(image_shape[0]), int(image_shape[1])
    strength = float(getattr(film, "light_piping_strength", 0.0))
    mode = str(getattr(film, "light_piping_edge_mode", "none")).strip().lower()
    if strength <= 1e-8 or mode == "none":
        return None
    weights = np.asarray(
        getattr(film, "light_piping_layer_weights", (1.0, 0.45, 0.18)),
        dtype=np.float32,
    ).reshape(3)
    layer_weights = (
        (float(np.mean(weights)),)
        if int(layer_count) == 1
        else tuple(float(value) for value in weights)
    )
    return EdgeExposureAdditionField(
        full_shape=(height, width),
        layer_weights=layer_weights,
        edge_weights=_edge_weights(mode, (height, width)),
        strength=strength,
        depth_scale=float(getattr(film, "light_piping_depth", 0.035)),
    )
