"""Deterministic, non-rendering benchmark for the public develop/scan pipeline.

The benchmark deliberately calls the same public entry points as production.
It does not replace operators, lower resolution internally, or save image
products.  Fixed-seed output hashes are checked across repetitions so timing
work cannot silently change the material or observation result.
"""

from __future__ import annotations

import argparse
import copy
import gc
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import time
import tracemalloc
from typing import Any

import numpy as np

from half_frame_darkroom.core.atomic_io import atomic_write_json
from half_frame_darkroom.core.engine import develop_negative, process_array, scan_negative
from half_frame_darkroom.model.config import DarkroomConfig


def synthetic_reference_image(width: int, height: int) -> np.ndarray:
    """Build a deterministic sRGB reference containing gradients and detail."""
    if width <= 0 or height <= 0:
        raise ValueError("Benchmark width and height must be positive.")
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    image = np.empty((height, width, 3), dtype=np.float32)
    image[..., 0] = np.clip(0.08 + 0.82 * x + 0.06 * np.sin(y * 37.0), 0.0, 1.0)
    image[..., 1] = np.clip(0.05 + 0.78 * y + 0.08 * np.sin(x * 29.0), 0.0, 1.0)
    image[..., 2] = np.clip(0.06 + 0.48 * x + 0.38 * y, 0.0, 1.0)
    return image


def _array_digest(array: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(array)
    return hashlib.sha256(memoryview(contiguous).cast("B")).hexdigest()


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "min": float(min(samples)),
        "median": float(median(samples)),
        "mean": float(mean(samples)),
        "max": float(max(samples)),
    }


def run_benchmark(
    *,
    width: int = 2400,
    height: int = 1600,
    repeats: int = 3,
    quality_mode: str = "standard",
    fast_mode: bool = False,
    program_key: str = "color_negative",
    seed: int = 20260713,
    material_tile_rows: int = 256,
    material_tile_threshold_megapixels: float = 8.0,
    scan_tile_rows: int = 512,
    scan_tile_threshold_megapixels: float = 48.0,
) -> dict[str, Any]:
    """Benchmark develop then scan while enforcing fixed-seed determinism."""
    if repeats <= 0:
        raise ValueError("Benchmark repeats must be positive.")
    image = synthetic_reference_image(width, height)
    config = DarkroomConfig()
    config.processing.quality_mode = quality_mode
    config.fast_mode = bool(fast_mode)
    config.chemistry.program_key = program_key
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)
    config.processing.material_tile_rows = int(material_tile_rows)
    config.processing.material_tile_threshold_megapixels = float(
        material_tile_threshold_megapixels
    )
    config.processing.scan_tile_rows = int(scan_tile_rows)
    config.processing.scan_tile_threshold_megapixels = float(scan_tile_threshold_megapixels)

    develop_samples: list[float] = []
    scan_samples: list[float] = []
    traced_peak_samples: list[float] = []
    expected_medium_digest: str | None = None
    expected_scan_digest: str | None = None

    for _ in range(repeats):
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        medium = develop_negative(image, copy.deepcopy(config))
        developed = time.perf_counter()
        scanned = scan_negative(medium)
        completed = time.perf_counter()
        _, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        medium_digest = _array_digest(medium.density_grain)
        scan_digest = _array_digest(scanned.output_srgb)
        if expected_medium_digest is None:
            expected_medium_digest = medium_digest
            expected_scan_digest = scan_digest
        elif medium_digest != expected_medium_digest or scan_digest != expected_scan_digest:
            raise RuntimeError(
                "Fixed-seed benchmark output changed between repetitions; "
                "timings are not comparable."
            )

        develop_samples.append(developed - started)
        scan_samples.append(completed - developed)
        traced_peak_samples.append(traced_peak / (1024.0 * 1024.0))
        del scanned, medium

    return {
        "schema_version": 1,
        "image": {
            "width": int(width),
            "height": int(height),
            "pixels": int(width * height),
        },
        "runtime": {
            "quality_mode": str(quality_mode),
            "fast_mode": bool(fast_mode),
            "program_key": str(program_key),
            "seed": int(seed),
            "repeats": int(repeats),
            "material_tile_rows": int(material_tile_rows),
            "material_tile_threshold_megapixels": float(
                material_tile_threshold_megapixels
            ),
            "scan_tile_rows": int(scan_tile_rows),
            "scan_tile_threshold_megapixels": float(scan_tile_threshold_megapixels),
        },
        "develop_seconds": _summary(develop_samples),
        "scan_seconds": _summary(scan_samples),
        "total_seconds": _summary(
            [develop + scan for develop, scan in zip(develop_samples, scan_samples)]
        ),
        "python_numpy_traced_peak_mib": _summary(traced_peak_samples),
        "determinism": {
            "density_grain_sha256": expected_medium_digest,
            "scan_output_sha256": expected_scan_digest,
            "stable_across_repeats": True,
        },
        "notes": [
            "Timing uses public develop_negative() and scan_negative() entry points.",
            "Traced peak is a comparative Python/NumPy allocation metric, not total system RSS.",
            "No image products are written by the benchmark.",
        ],
    }


