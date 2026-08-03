"""Compatibility adapters between the current density engine and final media."""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.core.film_process.model import FilmFinalMedium, ReducedFilmMaterial
from half_frame_darkroom.model.config import FilmStockConfig


def reduced_material_from_film_stock(film: FilmStockConfig) -> ReducedFilmMaterial:
    """Create normalized material capacities from the current film config.

    Current presets describe final density response rather than chemical pool
    capacities.  Unit capacities are therefore intentional: H-D and process
    controls can be migrated independently while optical coefficients remain
    identical to existing presets.
    """
    color_process = str(getattr(film, "color_process", "color")).strip().lower()
    is_monochrome = color_process in {"bw", "black_white", "monochrome"}
    return ReducedFilmMaterial(
        key=str(film.name),
        layer_count=3,
        halide_capacity=(1.0, 1.0, 1.0),
        coupler_capacity=None if is_monochrome else (1.0, 1.0, 1.0),
        dye_absorption_matrix=(
            None
            if is_monochrome
            else tuple(tuple(float(v) for v in row) for row in film.dye_absorption_matrix)
        ),
        silver_density_per_layer=(1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0),
        residual_halide_density_per_layer=(0.03, 0.03, 0.03),
        base_density_rgb=tuple(float(v) for v in film.film_base_density_rgb),
        clear_support_density_rgb=None,
        masking_coupler_density_rgb=None,
        medium_family=str(getattr(film, "medium_family", "film")),
        color_system="silver_bw" if is_monochrome else "color_coupler",
    )


def final_medium_from_legacy_density(
    density_layers: np.ndarray,
    film: FilmStockConfig,
    *,
    image_polarity: str = "negative",
    process_key: str = "legacy_density",
    compatible_interpreters: tuple[str, ...] | None = None,
) -> FilmFinalMedium:
    """Wrap current CMY density output as an immutable final film medium.

    The legacy engine has already collapsed chemistry into optical layer
    density, so its layer values are represented as a dye-density proxy here.
    This preserves current pixels exactly while scanner code migrates to the
    final-medium observation API.
    """
    density = np.asarray(density_layers, dtype=np.float32)
    if density.ndim < 1 or density.shape[-1] != 3:
        raise ValueError(f"legacy density must end with three layers, got {density.shape}")
    polarity = str(image_polarity).lower()
    if compatible_interpreters is None:
        compatible_interpreters = (
            ("negative_scan",) if polarity == "negative" else ("positive_transparency_scan",)
        )
    zeros = np.zeros_like(density, dtype=np.float32)
    return FilmFinalMedium(
        material_key=str(film.name),
        metallic_silver=zeros,
        dye=density,
        residual_halide=zeros,
        bleached_halide=None,
        dye_absorption_matrix=tuple(tuple(float(v) for v in row) for row in film.dye_absorption_matrix),
        silver_density_per_layer=(0.0, 0.0, 0.0),
        residual_halide_density_per_layer=(0.0, 0.0, 0.0),
        base_density_rgb=tuple(float(v) for v in film.film_base_density_rgb),
        clear_support_density_rgb=None,
        masking_coupler_density_rgb=None,
        masking_coupler_remaining=1.0,
        auxiliary_density_rgb=(0.0, 0.0, 0.0),
        auxiliary_remaining=0.0,
        image_polarity=polarity,
        view_mode="transmissive",
        compatible_interpreters=tuple(str(v) for v in compatible_interpreters),
        process_key=str(process_key),
    )
