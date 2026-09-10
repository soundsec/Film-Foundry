"""Build classic film-phenomenon targets and characterize their cost.

This developer-facing tool uses the same formation boundaries as the main
pipeline.  It creates deterministic diagnostic charts for dye/emulsion grain,
compact highlight spread, and support/backing halation, then reports both
formation-side measurements and same-machine compute samples.  Saved PNGs are
diagnostic views only: metrics always read the unmodified float32 material or
exposure fields, and no scanner interpretation participates.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import json
from pathlib import Path
from statistics import mean, median
import time
import tracemalloc
from typing import Callable

import cv2
import numpy as np

from film_foundry.core.atomic_io import atomic_output_directory, atomic_write_json
from film_foundry.core.color import linear_to_srgb
from film_foundry.core.density_grain import (
    FULL_DENSITY_GRAIN_MODEL,
    REDUCED_DENSITY_GRAIN_MODEL,
    apply_density_grain,
)
from film_foundry.core.emulsion_spread import emulsion_highlight_spread_field
from film_foundry.core.halation import (
    FULL_HALATION_SOURCE_MODEL,
    REDUCED_HALATION_SOURCE_MODEL,
    halation_return_field,
)
from film_foundry.core.io_utils import save_image
from film_foundry.core.scanner import layer_density_to_optical_density_rgb
from film_foundry.model.config import (
    DevelopRecipeConfig,
    FilmStockConfig,
    OutputConfig,
)


ArrayMap = dict[str, np.ndarray]
Box = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class ClassicPhenomenaTargets:
    density_layers: np.ndarray
    highlight_exposure: np.ndarray
    density_regions: dict[str, Box]
    highlight_regions: dict[str, Box]
    highlight_points: dict[str, tuple[int, int]]


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "minimum": float(min(samples)),
        "median": float(median(samples)),
        "mean": float(mean(samples)),
        "maximum": float(max(samples)),
    }


def _box(
    height: int,
    width: int,
    y0: float,
    y1: float,
    x0: float,
    x1: float,
) -> Box:
    top = int(np.clip(round(y0 * height), 0, height - 1))
    bottom = int(np.clip(round(y1 * height), top + 1, height))
    left = int(np.clip(round(x0 * width), 0, width - 1))
    right = int(np.clip(round(x1 * width), left + 1, width))
    return top, bottom, left, right


def _region(array: np.ndarray, bounds: Box) -> np.ndarray:
    top, bottom, left, right = bounds
    return array[top:bottom, left:right]


def _fill_box(array: np.ndarray, bounds: Box, value) -> None:
    top, bottom, left, right = bounds
    array[top:bottom, left:right] = value


def _draw_square(
    image: np.ndarray,
    center: tuple[int, int],
    radius: int,
    value: tuple[float, float, float],
) -> Box:
    cy, cx = center
    top = max(0, cy - radius)
    bottom = min(image.shape[0], cy + radius + 1)
    left = max(0, cx - radius)
    right = min(image.shape[1], cx + radius + 1)
    image[top:bottom, left:right] = value
    return top, bottom, left, right


def build_classic_phenomena_targets(
    width: int,
    height: int,
) -> ClassicPhenomenaTargets:
    """Return immutable layer-density and highlight-geometry charts."""

    width = int(width)
    height = int(height)
    if width < 240 or height < 160:
        raise ValueError("classic phenomenon targets require at least 240x160 pixels")

    density = np.full((height, width, 3), 0.10, dtype=np.float32)
    density_regions: dict[str, Box] = {}
    neutral_levels = (0.12, 0.32, 0.68, 1.15, 1.68)
    patch_margin = 0.012
    for index, level in enumerate(neutral_levels):
        left = index / len(neutral_levels) + patch_margin
        right = (index + 1) / len(neutral_levels) - patch_margin
        bounds = _box(height, width, 0.04, 0.57, left, right)
        key = f"neutral_d{level:.2f}"
        density_regions[key] = bounds
        _fill_box(density, bounds, (level, level, level))

    isolated_values = (
        ("cyan_layer", (1.35, 0.16, 0.16)),
        ("magenta_layer", (0.16, 1.35, 0.16)),
        ("yellow_layer", (0.16, 0.16, 1.35)),
    )
    for index, (key, value) in enumerate(isolated_values):
        bounds = _box(
            height,
            width,
            0.64,
            0.96,
            index / 3.0 + 0.025,
            (index + 1) / 3.0 - 0.025,
        )
        density_regions[key] = bounds
        _fill_box(density, bounds, value)

    highlight = np.full((height, width, 3), 0.025, dtype=np.float32)
    points = {
        "white_point": (int(round(0.21 * height)), int(round(0.14 * width))),
        "red_point": (int(round(0.21 * height)), int(round(0.38 * width))),
        "cyan_point": (int(round(0.21 * height)), int(round(0.62 * width))),
        "large_white": (int(round(0.21 * height)), int(round(0.85 * width))),
    }
    point_radius = max(1, int(round(min(height, width) * 0.003)))
    highlight_regions: dict[str, Box] = {}
    highlight_regions["white_point_core"] = _draw_square(
        highlight,
        points["white_point"],
        point_radius,
        (1.6, 1.6, 1.6),
    )
    highlight_regions["red_point_core"] = _draw_square(
        highlight,
        points["red_point"],
        point_radius,
        (4.0, 0.0, 0.0),
    )
    highlight_regions["cyan_point_core"] = _draw_square(
        highlight,
        points["cyan_point"],
        point_radius,
        (0.0, 1.25, 1.25),
    )
    highlight_regions["large_white_core"] = _draw_square(
        highlight,
        points["large_white"],
        point_radius * 4,
        (1.25, 1.25, 1.25),
    )

    line_thickness = max(1, int(round(min(height, width) * 0.003)))
    horizontal = _box(height, width, 0.405, 0.405 + line_thickness / height, 0.06, 0.94)
    vertical = _box(height, width, 0.34, 0.54, 0.50, 0.50 + line_thickness / width)
    highlight_regions["horizontal_line"] = horizontal
    highlight_regions["vertical_line"] = vertical
    _fill_box(highlight, horizontal, (1.35, 1.35, 1.35))
    _fill_box(highlight, vertical, (1.10, 1.10, 1.10))

    hard_edge = _box(height, width, 0.60, 0.95, 0.06, 0.43)
    broad_area = _box(height, width, 0.60, 0.95, 0.58, 0.94)
    highlight_regions["hard_edge_area"] = hard_edge
    highlight_regions["broad_highlight_area"] = broad_area
    _fill_box(highlight, hard_edge, (0.86, 0.86, 0.86))
    _fill_box(highlight, broad_area, (0.94, 0.94, 0.94))

    for array in (density, highlight):
        array.setflags(write=False)
    return ClassicPhenomenaTargets(
        density_layers=density,
        highlight_exposure=highlight,
        density_regions=density_regions,
        highlight_regions=highlight_regions,
        highlight_points=points,
    )


def _target_film() -> FilmStockConfig:
    return FilmStockConfig(
        layer_sensitivity_matrix=(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        ),
        grain_scales=(0.0012, 0.0035, 0.010),
        grain_scale_weights=(0.58, 0.30, 0.12),
        granularity_sigma=(0.030, 0.028, 0.032),
        emulsion_highlight_spread_strength=0.35,
        emulsion_highlight_spread_radius=0.006,
        emulsion_highlight_spread_threshold=0.45,
        emulsion_highlight_spread_softness=0.08,
        emulsion_highlight_spread_layer_radius_scales=(0.85, 1.0, 1.15),
        emulsion_highlight_spread_layer_strengths=(0.90, 1.0, 1.08),
        halation_strength=0.20,
        halation_threshold=0.45,
        halation_softness=0.08,
        halation_peak_threshold=0.01,
        halation_peak_softness=0.04,
        halation_area_suppression=0.90,
        halation_return_model="layer_selective",
        halation_layer_return_weights=(1.0, 0.42, 0.16),
        halation_spread_scale_weights=(0.0, 1.0, 0.0),
    )


def _measure_case(
    runner: Callable[[], ArrayMap],
    repeats: int,
) -> tuple[ArrayMap, dict[str, object]]:
    elapsed_samples: list[float] = []
    peak_samples: list[float] = []
    retained: ArrayMap | None = None
    for repeat in range(int(repeats)):
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        try:
            outputs = {name: np.asarray(value) for name, value in runner().items()}
            elapsed = time.perf_counter() - started
            _, traced_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        if not outputs:
            raise RuntimeError("classic phenomenon benchmark produced no arrays")
        for name, value in outputs.items():
            if value.size == 0 or not np.all(np.isfinite(value)):
                raise RuntimeError(f"classic phenomenon output '{name}' is empty or non-finite")
        elapsed_samples.append(float(elapsed))
        peak_samples.append(float(traced_peak) / (1024.0 * 1024.0))
        if repeat == int(repeats) - 1:
            retained = outputs
        else:
            del outputs
    if retained is None:
        raise RuntimeError("classic phenomenon benchmark did not execute")
    return retained, {
        "elapsed_seconds": _summary(elapsed_samples),
        "python_numpy_traced_peak_mib": _summary(peak_samples),
        "repeats": int(repeats),
    }


def _grain_metrics(
    formed_density: np.ndarray,
    source_density: np.ndarray,
    regions: dict[str, Box],
) -> dict[str, object]:
    delta = np.asarray(formed_density, dtype=np.float32) - source_density
    patches: dict[str, object] = {}
    for name, bounds in regions.items():
        patch = _region(delta, bounds)
        rms_by_layer = np.sqrt(
            np.mean(np.square(patch, dtype=np.float32), axis=(0, 1), dtype=np.float64)
        )
        sigma = max(1.0, min(patch.shape[:2]) * 0.04)
        low_pass = cv2.GaussianBlur(
            patch,
            (0, 0),
            sigmaX=sigma,
            sigmaY=sigma,
            borderType=cv2.BORDER_REFLECT_101,
        )
        total_std = float(np.std(patch, dtype=np.float64))
        patches[name] = {
            "rms_by_layer": [float(value) for value in rms_by_layer],
            "rms_total": float(
                np.sqrt(np.mean(np.square(patch, dtype=np.float32), dtype=np.float64))
            ),
            "low_frequency_std_fraction": float(
                np.std(low_pass, dtype=np.float64) / max(total_std, 1e-12)
            ),
            "horizontal_neighbor_delta": float(
                np.mean(np.abs(np.diff(patch, axis=1)), dtype=np.float64)
            ),
        }

    correlation_patch = _region(delta, regions["neutral_d0.68"])
    correlation = np.corrcoef(
        correlation_patch.reshape(-1, 3),
        rowvar=False,
    )
    return {
        "patches": patches,
        "layer_correlation_matrix": [
            [float(value) for value in row]
            for row in correlation
        ],
        "delta_mean_by_layer": [
            float(value)
            for value in np.mean(delta, axis=(0, 1), dtype=np.float64)
        ],
    }


def _radial_energy_radius(
    field: np.ndarray,
    center: tuple[int, int],
    fraction: float,
    max_radius: int,
) -> float:
    cy, cx = center
    top = max(0, cy - max_radius)
    bottom = min(field.shape[0], cy + max_radius + 1)
    left = max(0, cx - max_radius)
    right = min(field.shape[1], cx + max_radius + 1)
    crop = np.maximum(field[top:bottom, left:right], 0.0)
    energy = np.sum(crop, axis=-1, dtype=np.float64)
    yy, xx = np.indices(energy.shape, dtype=np.float32)
    distances = np.sqrt(
        np.square(yy - float(cy - top)) + np.square(xx - float(cx - left))
    )
    bins = np.floor(distances).astype(np.int32)
    radial = np.bincount(
        bins.ravel(),
        weights=energy.ravel(),
        minlength=max_radius + 1,
    )
    total = float(np.sum(radial, dtype=np.float64))
    if total <= 1e-12:
        return 0.0
    index = int(np.searchsorted(np.cumsum(radial), total * float(fraction)))
    return float(index)


def _highlight_metrics(
    emulsion_layers: np.ndarray,
    halation_layers: np.ndarray,
    targets: ClassicPhenomenaTargets,
) -> dict[str, object]:
    max_radius = max(8, int(round(min(emulsion_layers.shape[:2]) * 0.10)))
    white_point = targets.highlight_points["white_point"]
    compact_r50 = _radial_energy_radius(
        emulsion_layers,
        white_point,
        0.50,
        max_radius,
    )
    compact_r90 = _radial_energy_radius(
        emulsion_layers,
        white_point,
        0.90,
        max_radius,
    )
    halation_r50 = _radial_energy_radius(
        halation_layers,
        white_point,
        0.50,
        max_radius,
    )
    halation_r90 = _radial_energy_radius(
        halation_layers,
        white_point,
        0.90,
        max_radius,
    )

    broad = targets.highlight_regions["broad_highlight_area"]
    top, bottom, left, right = broad
    inset_y = max(1, (bottom - top) // 4)
    inset_x = max(1, (right - left) // 4)
    broad_center = (
        top + inset_y,
        bottom - inset_y,
        left + inset_x,
        right - inset_x,
    )
    emulsion_broad = float(np.mean(_region(emulsion_layers, broad_center), dtype=np.float64))
    halation_broad = float(np.mean(_region(halation_layers, broad_center), dtype=np.float64))

    red_center = targets.highlight_points["red_point"]
    red_radius = max(2, int(round(min(emulsion_layers.shape[:2]) * 0.018)))
    red_box = (
        max(0, red_center[0] - red_radius),
        min(emulsion_layers.shape[0], red_center[0] + red_radius + 1),
        max(0, red_center[1] - red_radius),
        min(emulsion_layers.shape[1], red_center[1] + red_radius + 1),
    )
    emulsion_red = np.sum(_region(emulsion_layers, red_box), axis=(0, 1), dtype=np.float64)
    halation_red = np.sum(_region(halation_layers, red_box), axis=(0, 1), dtype=np.float64)

    hard_edge = targets.highlight_regions["hard_edge_area"]
    edge_top, edge_bottom, _edge_left, edge_right = hard_edge
    edge_y_inset = max(1, (edge_bottom - edge_top) // 5)
    tail_limit = min(
        emulsion_layers.shape[1] - edge_right,
        max(8, int(round(emulsion_layers.shape[1] * 0.12))),
    )

    def edge_tail_distance(field: np.ndarray) -> tuple[float, bool]:
        tail = np.mean(
            field[
                edge_top + edge_y_inset : edge_bottom - edge_y_inset,
                edge_right : edge_right + tail_limit,
            ],
            axis=(0, 2),
            dtype=np.float64,
        )
        peak = float(np.max(tail, initial=0.0))
        active = np.flatnonzero(tail >= peak * 0.10)
        distance = 0.0 if active.size == 0 else float(active[-1] + 1)
        return distance, bool(active.size > 0 and int(active[-1] + 1) >= tail_limit)

    emulsion_edge_tail, emulsion_tail_truncated = edge_tail_distance(emulsion_layers)
    halation_edge_tail, halation_tail_truncated = edge_tail_distance(halation_layers)

    return {
        "isolated_white_point": {
            "emulsion_energy_radius_50_px": compact_r50,
            "emulsion_energy_radius_90_px": compact_r90,
            "halation_energy_radius_50_px": halation_r50,
            "halation_energy_radius_90_px": halation_r90,
        },
        "broad_highlight_center_mean": {
            "emulsion_layer_exposure_addition": emulsion_broad,
            "halation_layer_exposure_addition": halation_broad,
        },
        "saturated_red_point_energy_by_layer": {
            "emulsion": [float(value) for value in emulsion_red],
            "halation": [float(value) for value in halation_red],
        },
        "hard_edge_outside_tail_10_percent_px": {
            "emulsion": emulsion_edge_tail,
            "halation": halation_edge_tail,
            "sample_limit_px": int(tail_limit),
            "emulsion_reached_sample_limit": emulsion_tail_truncated,
            "halation_reached_sample_limit": halation_tail_truncated,
        },
    }


def _tone_map_linear(image: np.ndarray) -> np.ndarray:
    values = np.maximum(np.asarray(image, dtype=np.float32), 0.0)
    values = values / (1.0 + values)
    return linear_to_srgb(values)


def _density_transmission_preview(
    density_layers: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    optical = layer_density_to_optical_density_rgb(density_layers, film)
    transmission = np.power(10.0, -optical).astype(np.float32, copy=False)
    return linear_to_srgb(transmission)


def _signed_delta_preview(delta: np.ndarray) -> np.ndarray:
    values = np.asarray(delta, dtype=np.float32)
    scale = float(np.percentile(np.abs(values), 99.5))
    preview = 0.5 + values * (0.46 / max(scale, 1e-8))
    return np.clip(preview, 0.0, 1.0).astype(np.float32, copy=False)


def _positive_field_preview(field: np.ndarray) -> np.ndarray:
    values = np.maximum(np.asarray(field, dtype=np.float32), 0.0)
    scale = float(np.percentile(values, 99.7))
    return np.clip(values / max(scale, 1e-8), 0.0, 1.0).astype(np.float32, copy=False)


def _contact_sheet(images: tuple[np.ndarray, ...]) -> np.ndarray:
    if not images:
        raise ValueError("contact sheet requires at least one image")
    height = images[0].shape[0]
    separator = np.full((height, 8, 3), 0.18, dtype=np.float32)
    parts: list[np.ndarray] = []
    for index, image in enumerate(images):
        if image.shape != images[0].shape:
            raise ValueError("contact sheet images must have equal shapes")
        if index:
            parts.append(separator)
        parts.append(np.asarray(image, dtype=np.float32))
    return np.concatenate(parts, axis=1)


def run_classic_phenomena_benchmark(
    *,
    width: int = 1200,
    height: int = 800,
    repeats: int = 3,
    seed: int = 20260811,
    work_long_edge: int | None = None,
) -> tuple[dict[str, object], dict[str, np.ndarray]]:
    """Run the focused suite and return a JSON report plus diagnostic views."""

    if int(repeats) <= 0:
        raise ValueError("classic phenomenon benchmark repeats must be positive")
    if work_long_edge is not None and int(work_long_edge) <= 0:
        raise ValueError("work_long_edge must be positive or None")
    targets = build_classic_phenomena_targets(width, height)
    density_before = targets.density_layers.copy()
    highlight_before = targets.highlight_exposure.copy()
    film = _target_film()
    chemistry = DevelopRecipeConfig()

    def grain_runner(model: str) -> Callable[[], ArrayMap]:
        return lambda: {
            "density_layers": apply_density_grain(
                targets.density_layers,
                film,
                chemistry,
                rng=np.random.default_rng(int(seed)),
                work_long_edge=work_long_edge,
                image_polarity="negative",
                component_scope="emulsion",
                model=model,
            )
        }

    full_outputs, full_compute = _measure_case(
        grain_runner(FULL_DENSITY_GRAIN_MODEL),
        repeats,
    )
    reduced_outputs, reduced_compute = _measure_case(
        grain_runner(REDUCED_DENSITY_GRAIN_MODEL),
        repeats,
    )

    def run_emulsion() -> ArrayMap:
        field = emulsion_highlight_spread_field(
            targets.highlight_exposure,
            film,
            layer_count=3,
            work_long_edge=(
                max(height, width)
                if work_long_edge is None
                else int(work_long_edge)
            ),
        )
        if field is None:
            raise RuntimeError("classic emulsion-spread target produced no field")
        layers = np.zeros_like(targets.highlight_exposure, dtype=np.float32)
        field.add_scaled_to(layers, np.ones(3, dtype=np.float32))
        return {"layer_exposure_addition": layers}

    emulsion_outputs, emulsion_compute = _measure_case(run_emulsion, repeats)

    def run_halation(source_model: str) -> ArrayMap:
        scalar = halation_return_field(
            targets.highlight_exposure,
            film,
            work_long_edge=work_long_edge,
            spread_scale_weights=film.halation_spread_scale_weights,
            source_model=source_model,
        )
        if scalar is None:
            raise RuntimeError("classic halation target produced no return field")
        weights = np.asarray(film.halation_layer_return_weights, dtype=np.float32)
        layers = scalar[..., None] * weights.reshape(1, 1, 3)
        layers *= np.float32(film.halation_strength)
        return {"layer_exposure_addition": layers.astype(np.float32, copy=False)}

    halation_outputs, halation_compute = _measure_case(
        lambda: run_halation(FULL_HALATION_SOURCE_MODEL),
        repeats,
    )
    reduced_halation_outputs, reduced_halation_compute = _measure_case(
        lambda: run_halation(REDUCED_HALATION_SOURCE_MODEL),
        repeats,
    )

    full_density = full_outputs["density_layers"]
    reduced_density = reduced_outputs["density_layers"]
    emulsion_layers = emulsion_outputs["layer_exposure_addition"]
    halation_layers = halation_outputs["layer_exposure_addition"]
    reduced_halation_layers = reduced_halation_outputs[
        "layer_exposure_addition"
    ]
    full_metrics = _grain_metrics(
        full_density,
        targets.density_layers,
        targets.density_regions,
    )
    reduced_metrics = _grain_metrics(
        reduced_density,
        targets.density_layers,
        targets.density_regions,
    )
    highlight_metrics = _highlight_metrics(
        emulsion_layers,
        halation_layers,
        targets,
    )
    reduced_highlight_metrics = _highlight_metrics(
        emulsion_layers,
        reduced_halation_layers,
        targets,
    )
    halation_model_comparison = {
        "total_energy_ratio_reduced_to_full": float(
            np.sum(reduced_halation_layers, dtype=np.float64)
            / max(np.sum(halation_layers, dtype=np.float64), 1e-12)
        ),
        "mean_absolute_layer_exposure_difference": float(
            np.mean(
                np.abs(reduced_halation_layers - halation_layers),
                dtype=np.float64,
            )
        ),
        "maximum_absolute_layer_exposure_difference": float(
            np.max(np.abs(reduced_halation_layers - halation_layers))
        ),
    }

    full_patches = full_metrics["patches"]
    reduced_patches = reduced_metrics["patches"]
    full_mid_patch = full_patches["neutral_d0.68"]
    reduced_mid_patch = reduced_patches["neutral_d0.68"]
    reduced_layer_correlation = np.asarray(
        reduced_metrics["layer_correlation_matrix"],
        dtype=np.float64,
    )
    reduced_off_diagonal_correlation = reduced_layer_correlation[
        ~np.eye(reduced_layer_correlation.shape[0], dtype=bool)
    ]
    grain_comparison = {
        "mid_density_rms_ratio_reduced_to_full": float(
            reduced_mid_patch["rms_total"]
            / max(full_mid_patch["rms_total"], 1e-12)
        ),
        "mid_density_low_frequency_fraction_ratio_reduced_to_full": float(
            reduced_mid_patch["low_frequency_std_fraction"]
            / max(full_mid_patch["low_frequency_std_fraction"], 1e-12)
        ),
        "mid_density_neighbor_delta_ratio_reduced_to_full": float(
            reduced_mid_patch["horizontal_neighbor_delta"]
            / max(full_mid_patch["horizontal_neighbor_delta"], 1e-12)
        ),
        "maximum_reduced_layer_off_diagonal_correlation": float(
            np.max(reduced_off_diagonal_correlation)
        ),
    }
    radii = highlight_metrics["isolated_white_point"]
    red_energy = highlight_metrics["saturated_red_point_energy_by_layer"]
    edge_tails = highlight_metrics["hard_edge_outside_tail_10_percent_px"]
    broad_center = highlight_metrics["broad_highlight_center_mean"]
    reduced_radii = reduced_highlight_metrics["isolated_white_point"]
    reduced_edge_tails = reduced_highlight_metrics[
        "hard_edge_outside_tail_10_percent_px"
    ]
    reduced_broad_center = reduced_highlight_metrics[
        "broad_highlight_center_mean"
    ]
    checks = {
        "full_grain_rises_out_of_dmin": bool(
            full_patches["neutral_d0.68"]["rms_total"]
            > full_patches["neutral_d0.12"]["rms_total"]
        ),
        "reduced_grain_rises_out_of_dmin": bool(
            reduced_patches["neutral_d0.68"]["rms_total"]
            > reduced_patches["neutral_d0.12"]["rms_total"]
        ),
        "full_grain_layers_are_partly_correlated": bool(
            0.02 < full_metrics["layer_correlation_matrix"][0][1] < 0.98
        ),
        "reduced_grain_layers_are_partly_correlated": bool(
            0.02
            < grain_comparison[
                "maximum_reduced_layer_off_diagonal_correlation"
            ]
            <= 0.25
        ),
        "reduced_grain_preserves_mid_density_rms_budget": bool(
            0.80
            <= grain_comparison["mid_density_rms_ratio_reduced_to_full"]
            <= 1.20
        ),
        "reduced_grain_retains_a_low_frequency_cloud_component": bool(
            grain_comparison[
                "mid_density_low_frequency_fraction_ratio_reduced_to_full"
            ]
            >= 0.65
        ),
        "emulsion_spread_is_more_compact_than_halation": bool(
            radii["emulsion_energy_radius_90_px"]
            < radii["halation_energy_radius_90_px"]
        ),
        "hard_edge_halation_tail_is_wider_than_emulsion_spread": bool(
            edge_tails["emulsion"] < edge_tails["halation"]
        ),
        "broad_highlight_is_not_misread_as_dominant_halation": bool(
            broad_center["emulsion_layer_exposure_addition"]
            > 5.0 * broad_center["halation_layer_exposure_addition"]
        ),
        "emulsion_spread_retains_red_source_layer": bool(
            red_energy["emulsion"][0]
            > 20.0 * max(red_energy["emulsion"][1], red_energy["emulsion"][2], 1e-12)
        ),
        "halation_uses_fixed_support_return_layer_weights": bool(
            np.isclose(
                red_energy["halation"][1]
                / max(red_energy["halation"][0], 1e-12),
                film.halation_layer_return_weights[1],
                rtol=0.03,
                atol=1e-5,
            )
            and np.isclose(
                red_energy["halation"][2]
                / max(red_energy["halation"][0], 1e-12),
                film.halation_layer_return_weights[2],
                rtol=0.03,
                atol=1e-5,
            )
        ),
        "reduced_halation_preserves_point_return_radius": bool(
            abs(
                reduced_radii["halation_energy_radius_90_px"]
                - radii["halation_energy_radius_90_px"]
            )
            <= max(3.0, 0.15 * radii["halation_energy_radius_90_px"])
        ),
        "reduced_halation_preserves_longer_hard_edge_tail": bool(
            reduced_edge_tails["halation"] > edge_tails["emulsion"]
        ),
        "reduced_halation_retains_broad_area_suppression": bool(
            reduced_broad_center["halation_layer_exposure_addition"]
            < 0.20
            * reduced_broad_center["emulsion_layer_exposure_addition"]
        ),
        "reduced_halation_preserves_energy_budget": bool(
            0.70
            <= halation_model_comparison["total_energy_ratio_reduced_to_full"]
            <= 1.30
        ),
    }

    source_unchanged = bool(
        np.array_equal(targets.density_layers, density_before)
        and np.array_equal(targets.highlight_exposure, highlight_before)
    )
    artifacts = {
        "00_density_target_transmission.png": _density_transmission_preview(
            targets.density_layers,
            film,
        ),
        "01_highlight_geometry_target.png": _tone_map_linear(
            targets.highlight_exposure,
        ),
        "10_full_grain_transmission.png": _density_transmission_preview(
            full_density,
            film,
        ),
        "11_reduced_grain_transmission.png": _density_transmission_preview(
            reduced_density,
            film,
        ),
        "12_full_grain_delta_false_color.png": _signed_delta_preview(
            full_density - targets.density_layers,
        ),
        "13_reduced_grain_delta_false_color.png": _signed_delta_preview(
            reduced_density - targets.density_layers,
        ),
        "20_emulsion_highlight_spread_false_color.png": _positive_field_preview(
            emulsion_layers,
        ),
        "21_halation_return_false_color.png": _positive_field_preview(
            halation_layers,
        ),
        "22_reduced_halation_return_false_color.png": _positive_field_preview(
            reduced_halation_layers,
        ),
    }
    artifacts["30_grain_comparison_left_target_full_reduced.png"] = _contact_sheet(
        (
            artifacts["00_density_target_transmission.png"],
            artifacts["10_full_grain_transmission.png"],
            artifacts["11_reduced_grain_transmission.png"],
        )
    )
    artifacts["31_highlight_comparison_left_target_spread_halation.png"] = _contact_sheet(
        (
            artifacts["01_highlight_geometry_target.png"],
            artifacts["20_emulsion_highlight_spread_false_color.png"],
            artifacts["21_halation_return_false_color.png"],
            artifacts["22_reduced_halation_return_false_color.png"],
        )
    )

    report: dict[str, object] = {
        "schema_version": 1,
        "purpose": "classic_film_phenomena_visual_and_compute_characterization",
        "image": {
            "width": int(width),
            "height": int(height),
            "pixels": int(width * height),
        },
        "runtime": {
            "seed": int(seed),
            "repeats": int(repeats),
            "work_long_edge": (
                None if work_long_edge is None else int(work_long_edge)
            ),
            "scanner_used": False,
            "source_arrays_unchanged": source_unchanged,
        },
        "formation_contract": {
            "dye_grain_stage": "material_layer_density",
            "emulsion_spread_stage": "pre_latent_exposure",
            "halation_stage": "pre_latent_exposure_support_or_backing_return",
            "full_grain_model": FULL_DENSITY_GRAIN_MODEL,
            "reduced_grain_model": REDUCED_DENSITY_GRAIN_MODEL,
            "full_halation_source_model": FULL_HALATION_SOURCE_MODEL,
            "reduced_halation_source_model": REDUCED_HALATION_SOURCE_MODEL,
        },
        "compute_performance": {
            "dye_grain_full": full_compute,
            "dye_grain_reduced": reduced_compute,
            "emulsion_highlight_spread": emulsion_compute,
            "halation_return": halation_compute,
            "halation_return_reduced": reduced_halation_compute,
        },
        "phenomenon_characterization": {
            "dye_grain_full": full_metrics,
            "dye_grain_reduced": reduced_metrics,
            "grain_model_comparison": grain_comparison,
            "highlight_spread_and_halation": highlight_metrics,
            "highlight_spread_and_reduced_halation": reduced_highlight_metrics,
            "halation_model_comparison": halation_model_comparison,
        },
        "directional_checks": checks,
        "artifacts": list(artifacts),
        "notes": [
            "PNG contrast is diagnostic and may be enhanced independently per field.",
            "All measurements use unenhanced float32 formation-side arrays.",
            "Traced peak excludes immutable target arrays allocated before each case.",
            "Timing is a same-machine comparison, not a cross-machine promise.",
            "Directional checks validate model roles; they do not calibrate a real film stock.",
        ],
    }
    return report, artifacts


def write_classic_phenomena_bundle(
    output_dir: str | Path,
    report: dict[str, object],
    artifacts: dict[str, np.ndarray],
) -> Path:
    """Atomically replace one complete diagnostic bundle."""

    target = Path(output_dir)
    output_config = OutputConfig(
        bit_depth=8,
        watermark_metadata=False,
        anti_banding_strength=0.0,
    )
    with atomic_output_directory(target) as staging:
        for name, image in artifacts.items():
            save_image(image, staging / name, output_config)
        atomic_write_json(staging / "report.json", report)
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument(
        "--work-long-edge",
        type=int,
        help="Optional explicit work-grid edge; omitted means native target size.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report, artifacts = run_classic_phenomena_benchmark(
        width=args.width,
        height=args.height,
        repeats=args.repeats,
        seed=args.seed,
        work_long_edge=args.work_long_edge,
    )
    output_dir = write_classic_phenomena_bundle(
        args.output_dir,
        report,
        artifacts,
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "directional_checks": report["directional_checks"],
                "compute_performance": report["compute_performance"],
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
