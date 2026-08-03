"""Audit exact tiling and global-grid contracts without changing production code.

The audit deliberately calls the same material, spatial-field, grain, and
output-only scan boundaries used by Film Foundry.  It compares continuous and
tiled execution on adversarial bands and boundary-aligned highlights, reports
whole-array and seam-local errors, and fails when a contract exceeds its
declared exact or numerical tolerance.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
from typing import Callable

import numpy as np

from film_foundry.tools.benchmark_pipeline import synthetic_reference_image
from half_frame_darkroom.core import engine
from half_frame_darkroom.core.atomic_io import atomic_write_json
from half_frame_darkroom.core.development_adjacency import (
    build_development_adjacency_field,
)
from half_frame_darkroom.core.engine import develop_negative
from half_frame_darkroom.core.halation import (
    halation_source_energy,
    halation_source_energy_tiled,
    spread_multiscale_halation_work_source,
)
from half_frame_darkroom.core.silver_grain import (
    SilverGrainPlan,
    apply_metallic_silver_grain,
)
from half_frame_darkroom.core.spatial_fields import global_field_grid
from half_frame_darkroom.model.config import (
    DarkroomConfig,
    DevelopRecipeConfig,
    FilmStockConfig,
)


def _sha256(array: np.ndarray) -> str:
    values = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode("ascii"))
    digest.update(repr(tuple(int(value) for value in values.shape)).encode("ascii"))
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


def _timed(call: Callable[[], object]) -> tuple[object, float]:
    started = time.perf_counter()
    result = call()
    return result, float(time.perf_counter() - started)


def _boundary_rows(height: int, tile_rows: int) -> tuple[int, ...]:
    return tuple(range(int(tile_rows), int(height), int(tile_rows)))


def _comparison(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    tile_rows: int,
) -> dict[str, object]:
    expected = np.asarray(reference)
    actual = np.asarray(candidate)
    if expected.shape != actual.shape or expected.dtype != actual.dtype:
        raise RuntimeError(
            "grid audit shape/dtype mismatch: "
            f"{expected.shape}/{expected.dtype} != {actual.shape}/{actual.dtype}"
        )
    if expected.size == 0:
        raise RuntimeError("grid audit arrays must not be empty")
    if not np.all(np.isfinite(expected)) or not np.all(np.isfinite(actual)):
        raise RuntimeError("grid audit arrays must be finite")

    difference = np.abs(
        actual.astype(np.float64, copy=False)
        - expected.astype(np.float64, copy=False)
    )
    boundaries = (
        _boundary_rows(expected.shape[0], tile_rows)
        if expected.ndim >= 2
        else ()
    )
    boundary_max = 0.0
    boundary_jump_delta = 0.0
    for boundary in boundaries:
        start = max(0, boundary - 1)
        stop = min(expected.shape[0], boundary + 1)
        boundary_max = max(boundary_max, float(np.max(difference[start:stop])))
        expected_jump = expected[boundary].astype(np.float64) - expected[boundary - 1].astype(np.float64)
        actual_jump = actual[boundary].astype(np.float64) - actual[boundary - 1].astype(np.float64)
        boundary_jump_delta = max(
            boundary_jump_delta,
            float(np.max(np.abs(actual_jump - expected_jump))),
        )
    report = {
        "exact_equal": bool(np.array_equal(actual, expected)),
        "reference_sha256": _sha256(expected),
        "candidate_sha256": _sha256(actual),
        "max_abs_error": float(np.max(difference)),
        "mean_abs_error": float(np.mean(difference, dtype=np.float64)),
        "boundary_max_abs_error": float(boundary_max),
        "boundary_jump_delta": float(boundary_jump_delta),
        "boundary_count": len(boundaries),
    }
    del difference
    return report


def _assert_comparison(
    label: str,
    comparison: dict[str, object],
    *,
    tolerance: float,
) -> None:
    if float(comparison["max_abs_error"]) > float(tolerance):
        raise RuntimeError(
            f"{label} exceeded grid tolerance {tolerance}: "
            f"max={comparison['max_abs_error']}, "
            f"boundary={comparison['boundary_max_abs_error']}, "
            f"jump={comparison['boundary_jump_delta']}"
        )


def _adversarial_image(
    width: int,
    height: int,
    tile_rows: tuple[int, ...],
) -> np.ndarray:
    image = synthetic_reference_image(width, height).astype(np.float32, copy=True)
    bands = (
        (0.04, 0.12, 0.92),
        (0.94, 0.04, 0.03),
        (0.05, 0.90, 0.12),
        (0.96, 0.76, 0.08),
    )
    edges = np.linspace(0, height, len(bands) + 1, dtype=np.int32)
    horizontal = np.linspace(0.68, 1.0, width, dtype=np.float32)[None, :, None]
    for index, color in enumerate(bands):
        start = int(edges[index])
        stop = int(edges[index + 1])
        image[start:stop] = np.asarray(color, dtype=np.float32) * horizontal
    image[:, width // 2 :] = np.clip(image[:, width // 2 :] * 0.72 + 0.19, 0.0, 1.0)
    for index, rows in enumerate(tile_rows):
        for boundary in _boundary_rows(height, rows):
            left = (index * 37 + boundary * 11) % max(width - 3, 1)
            image[max(0, boundary - 1) : min(height, boundary + 2), left : left + 3] = (
                1.0,
                0.94,
                0.72,
            )
    return np.asarray(image, dtype=np.float32)


def _program_config(program_key: str, seed: int) -> DarkroomConfig:
    config = DarkroomConfig()
    config.chemistry.program_key = str(program_key)
    config.mode = "bw_negative" if str(program_key).startswith("bw_") else "color_negative"
    config.film.color_process = "bw" if str(program_key).startswith("bw_") else "color"
    config.film.medium_process = "negative"
    config.film.image_polarity = "negative"
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)
    config.enable_mtf = False
    config.enable_halation = False
    config.enable_grain = False
    config.scanner.scan_normalize = False
    if program_key == "color_negative_bleach_bypass":
        config.chemistry.silver_bleach_completion = 0.28
    return config


def _medium_arrays(medium) -> dict[str, np.ndarray]:
    arrays = {
        "density_cmy": np.asarray(medium.density_cmy),
        "density_grain": np.asarray(medium.density_grain),
    }
    if medium.optical_density_rgb is not None:
        arrays["optical_density_rgb"] = np.asarray(medium.optical_density_rgb)
    if medium.clear_base_optical_density_rgb is not None:
        arrays["clear_base_optical_density_rgb"] = np.asarray(
            medium.clear_base_optical_density_rgb
        )
    return arrays


def _audit_material_programs(
    image: np.ndarray,
    tile_rows: tuple[int, ...],
    seed: int,
) -> dict[str, object]:
    programs = (
        "color_negative",
        "color_negative_bleach_bypass",
        "bw_negative",
        "bw_reversal",
        "color_reversal",
    )
    report: dict[str, object] = {}
    for program in programs:
        baseline_config = _program_config(program, seed)
        baseline_config.processing.material_tile_rows = 0
        baseline, baseline_seconds = _timed(
            lambda config=baseline_config: develop_negative(image, config)
        )
        baseline_arrays = _medium_arrays(baseline)
        tiled_runs: dict[str, object] = {}
        for rows in tile_rows:
            tiled_config = _program_config(program, seed)
            tiled_config.processing.material_tile_rows = int(rows)
            tiled_config.processing.material_tile_threshold_megapixels = 1e-12
            candidate, elapsed = _timed(
                lambda config=tiled_config: develop_negative(image, config)
            )
            candidate_arrays = _medium_arrays(candidate)
            comparisons: dict[str, object] = {}
            for name, reference in baseline_arrays.items():
                comparison = _comparison(
                    reference,
                    candidate_arrays[name],
                    tile_rows=rows,
                )
                _assert_comparison(
                    f"material/{program}/{name}/rows={rows}",
                    comparison,
                    tolerance=0.0,
                )
                comparisons[name] = comparison
            tiled_runs[str(rows)] = {
                "elapsed_seconds": elapsed,
                "comparisons": comparisons,
            }
        report[program] = {
            "untiled_elapsed_seconds": baseline_seconds,
            "tiles": tiled_runs,
        }
    return report


def _spatial_stack_config(seed: int) -> DarkroomConfig:
    config = _program_config("color_negative_bleach_bypass", seed)
    config.enable_halation = True
    config.enable_grain = True
    config.film.halation_return_model = "layer_selective"
    config.film.halation_spread_scale_weights = (0.20, 0.60, 0.20)
    config.film.halation_outer_radius = 0.030
    config.film.light_piping_strength = 0.42
    config.film.light_piping_edge_mode = "all_edges"
    config.film.silver_grain_strength = 0.085
    config.chemistry.development_adjacency_strength = 0.74
    config.chemistry.development_adjacency_radius = 0.018
    config.chemistry.uneven_development = 0.67
    config.chemistry.agitation = 0.42
    config.chemistry.developer_exhaustion = 0.24
    config.processing.halation_work_long_edge = 111
    config.processing.adjacency_work_long_edge = 107
    config.processing.grain_work_long_edge = 127
    return config


def _audit_spatial_stack(
    image: np.ndarray,
    tile_rows: tuple[int, ...],
    seed: int,
) -> dict[str, object]:
    baseline_config = _spatial_stack_config(seed)
    baseline_config.processing.material_tile_rows = 0
    baseline, baseline_seconds = _timed(
        lambda: develop_negative(image, baseline_config)
    )
    baseline_arrays = _medium_arrays(baseline)
    tiled_runs: dict[str, object] = {}
    for rows in tile_rows:
        config = _spatial_stack_config(seed)
        config.processing.material_tile_rows = int(rows)
        config.processing.material_tile_threshold_megapixels = 1e-12
        candidate, elapsed = _timed(lambda config=config: develop_negative(image, config))
        candidate_arrays = _medium_arrays(candidate)
        comparisons: dict[str, object] = {}
        for name, reference in baseline_arrays.items():
            comparison = _comparison(
                reference,
                candidate_arrays[name],
                tile_rows=rows,
            )
            _assert_comparison(
                f"spatial_stack/{name}/rows={rows}",
                comparison,
                tolerance=0.0,
            )
            comparisons[name] = comparison
        tiled_runs[str(rows)] = {
            "elapsed_seconds": elapsed,
            "comparisons": comparisons,
        }
    return {
        "untiled_elapsed_seconds": baseline_seconds,
        "tiles": tiled_runs,
    }


def _scan_output(medium, config: DarkroomConfig) -> np.ndarray:
    if str(medium.image_polarity).lower() == "positive":
        return engine._scan_positive_output_only(medium, config)
    return engine._scan_negative_output_only(medium, config)


def _audit_scan_tiling(
    image: np.ndarray,
    tile_rows: tuple[int, ...],
    seed: int,
) -> dict[str, object]:
    media = {
        "negative": develop_negative(image, _program_config("color_negative", seed)),
        "positive": develop_negative(image, _program_config("color_reversal", seed)),
    }
    report: dict[str, object] = {}
    for polarity, medium in media.items():
        modes: dict[str, object] = {}
        for normalization_mode in ("luma", "rgb"):
            baseline_config = copy.deepcopy(medium.metadata["runtime_config"])
            baseline_config.processing.scan_tile_rows = 0
            baseline_config.scanner.scan_normalize = True
            baseline_config.scanner.scan_normalize_mode = normalization_mode
            baseline, baseline_seconds = _timed(
                lambda config=baseline_config: _scan_output(medium, config)
            )
            tiled_runs: dict[str, object] = {}
            for rows in tile_rows:
                config = copy.deepcopy(medium.metadata["runtime_config"])
                config.processing.scan_tile_rows = int(rows)
                config.processing.scan_tile_threshold_megapixels = 1e-12
                config.scanner.scan_normalize = True
                config.scanner.scan_normalize_mode = normalization_mode
                candidate, elapsed = _timed(
                    lambda config=config: _scan_output(medium, config)
                )
                comparison = _comparison(
                    baseline,
                    candidate,
                    tile_rows=rows,
                )
                _assert_comparison(
                    f"scan/{polarity}/{normalization_mode}/rows={rows}",
                    comparison,
                    tolerance=1e-7,
                )
                tiled_runs[str(rows)] = {
                    "elapsed_seconds": elapsed,
                    "comparison": comparison,
                }
            modes[normalization_mode] = {
                "untiled_elapsed_seconds": baseline_seconds,
                "tiles": tiled_runs,
            }
        report[polarity] = modes
    return report


def _audit_halation_source(
    image: np.ndarray,
    tile_rows: tuple[int, ...],
) -> dict[str, object]:
    film = FilmStockConfig()
    reference, baseline_seconds = _timed(lambda: halation_source_energy(image, film))
    tiled_runs: dict[str, object] = {}
    for rows in tile_rows:
        candidate, elapsed = _timed(
            lambda rows=rows: halation_source_energy_tiled(
                image,
                film,
                tile_rows=rows,
            )
        )
        comparison = _comparison(reference, candidate, tile_rows=rows)
        _assert_comparison(
            f"halation_source/rows={rows}",
            comparison,
            # OpenCV's float32 Gaussian/Sobel ordering can differ by a small
            # fraction of one ULP between a full frame and an extreme
            # one-row halo stripe.  257x173 stress frames reach ~2.94e-8;
            # keep the bound far below scanner tolerance without falsely
            # classifying this rounding as row overlap.
            tolerance=5e-8,
        )
        tiled_runs[str(rows)] = {
            "elapsed_seconds": elapsed,
            "comparison": comparison,
        }
    return {
        "untiled_elapsed_seconds": baseline_seconds,
        "tiles": tiled_runs,
    }


def _audit_lazy_adjacency(
    image: np.ndarray,
    tile_rows: tuple[int, ...],
) -> dict[str, object]:
    field, audit = build_development_adjacency_field(
        image,
        FilmStockConfig(),
        DevelopRecipeConfig(
            program_key="color_negative",
            development_adjacency_strength=0.82,
            development_adjacency_radius=0.021,
        ),
        "color_negative",
        work_long_edge=113,
    )
    if field is None:
        raise RuntimeError("grid audit adjacency field was not built")
    reference = field.slice_rows(0, image.shape[0])
    tiled_runs: dict[str, object] = {}
    for rows in tile_rows:
        candidate = np.concatenate(
            [
                field.slice_rows(start, min(start + rows, image.shape[0]))
                for start in range(0, image.shape[0], rows)
            ],
            axis=0,
        )
        comparison = _comparison(reference, candidate, tile_rows=rows)
        _assert_comparison(
            f"development_adjacency/rows={rows}",
            comparison,
            tolerance=0.0,
        )
        tiled_runs[str(rows)] = comparison
    return {"field_audit": audit, "tiles": tiled_runs}


def _audit_silver_grain(
    width: int,
    height: int,
    tile_rows: tuple[int, ...],
    seed: int,
) -> dict[str, object]:
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    x = np.linspace(0.08, 0.94, width, dtype=np.float32)[None, :]
    silver = np.asarray(y * x, dtype=np.float32)
    reference = np.full((height, width, 3), 0.82, dtype=np.float32)
    plan = SilverGrainPlan(
        full_shape=(height, width),
        seed=int(seed),
        strength=0.09,
        radius=0.003,
        clump_mix=0.27,
        tile_rows=max(tile_rows),
    )
    apply_metallic_silver_grain(reference, silver, plan)
    tiled_runs: dict[str, object] = {}
    for rows in tile_rows:
        candidate = np.full((height, width, 3), 0.82, dtype=np.float32)
        for start in range(0, height, rows):
            stop = min(start + rows, height)
            apply_metallic_silver_grain(
                candidate[start:stop],
                silver[start:stop],
                plan,
                row_offset=start,
            )
        comparison = _comparison(reference, candidate, tile_rows=rows)
        _assert_comparison(
            f"silver_grain/rows={rows}",
            comparison,
            tolerance=0.0,
        )
        tiled_runs[str(rows)] = comparison
    return {"tiles": tiled_runs}


def _audit_orientation() -> dict[str, object]:
    film = FilmStockConfig(
        halation_outer_radius=0.05,
        halation_exponential_radius=0.022,
    )
    landscape_grid = global_field_grid((91, 137), 67)
    portrait_grid = global_field_grid((137, 91), 67)
    landscape_source = np.zeros(landscape_grid.work_shape, dtype=np.float32)
    landscape_source[landscape_source.shape[0] // 2, landscape_source.shape[1] // 2] = 1.0
    portrait_source = landscape_source.T.copy()
    landscape = spread_multiscale_halation_work_source(
        landscape_source,
        film,
        landscape_grid,
        (0.2, 0.6, 0.2),
    )
    portrait = spread_multiscale_halation_work_source(
        portrait_source,
        film,
        portrait_grid,
        (0.2, 0.6, 0.2),
    )
    # This is a transpose/orientation comparison, not a row-tile comparison;
    # suppress seam rows so its interpolation residual cannot be mislabeled as
    # a tile-boundary error in aggregate reports.
    comparison = _comparison(
        landscape,
        portrait.T,
        tile_rows=landscape.shape[0],
    )
    _assert_comparison("orientation/multiscale_halation", comparison, tolerance=2e-7)
    return {"multiscale_halation_transpose": comparison}


def run_grid_audit(
    *,
    width: int = 384,
    height: int = 256,
    tile_rows: tuple[int, ...] | list[int] = (1, 17, 64),
    seed: int = 20260722,
) -> dict[str, object]:
    """Run grid/tile equivalence checks and return a JSON-safe report."""
    if width <= 3 or height <= 3:
        raise ValueError("Grid audit width and height must both be greater than three.")
    rows = tuple(int(value) for value in tile_rows)
    if not rows or any(value <= 0 for value in rows):
        raise ValueError("Grid audit tile rows must contain unique positive integers.")
    if len(rows) != len(set(rows)):
        raise ValueError("Grid audit tile rows must contain unique positive integers.")
    image = _adversarial_image(width, height, rows)
    image_digest = _sha256(image)
    report = {
        "material_programs": _audit_material_programs(image, rows, seed),
        "spatial_stack": _audit_spatial_stack(image, rows, seed),
        "scan_tiling": _audit_scan_tiling(image, rows, seed),
        "halation_source": _audit_halation_source(image, rows),
        "development_adjacency": _audit_lazy_adjacency(image, rows),
        "silver_grain": _audit_silver_grain(width, height, rows, seed),
        "orientation": _audit_orientation(),
    }
    if _sha256(image) != image_digest:
        raise RuntimeError("grid audit modified its shared source image")
    return {
        "schema_version": 1,
        "image": {
            "width": int(width),
            "height": int(height),
            "pixels": int(width * height),
            "sha256": image_digest,
        },
        "runtime": {
            "seed": int(seed),
            "tile_rows": list(rows),
        },
        "source_image_unchanged": True,
        "contracts": report,
        "notes": [
            "Material, adjacency, and silver-grain tiling require exact equality.",
            "Halation-source stripes allow at most 5e-8 for OpenCV float32 convolution rounding.",
            "Output-only scan tiling allows at most 1e-7 absolute numerical error.",
            "Boundary metrics include the two rows straddling every requested tile boundary.",
            "The audit calls production boundaries and does not register another media pipeline.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument(
        "--tile-rows",
        type=int,
        action="append",
        help="Row tile size to audit; repeat for multiple sizes.",
    )
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_grid_audit(
        width=args.width,
        height=args.height,
        tile_rows=(1, 17, 64) if args.tile_rows is None else tuple(args.tile_rows),
        seed=args.seed,
    )
    payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)
    if args.json_output is not None:
        atomic_write_json(args.json_output, result)
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
