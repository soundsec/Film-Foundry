"""Deterministic, non-rendering benchmark for the public develop/scan pipeline.

The benchmark deliberately calls the same public entry points as production.
It does not replace operators, lower resolution internally, or save image
products.  Fixed-seed output hashes are checked across repetitions so timing
work cannot silently change the material or observation result.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import gc
import hashlib
import json
import os
from pathlib import Path
from statistics import mean, median
import time
import tracemalloc
from typing import Any

import cv2
import numpy as np

from half_frame_darkroom.core.atomic_io import atomic_write_json
from half_frame_darkroom.core.engine import (
    _process_output_with_runtime,
    develop_negative,
    scan_negative,
)
from half_frame_darkroom.core.execution_topology import (
    OPTIMIZED_TOPOLOGY,
    REFERENCE_TOPOLOGY,
    REFERENCE_TOPOLOGY_VERSION,
    execution_topology,
)
from half_frame_darkroom.model.config import DarkroomConfig


ACCIDENT_PROFILES = ("none", "combined")


def _apply_accident_profile(config: DarkroomConfig, profile: str) -> None:
    """Apply a documented benchmark-only accident workload.

    The combined values intentionally match the parameter-cost audit.  This
    keeps the diagnostic and production benchmarks comparable without making
    either tool import the other (the audit already imports the synthetic
    reference image from this module).
    """
    normalized = str(profile).strip().lower()
    if normalized == "none":
        return
    if normalized != "combined":
        raise ValueError(f"Unknown benchmark accident profile: {profile!r}")
    chemistry = config.chemistry
    chemistry.light_leak_strength = 0.65
    chemistry.uneven_development = 0.65
    chemistry.chemical_stain = 0.55
    chemistry.silver_plating = 0.40
    chemistry.silver_retention = 0.30
    chemistry.developer_exhaustion = 0.28
    chemistry.fixer_exhaustion = 0.25
    chemistry.agitation = 0.45


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


def _optional_summary(samples: list[float | None]) -> dict[str, float] | None:
    available = [float(value) for value in samples if value is not None]
    return None if not available else _summary(available)


def _process_memory_snapshot_mib() -> dict[str, float | None]:
    """Read process memory without adding a runtime dependency.

    Windows exposes current/peak working set and current private usage through
    GetProcessMemoryInfo.  Linux exposes current/peak RSS through procfs.  An
    unsupported platform returns null metrics instead of changing benchmark
    execution or importing an optional package.
    """
    scale = 1024.0 * 1024.0
    snapshot: dict[str, float | None] = {
        "rss_mib": None,
        "peak_rss_mib": None,
        "private_mib": None,
    }
    if os.name == "nt":
        class ProcessMemoryCountersEx(ctypes.Structure):
            _fields_ = (
                ("cb", ctypes.c_ulong),
                ("page_fault_count", ctypes.c_ulong),
                ("peak_working_set_size", ctypes.c_size_t),
                ("working_set_size", ctypes.c_size_t),
                ("quota_peak_paged_pool_usage", ctypes.c_size_t),
                ("quota_paged_pool_usage", ctypes.c_size_t),
                ("quota_peak_non_paged_pool_usage", ctypes.c_size_t),
                ("quota_non_paged_pool_usage", ctypes.c_size_t),
                ("pagefile_usage", ctypes.c_size_t),
                ("peak_pagefile_usage", ctypes.c_size_t),
                ("private_usage", ctypes.c_size_t),
            )

        counters = ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(counters)
        get_current_process = ctypes.windll.kernel32.GetCurrentProcess
        get_current_process.restype = ctypes.c_void_p
        get_process_memory_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_process_memory_info.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(ProcessMemoryCountersEx),
            ctypes.c_ulong,
        )
        get_process_memory_info.restype = ctypes.c_int
        ok = get_process_memory_info(
            get_current_process(),
            ctypes.byref(counters),
            counters.cb,
        )
        if ok:
            snapshot["rss_mib"] = float(counters.working_set_size / scale)
            snapshot["peak_rss_mib"] = float(counters.peak_working_set_size / scale)
            snapshot["private_mib"] = float(counters.private_usage / scale)
        return snapshot

    status_path = Path("/proc/self/status")
    if status_path.is_file():
        values: dict[str, float] = {}
        for line in status_path.read_text(encoding="utf-8").splitlines():
            key, separator, remainder = line.partition(":")
            if not separator or key not in {"VmRSS", "VmHWM"}:
                continue
            parts = remainder.strip().split()
            if parts:
                values[key] = float(parts[0]) / 1024.0
        snapshot["rss_mib"] = values.get("VmRSS")
        snapshot["peak_rss_mib"] = values.get("VmHWM")
    return snapshot


def _system_cpu_times() -> tuple[int, int] | None:
    """Return cumulative ``(idle, total)`` CPU ticks without a dependency.

    The values are only used as two endpoints around a benchmark repetition,
    so the unit is irrelevant.  This adds negligible measurement overhead and
    lets a result disclose whether it was collected on an idle or contended
    machine.
    """

    if os.name == "nt":
        class FileTime(ctypes.Structure):
            _fields_ = (
                ("low", ctypes.c_ulong),
                ("high", ctypes.c_ulong),
            )

        idle = FileTime()
        kernel = FileTime()
        user = FileTime()
        get_system_times = ctypes.windll.kernel32.GetSystemTimes
        get_system_times.argtypes = (
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        )
        get_system_times.restype = ctypes.c_int
        if not get_system_times(
            ctypes.byref(idle),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None

        def ticks(value: FileTime) -> int:
            return (int(value.high) << 32) | int(value.low)

        idle_ticks = ticks(idle)
        # Windows kernel time includes idle time.
        return idle_ticks, ticks(kernel) + ticks(user)

    stat_path = Path("/proc/stat")
    if stat_path.is_file():
        first = stat_path.read_text(encoding="ascii").splitlines()[0].split()
        if first and first[0] == "cpu" and len(first) >= 5:
            values = [int(value) for value in first[1:]]
            idle_ticks = values[3] + (values[4] if len(values) > 4 else 0)
            return idle_ticks, sum(values)
    return None


def _system_cpu_busy_percent(
    before: tuple[int, int] | None,
    after: tuple[int, int] | None,
) -> float | None:
    if before is None or after is None:
        return None
    idle_delta = int(after[0]) - int(before[0])
    total_delta = int(after[1]) - int(before[1])
    if total_delta <= 0:
        return None
    busy = 100.0 * (1.0 - idle_delta / total_delta)
    return float(min(max(busy, 0.0), 100.0))


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
    scan_tile_threshold_megapixels: float = 8.0,
    native_thread_limit: int = 4,
    accident_profile: str = "none",
    reference_topology: bool = False,
) -> dict[str, Any]:
    """Benchmark develop then scan while enforcing fixed-seed determinism."""
    if repeats <= 0:
        raise ValueError("Benchmark repeats must be positive.")
    image = synthetic_reference_image(width, height)
    config = DarkroomConfig()
    config.processing.quality_mode = quality_mode
    config.fast_mode = bool(fast_mode)
    config.chemistry.program_key = program_key
    if str(program_key) == "color_negative_bleach_bypass":
        # Match the process editor's useful topology-switch default. Merely
        # naming the bypass while leaving 100% silver bleach would benchmark
        # the ordinary color-negative program under a misleading label.
        config.chemistry.silver_bleach_completion = 0.20
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)
    config.processing.material_tile_rows = int(material_tile_rows)
    config.processing.material_tile_threshold_megapixels = float(
        material_tile_threshold_megapixels
    )
    config.processing.scan_tile_rows = int(scan_tile_rows)
    config.processing.scan_tile_threshold_megapixels = float(scan_tile_threshold_megapixels)
    config.processing.native_thread_limit = int(native_thread_limit)
    _apply_accident_profile(config, accident_profile)

    develop_samples: list[float] = []
    scan_samples: list[float] = []
    traced_peak_samples: list[float] = []
    system_busy_samples: list[float | None] = []
    expected_medium_digest: str | None = None
    expected_scan_digest: str | None = None

    for _ in range(repeats):
        gc.collect()
        system_cpu_before = _system_cpu_times()
        tracemalloc.start()
        started = time.perf_counter()
        topology = REFERENCE_TOPOLOGY if reference_topology else OPTIMIZED_TOPOLOGY
        with execution_topology(topology):
            medium = develop_negative(image, copy.deepcopy(config))
            developed = time.perf_counter()
            scanned = scan_negative(medium)
            completed = time.perf_counter()
        system_busy_samples.append(
            _system_cpu_busy_percent(system_cpu_before, _system_cpu_times())
        )
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
            "native_thread_limit": int(native_thread_limit),
            "opencv_threads_effective": int(cv2.getNumThreads()),
            "accident_profile": str(accident_profile),
            "execution_topology": topology,
            "reference_topology_version": int(REFERENCE_TOPOLOGY_VERSION),
        },
        "develop_seconds": _summary(develop_samples),
        "scan_seconds": _summary(scan_samples),
        "total_seconds": _summary(
            [develop + scan for develop, scan in zip(develop_samples, scan_samples)]
        ),
        "python_numpy_traced_peak_mib": _summary(traced_peak_samples),
        "system_cpu_busy_percent": _optional_summary(system_busy_samples),
        "determinism": {
            "density_grain_sha256": expected_medium_digest,
            "scan_output_sha256": expected_scan_digest,
            "stable_across_repeats": True,
        },
        "notes": [
            "Timing uses public develop_negative() and scan_negative() entry points.",
            "Traced peak is a comparative Python/NumPy allocation metric, not total system RSS.",
            "No image products are written by the benchmark.",
            "System CPU busy percentage covers the whole machine during each repetition.",
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
    scan_tile_threshold_megapixels: float = 8.0,
    native_thread_limit: int = 4,
    accident_profile: str = "none",
    reference_topology: bool = False,
) -> dict[str, Any]:
    """Benchmark the output-only production path with discarded histories."""
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
    config.processing.native_thread_limit = int(native_thread_limit)
    _apply_accident_profile(config, accident_profile)
    config.fast_mode = bool(fast_mode)
    config.chemistry.program_key = program_key
    if str(program_key) == "color_negative_bleach_bypass":
        config.chemistry.silver_bleach_completion = 0.20
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)

    total_samples: list[float] = []
    develop_samples: list[float] = []
    scan_samples: list[float] = []
    peak_samples: list[float] = []
    system_busy_samples: list[float | None] = []
    stage_samples: dict[str, dict[str, list[float | None]]] = {
        stage: {
            "elapsed_seconds": [],
            "python_numpy_current_mib": [],
            "python_numpy_peak_mib": [],
            "process_rss_mib": [],
            "process_peak_rss_mib": [],
            "process_private_mib": [],
        }
        for stage in ("after_develop", "after_scan")
    }
    expected_digest: str | None = None
    for _ in range(repeats):
        gc.collect()
        system_cpu_before = _system_cpu_times()
        tracemalloc.start()
        started = time.perf_counter()
        previous_stage_time = started
        stage_records: dict[str, dict[str, float | None]] = {}

        def observe_stage(stage: str) -> None:
            nonlocal previous_stage_time
            observed = time.perf_counter()
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            process_memory = _process_memory_snapshot_mib()
            stage_records[stage] = {
                "elapsed_seconds": observed - previous_stage_time,
                "python_numpy_current_mib": traced_current / (1024.0 * 1024.0),
                "python_numpy_peak_mib": traced_peak / (1024.0 * 1024.0),
                "process_rss_mib": process_memory["rss_mib"],
                "process_peak_rss_mib": process_memory["peak_rss_mib"],
                "process_private_mib": process_memory["private_mib"],
            }
            previous_stage_time = observed
            tracemalloc.reset_peak()

        topology = REFERENCE_TOPOLOGY if reference_topology else OPTIMIZED_TOPOLOGY
        with execution_topology(topology):
            output, _runtime, medium = _process_output_with_runtime(
                image,
                copy.deepcopy(config),
                retain_cold_history=False,
                # Match public process_array(): once the authoritative RGB
                # optical master exists, an immediate pixel result has no
                # layer-export, resave, or Inspector consumer for the two CMY
                # compatibility masters.  Keeping the default True here made
                # the command labelled "production_output_only" benchmark a
                # different retention contract from the production API.
                retain_layer_masters=False,
                _stage_observer=observe_stage,
            )
            completed = time.perf_counter()
        system_busy_samples.append(
            _system_cpu_busy_percent(system_cpu_before, _system_cpu_times())
        )
        traced_current, tail_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        stage_peak = max(
            float(record["python_numpy_peak_mib"])
            for record in stage_records.values()
        )
        traced_peak_mib = max(stage_peak, tail_peak / (1024.0 * 1024.0))
        digest = _array_digest(output)
        if expected_digest is None:
            expected_digest = digest
        elif digest != expected_digest:
            raise RuntimeError("Fixed-seed production output changed between repetitions.")
        total_samples.append(completed - started)
        develop_samples.append(float(stage_records["after_develop"]["elapsed_seconds"]))
        scan_samples.append(float(stage_records["after_scan"]["elapsed_seconds"]))
        peak_samples.append(traced_peak_mib)
        for stage, records in stage_samples.items():
            current = stage_records[stage]
            for metric, samples in records.items():
                samples.append(current[metric])
        del output, medium, _runtime, traced_current

    stage_summary: dict[str, dict[str, dict[str, float] | None]] = {}
    for stage, records in stage_samples.items():
        stage_summary[stage] = {
            metric: _optional_summary(samples)
            for metric, samples in records.items()
        }

    return {
        "schema_version": 2,
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
            "native_thread_limit": int(native_thread_limit),
            "opencv_threads_effective": int(cv2.getNumThreads()),
            "accident_profile": str(accident_profile),
            "retain_cold_history": False,
            "retain_layer_masters": False,
            "execution_topology": topology,
            "reference_topology_version": int(REFERENCE_TOPOLOGY_VERSION),
        },
        "develop_seconds": _summary(develop_samples),
        "scan_seconds": _summary(scan_samples),
        "total_seconds": _summary(total_samples),
        "python_numpy_traced_peak_mib": _summary(peak_samples),
        "system_cpu_busy_percent": _optional_summary(system_busy_samples),
        "stage_memory": stage_summary,
        "determinism": {
            "scan_output_sha256": expected_digest,
            "stable_across_repeats": True,
        },
        "notes": [
            "Timing uses the same private production output-only entry point called by public process_array().",
            "Consumed formation histories are released after their final read; output-only compatibility-layer buffers may be transferred during optical composition.",
            "All active formation, optical-master, and scan math remains FP32; no image operation is omitted.",
            "Traced peak is a comparative Python/NumPy allocation metric, not total system RSS.",
            "Stage process RSS/private metrics are sampled at boundaries; process peak RSS is lifetime-monotonic.",
            "System CPU busy percentage covers the whole machine during each repetition.",
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
        default=8.0,
        help="Frame megapixels above which output-only scan tiling activates.",
    )
    parser.add_argument(
        "--native-threads",
        type=int,
        default=4,
        help=(
            "Process-wide OpenCV worker limit; 0 restores the native startup "
            "default. Scheduling only; image semantics are unchanged."
        ),
    )
    parser.add_argument(
        "--accident-profile",
        default="none",
        choices=ACCIDENT_PROFILES,
        help="Optional fixed process-accident workload for like-for-like profiling.",
    )
    parser.add_argument(
        "--reference-topology",
        action="store_true",
        help=(
            "Developer-only allocation-heavy reference execution for "
            "same-version A/B; image semantics and precision are unchanged."
        ),
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
        native_thread_limit=args.native_threads,
        accident_profile=args.accident_profile,
        reference_topology=args.reference_topology,
    )
    if args.json_output is not None:
        atomic_write_json(args.json_output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