def run_production_benchmark(
    *,
    width: int = 2400,
    height: int = 1600,
    repeats: int = 3,
    quality_mode: str = "standard",
    fast_mode: bool = False,
    program_key: str = "color_negative",
    seed: int = 20260713,
    material_tile_rows: int = 256,
    material_tile_threshold_megapixels: float = 8.0,
    scan_tile_rows: int = 512,
    scan_tile_threshold_megapixels: float = 48.0,
) -> dict[str, Any]:
    """Benchmark the output-only production path with cold-history storage."""
    if repeats <= 0:
        raise ValueError("Benchmark repeats must be positive.")
    image = synthetic_reference_image(width, height)
    config = DarkroomConfig()
    config.processing.quality_mode = quality_mode
    config.processing.material_tile_rows = int(material_tile_rows)
    config.processing.material_tile_threshold_megapixels = float(
        material_tile_threshold_megapixels
    )
    config.processing.scan_tile_rows = int(scan_tile_rows)
    config.processing.scan_tile_threshold_megapixels = float(scan_tile_threshold_megapixels)
    config.fast_mode = bool(fast_mode)
    config.chemistry.program_key = program_key
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)

    total_samples: list[float] = []
    peak_samples: list[float] = []
    expected_digest: str | None = None
    for _ in range(repeats):
        gc.collect()
        tracemalloc.start()
        started = time.perf_counter()
        output = process_array(image, copy.deepcopy(config))
        completed = time.perf_counter()
        _, traced_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        digest = _array_digest(output)
        if expected_digest is None:
            expected_digest = digest
        elif digest != expected_digest:
            raise RuntimeError("Fixed-seed production output changed between repetitions.")
        total_samples.append(completed - started)
        peak_samples.append(traced_peak / (1024.0 * 1024.0))
        del output

    return {
        "schema_version": 1,
        "execution_path": "production_output_only",
        "image": {"width": int(width), "height": int(height), "pixels": int(width * height)},
        "runtime": {
            "quality_mode": str(quality_mode),
            "fast_mode": bool(fast_mode),
            "program_key": str(program_key),
            "seed": int(seed),
            "repeats": int(repeats),
            "material_tile_rows": int(material_tile_rows),
            "material_tile_threshold_megapixels": float(material_tile_threshold_megapixels),
            "scan_tile_rows": int(scan_tile_rows),
            "scan_tile_threshold_megapixels": float(scan_tile_threshold_megapixels),
        },
        "total_seconds": _summary(total_samples),
        "python_numpy_traced_peak_mib": _summary(peak_samples),
        "determinism": {
            "scan_output_sha256": expected_digest,
            "stable_across_repeats": True,
        },
        "notes": [
            "Timing uses public process_array() production output-only entry point.",
            "Cold historical stages may use FP16; active formation, density masters, and scan math remain FP32.",
            "Traced peak is a comparative Python/NumPy allocation metric, not total system RSS.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=2400)
    parser.add_argument("--height", type=int, default=1600)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--quality",
        default="standard",
        choices=("draft", "preview", "standard", "high", "full", "native"),
    )
    parser.add_argument("--fast", action="store_true")
    parser.add_argument(
        "--program",
        default="color_negative",
        choices=(
            "auto",
            "bw_negative",
            "color_negative",
            "color_negative_bleach_bypass",
            "bw_reversal",
            "color_reversal",
        ),
    )
    parser.add_argument("--seed", type=int, default=20260713)
    parser.add_argument(
        "--material-tile-rows",
        type=int,
        default=256,
        help="Exact material-pool row tile size; 0 disables tiling.",
    )
    parser.add_argument(
        "--material-tile-threshold-mp",
        type=float,
        default=8.0,
        help="Frame megapixels above which exact material tiling activates.",
    )
    parser.add_argument(
        "--scan-tile-rows",
        type=int,
        default=512,
        help="Exact output-only scan row tile size; 0 disables scan tiling.",
    )
    parser.add_argument(
        "--scan-tile-threshold-mp",
        type=float,
        default=48.0,
        help="Frame megapixels above which output-only scan tiling activates.",
    )
    parser.add_argument("--json-output", type=Path)
    parser.add_argument(
        "--production",
        action="store_true",
        help="Benchmark process_array output-only production retention instead of diagnostic develop+scan.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    benchmark = run_production_benchmark if args.production else run_benchmark
    result = benchmark(
        width=args.width,
        height=args.height,
        repeats=args.repeats,
        quality_mode=args.quality,
        fast_mode=args.fast,
        program_key=args.program,
        seed=args.seed,
        material_tile_rows=args.material_tile_rows,
        material_tile_threshold_megapixels=args.material_tile_threshold_mp,
        scan_tile_rows=args.scan_tile_rows,
        scan_tile_threshold_megapixels=args.scan_tile_threshold_mp,
    )
    if args.json_output is not None:
        atomic_write_json(args.json_output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
