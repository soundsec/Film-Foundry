"""Deterministic, isolated audit runner for Film Foundry phenomena.

This developer-facing tool calls the same module boundaries used by the main
pipeline.  It does not register another image pipeline, save image products,
or substitute reduced formulas.  Each case receives the same immutable
synthetic source and a fresh fixed-seed random generator, making time, traced
allocation, output statistics, and deterministic hashes independently
inspectable.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import hashlib
import json
from pathlib import Path
from statistics import mean, median
import time
import tracemalloc
from typing import Callable

import numpy as np

from film_foundry.tools.benchmark_pipeline import synthetic_reference_image
from half_frame_darkroom.core.accidents import (
    apply_density_accident_components,
    apply_light_leak_to_exposure,
    uneven_development_rate_field,
)
from half_frame_darkroom.core.atomic_io import atomic_write_json
from half_frame_darkroom.core.color import srgb_to_linear
from half_frame_darkroom.core.density_grain import apply_density_grain
from half_frame_darkroom.core.development_adjacency import (
    build_development_adjacency_field,
)
from half_frame_darkroom.core.film_process.integration import (
    _color_material,
    _material_latent_state,
    develop_bw_negative_reduced,
    develop_bw_reversal_reduced,
    develop_color_negative_reduced,
    develop_color_reversal_reduced,
)
from half_frame_darkroom.core.halation import apply_halation
from half_frame_darkroom.core.light_piping import light_piping_exposure_field
from half_frame_darkroom.core.mtf import apply_emulsion_mtf
from half_frame_darkroom.core.scanner import (
    capture_optical_density,
    layer_density_to_optical_density_rgb,
    light_table_illuminant_rgb,
    negative_backlight_illuminant_rgb,
    normalize_scan_rgb,
    render_positive_transparency_scan,
    scanner_raw_to_positive_rgb,
)
from half_frame_darkroom.core.silver_grain import (
    SilverGrainPlan,
    apply_metallic_silver_grain,
)
from half_frame_darkroom.model.config import (
    DevelopRecipeConfig,
    FilmStockConfig,
    ScannerConfig,
)


ArrayArtifacts = dict[str, np.ndarray]


@dataclass(frozen=True, slots=True)
class AuditContext:
    width: int
    height: int
    seed: int
    image_srgb: np.ndarray
    image_linear: np.ndarray
    density_layers: np.ndarray


@dataclass(frozen=True, slots=True)
class PhenomenonCase:
    key: str
    stage: str
    description: str
    runner: Callable[[AuditContext], ArrayArtifacts]


def _summary(samples: list[float]) -> dict[str, float]:
    return {
        "min": float(min(samples)),
        "median": float(median(samples)),
        "mean": float(mean(samples)),
        "max": float(max(samples)),
    }


def _artifact_digest(artifacts: ArrayArtifacts) -> str:
    digest = hashlib.sha256()
    for name in sorted(artifacts):
        array = np.ascontiguousarray(artifacts[name])
        digest.update(name.encode("utf-8"))
        digest.update(str(array.dtype).encode("ascii"))
        digest.update(repr(tuple(int(value) for value in array.shape)).encode("ascii"))
        digest.update(memoryview(array).cast("B"))
    return digest.hexdigest()


def _array_summary(array: np.ndarray) -> dict[str, object]:
    values = np.asarray(array)
    if values.size == 0:
        raise ValueError("phenomenon audit arrays must not be empty")
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("phenomenon audit produced a non-finite array")
    return {
        "shape": [int(value) for value in values.shape],
        "dtype": str(values.dtype),
        "minimum": minimum,
        "maximum": maximum,
        "mean": float(np.mean(values, dtype=np.float64)),
        "standard_deviation": float(np.std(values, dtype=np.float64)),
        "bytes": int(values.nbytes),
    }


def _material_density(ctx: AuditContext) -> np.ndarray:
    return np.asarray(ctx.density_layers, dtype=np.float32)


def _monochrome_film(*, positive: bool = False) -> FilmStockConfig:
    film = FilmStockConfig()
    film.name = "Audit Monochrome Reversal" if positive else "Audit Monochrome Negative"
    film.color_process = "bw"
    film.medium_process = "reversal" if positive else "negative"
    film.image_polarity = "positive" if positive else "negative"
    film.film_base_density_rgb = (0.04, 0.04, 0.04)
    film.clear_support_density_rgb = (0.03, 0.03, 0.03)
    film.density_min = (0.08, 0.08, 0.08)
    film.density_max = (1.90, 1.90, 1.90)
    return film


def _positive_color_film() -> FilmStockConfig:
    film = FilmStockConfig()
    film.name = "Audit Color Reversal"
    film.medium_process = "reversal"
    film.image_polarity = "positive"
    film.color_process = "color"
    film.film_base_density_rgb = (0.045, 0.045, 0.05)
    film.clear_support_density_rgb = (0.035, 0.035, 0.04)
    return film


def _case_mtf(ctx: AuditContext) -> ArrayArtifacts:
    return {"linear_output": apply_emulsion_mtf(ctx.image_linear, FilmStockConfig())}


def _case_halation(ctx: AuditContext) -> ArrayArtifacts:
    return {
        "linear_output": apply_halation(
            ctx.image_linear,
            FilmStockConfig(),
            work_long_edge=None,
        )
    }


def _case_light_leak(ctx: AuditContext) -> ArrayArtifacts:
    recipe = DevelopRecipeConfig(light_leak_strength=0.65)
    output, leak_map = apply_light_leak_to_exposure(
        ctx.image_linear,
        recipe,
        rng=np.random.default_rng(ctx.seed),
    )
    if leak_map is None:
        raise RuntimeError("enabled light-leak audit produced no exposure map")
    return {"linear_output": output, "exposure_map": leak_map}


def _case_light_piping(ctx: AuditContext) -> ArrayArtifacts:
    film = FilmStockConfig(
        light_piping_strength=0.55,
        light_piping_edge_mode="all_edges",
    )
    field = light_piping_exposure_field(
        ctx.image_linear.shape,
        film,
        layer_count=3,
    )
    if field is None:
        raise RuntimeError("enabled light-piping audit produced no field")
    target = np.zeros((ctx.height, ctx.width, 3), dtype=np.float32)
    field.add_scaled_to(target, np.ones(3, dtype=np.float32))
    return {"layer_exposure_addition": target}


def _case_uneven_development(ctx: AuditContext) -> ArrayArtifacts:
    recipe = DevelopRecipeConfig(
        uneven_development=0.72,
        agitation=0.35,
        developer_exhaustion=0.30,
    )
    field = uneven_development_rate_field(
        ctx.image_linear,
        recipe,
        rng=np.random.default_rng(ctx.seed),
        work_long_edge=None,
    )
    if field is None:
        raise RuntimeError("enabled uneven-development audit produced no rate field")
    # Record directional energy explicitly.  A reduced uneven-development
    # bath has one dominant drainage/agitation direction; comparable energy
    # on both axes is a useful warning for the former woven crosshatch error.
    gradient_y = float(np.mean(np.abs(np.diff(field, axis=0)), dtype=np.float64))
    gradient_x = float(np.mean(np.abs(np.diff(field, axis=1)), dtype=np.float64))
    dominant = max(gradient_y, gradient_x)
    axis_balance = min(gradient_y, gradient_x) / max(dominant, 1e-12)
    return {
        "development_rate": field,
        "axis_gradient_energy_yx": np.asarray((gradient_y, gradient_x), dtype=np.float32),
        "axis_energy_balance": np.asarray((axis_balance,), dtype=np.float32),
    }


def _case_development_adjacency(ctx: AuditContext) -> ArrayArtifacts:
    recipe = DevelopRecipeConfig(
        program_key="color_negative",
        development_adjacency_strength=0.78,
        development_adjacency_radius=0.018,
    )
    field, _audit = build_development_adjacency_field(
        ctx.image_linear,
        FilmStockConfig(),
        recipe,
        "color_negative",
        work_long_edge=max(ctx.height, ctx.width),
    )
    if field is None:
        raise RuntimeError("enabled development-adjacency audit produced no rate field")
    return {"first_development_rate": field.slice_rows(0, ctx.height)}


def _case_density_accidents(ctx: AuditContext) -> ArrayArtifacts:
    recipe = DevelopRecipeConfig(
        developer_type="monobath",
        fixer_type="monobath",
        process_mode="monobath",
        fixer_exhaustion=0.65,
        chemical_stain=0.62,
        silver_plating=0.58,
    )
    result = apply_density_accident_components(
        _material_density(ctx),
        recipe,
        rng=np.random.default_rng(ctx.seed),
        film=FilmStockConfig(),
        work_long_edge=None,
    )
    artifacts: ArrayArtifacts = {"density_layers": result.density_cmy}
    artifacts.update({f"map_{name}": value for name, value in result.maps.items()})
    return artifacts


def _case_dye_grain(ctx: AuditContext) -> ArrayArtifacts:
    return {
        "density_layers": apply_density_grain(
            _material_density(ctx),
            FilmStockConfig(),
            DevelopRecipeConfig(),
            rng=np.random.default_rng(ctx.seed),
            work_long_edge=None,
            image_polarity="negative",
            component_scope="emulsion",
        )
    }


def _case_silver_grain(ctx: AuditContext) -> ArrayArtifacts:
    silver = np.mean(_material_density(ctx), axis=-1, dtype=np.float32)
    optical = np.repeat(silver[..., None], 3, axis=-1).astype(np.float32)
    plan = SilverGrainPlan(
        full_shape=(ctx.height, ctx.width),
        seed=ctx.seed,
        strength=0.08,
        radius=0.003,
        clump_mix=0.24,
        tile_rows=37,
    )
    if not apply_metallic_silver_grain(optical, silver, plan):
        raise RuntimeError("enabled silver-grain audit did not apply")
    return {"optical_density_rgb": optical}


def _case_latent_state(ctx: AuditContext) -> ArrayArtifacts:
    film = FilmStockConfig()
    state = _material_latent_state(
        ctx.image_linear,
        film,
        _color_material(film),
    )
    return {
        "developability": state.developability,
        "halide": state.halide,
    }


def _case_color_negative(ctx: AuditContext) -> ArrayArtifacts:
    result = develop_color_negative_reduced(
        ctx.image_linear,
        FilmStockConfig(),
        DevelopRecipeConfig(program_key="color_negative"),
    )
    return {
        "density_layers": result.density_cmy,
        "optical_density_rgb": result.optical_density_rgb,
    }


def _case_push_pull_process(ctx: AuditContext) -> ArrayArtifacts:
    film = FilmStockConfig()
    pushed = develop_color_negative_reduced(
        ctx.image_linear,
        film,
        DevelopRecipeConfig(program_key="color_negative", push_stops=1.5),
    )
    pulled = develop_color_negative_reduced(
        ctx.image_linear,
        film,
        DevelopRecipeConfig(program_key="color_negative", push_stops=-1.0),
    )
    return {
        "pushed_optical_density_rgb": pushed.optical_density_rgb,
        "pulled_optical_density_rgb": pulled.optical_density_rgb,
    }


def _case_material_degradation(ctx: AuditContext) -> ArrayArtifacts:
    film = FilmStockConfig(material_degradation=0.82)
    result = develop_color_negative_reduced(
        ctx.image_linear,
        film,
        DevelopRecipeConfig(program_key="color_negative"),
    )
    return {
        "latent_fraction": result.latent_fraction,
        "optical_density_rgb": result.optical_density_rgb,
        "clear_base_optical_density_rgb": result.clear_base_optical_density_rgb,
    }


def _case_cross_process(ctx: AuditContext) -> ArrayArtifacts:
    result = develop_color_negative_reduced(
        ctx.image_linear,
        _positive_color_film(),
        DevelopRecipeConfig(program_key="color_negative"),
    )
    return {
        "density_layers": result.density_cmy,
        "optical_density_rgb": result.optical_density_rgb,
        "retained_silver_density_rgb": result.silver_density_rgb,
    }


def _case_bleach_bypass(ctx: AuditContext) -> ArrayArtifacts:
    recipe = DevelopRecipeConfig(
        program_key="color_negative_bleach_bypass",
        silver_bleach_completion=0.25,
    )
    result = develop_color_negative_reduced(
        ctx.image_linear,
        FilmStockConfig(),
        recipe,
    )
    return {
        "optical_density_rgb": result.optical_density_rgb,
        "retained_silver_density_rgb": result.silver_density_rgb,
    }


def _case_reversal_mask_bleach(ctx: AuditContext) -> ArrayArtifacts:
    result = develop_color_reversal_reduced(
        ctx.image_linear,
        FilmStockConfig(),
        DevelopRecipeConfig(
            program_key="color_reversal",
            mask_bleach_completion=0.88,
        ),
    )
    return {
        "density_layers": result.density_cmy,
        "optical_density_rgb": result.optical_density_rgb,
        "clear_base_optical_density_rgb": result.clear_base_optical_density_rgb,
    }


def _case_bw_negative(ctx: AuditContext) -> ArrayArtifacts:
    result = develop_bw_negative_reduced(
        ctx.image_linear,
        _monochrome_film(),
        DevelopRecipeConfig(program_key="bw_negative"),
    )
    return {
        "density_layers": result.density_rgb,
        "optical_density_rgb": result.optical_density_rgb,
    }


def _case_bw_reversal(ctx: AuditContext) -> ArrayArtifacts:
    result = develop_bw_reversal_reduced(
        ctx.image_linear,
        _monochrome_film(positive=True),
        DevelopRecipeConfig(program_key="bw_reversal"),
    )
    return {
        "density_layers": result.density_rgb,
        "optical_density_rgb": result.optical_density_rgb,
    }


def _case_color_reversal(ctx: AuditContext) -> ArrayArtifacts:
    result = develop_color_reversal_reduced(
        ctx.image_linear,
        _positive_color_film(),
        DevelopRecipeConfig(program_key="color_reversal"),
    )
    return {
        "density_layers": result.density_cmy,
        "optical_density_rgb": result.optical_density_rgb,
    }


def _case_negative_scan(ctx: AuditContext) -> ArrayArtifacts:
    film = FilmStockConfig()
    scanner = ScannerConfig()
    optical = layer_density_to_optical_density_rgb(
        _material_density(ctx),
        film,
    )
    raw = capture_optical_density(
        optical,
        scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(scanner),
    )
    positive = scanner_raw_to_positive_rgb(
        raw,
        scanner,
        known_base_density_rgb=np.asarray(film.film_base_density_rgb, dtype=np.float32),
    )
    return {"scanner_raw": raw, "positive_linear": positive}


def _case_negative_channel_compensation(ctx: AuditContext) -> ArrayArtifacts:
    film = FilmStockConfig()
    optical = layer_density_to_optical_density_rgb(
        _material_density(ctx),
        film,
    )
    disabled = ScannerConfig(negative_channel_compensation_enabled=False)
    enabled = ScannerConfig(
        negative_channel_compensation_enabled=True,
        negative_channel_compensation_strength=0.72,
    )
    raw = capture_optical_density(
        optical,
        disabled,
        illuminant_rgb=negative_backlight_illuminant_rgb(disabled),
    )
    known_base = np.asarray(film.film_base_density_rgb, dtype=np.float32)
    return {
        "compensation_disabled": scanner_raw_to_positive_rgb(
            raw,
            disabled,
            known_base_density_rgb=known_base,
        ),
        "compensation_enabled": scanner_raw_to_positive_rgb(
            raw,
            enabled,
            known_base_density_rgb=known_base,
        ),
    }


def _case_positive_scan(ctx: AuditContext) -> ArrayArtifacts:
    film = _positive_color_film()
    scanner = ScannerConfig(
        interpreter_key="positive_transparency_scan",
        scan_method="positive_transparency",
        input_polarity="positive",
    )
    optical = layer_density_to_optical_density_rgb(
        _material_density(ctx),
        film,
    )
    raw = capture_optical_density(
        optical,
        scanner,
        illuminant_rgb=light_table_illuminant_rgb(scanner),
    )
    positive = render_positive_transparency_scan(raw, scanner)
    return {"scanner_raw": raw, "positive_linear": positive}


def _case_scan_normalization(ctx: AuditContext) -> ArrayArtifacts:
    return {
        "normalized_linear": normalize_scan_rgb(
            ctx.image_linear,
            black_percentile=0.3,
            white_percentile=99.7,
            strength=1.0,
            mode="luma",
        )
    }


PHENOMENON_CASES: tuple[PhenomenonCase, ...] = (
    PhenomenonCase("emulsion_mtf", "pre_latent_exposure", "Emulsion spatial-frequency response", _case_mtf),
    PhenomenonCase("halation", "pre_latent_exposure", "Film-side highlight return and spread", _case_halation),
    PhenomenonCase("light_leak", "pre_latent_exposure", "Accidental exposure entering from a local edge", _case_light_leak),
    PhenomenonCase("light_piping", "pre_latent_exposure", "Declared support-edge exposure propagation", _case_light_piping),
    PhenomenonCase("latent_state", "latent_state", "Material-only exposure-to-developability mapping", _case_latent_state),
    PhenomenonCase("uneven_development", "process_kinetics", "Spatial developer-rate variation", _case_uneven_development),
    PhenomenonCase("development_adjacency", "process_kinetics", "First-development adjacency rate field", _case_development_adjacency),
    PhenomenonCase("color_negative_process", "material_process", "Color coupling, bleach, and fix", _case_color_negative),
    PhenomenonCase("push_pull_process", "material_process", "Push/pull development response through the same color-negative program", _case_push_pull_process),
    PhenomenonCase("material_degradation", "material_process", "Material-side ageing, speed loss, fog, and layer imbalance", _case_material_degradation),
    PhenomenonCase("cross_process", "material_process", "Reversal material processed through a color-negative program", _case_cross_process),
    PhenomenonCase("bleach_bypass_process", "material_process", "Partial silver bleach and retained image silver", _case_bleach_bypass),
    PhenomenonCase("reversal_mask_bleach", "material_process", "Negative material reversal with experimental mask/dye bleach", _case_reversal_mask_bleach),
    PhenomenonCase("bw_negative_process", "material_process", "Silver negative development and fixing", _case_bw_negative),
    PhenomenonCase("bw_reversal_process", "material_process", "First image removal, activation, and second development", _case_bw_reversal),
    PhenomenonCase("color_reversal_process", "material_process", "First development, activation, color development, bleach, and fix", _case_color_reversal),
    PhenomenonCase("density_accidents", "final_medium_components", "Chemical stain and deposited surface silver", _case_density_accidents),
    PhenomenonCase("dye_grain", "final_medium_components", "Density-responsive correlated emulsion grain", _case_dye_grain),
    PhenomenonCase("silver_grain", "final_medium_components", "Coordinate-stable neutral metallic-silver grain", _case_silver_grain),
    PhenomenonCase("negative_scan", "scan_observation", "Backlight capture, oracle base removal, inversion, and print mapping", _case_negative_scan),
    PhenomenonCase("negative_channel_compensation", "scan_interpretation", "Bounded scanner-side negative channel correction", _case_negative_channel_compensation),
    PhenomenonCase("positive_scan", "scan_observation", "Light-table capture and positive transparency interpretation", _case_positive_scan),
    PhenomenonCase("scan_normalization", "scan_interpretation", "One global black/white calibration", _case_scan_normalization),
)

PHENOMENON_KEYS = tuple(case.key for case in PHENOMENON_CASES)


def _context(width: int, height: int, seed: int) -> AuditContext:
    if width <= 0 or height <= 0:
        raise ValueError("Phenomenon audit width and height must be positive.")
    image_srgb = synthetic_reference_image(width, height)
    image_linear = srgb_to_linear(image_srgb)
    density_layers = np.empty_like(image_linear, dtype=np.float32)
    density_layers[..., 0] = 0.10 + image_linear[..., 0] * 1.65
    density_layers[..., 1] = 0.11 + image_linear[..., 1] * 1.72
    density_layers[..., 2] = 0.09 + image_linear[..., 2] * 1.58
    for array in (image_srgb, image_linear, density_layers):
        array.setflags(write=False)
    return AuditContext(
        width=int(width),
        height=int(height),
        seed=int(seed),
        image_srgb=image_srgb,
        image_linear=image_linear,
        density_layers=density_layers,
    )


def run_phenomenon_audit(
    *,
    width: int = 480,
    height: int = 320,
    repeats: int = 2,
    seed: int = 20260721,
    phenomena: tuple[str, ...] | list[str] | None = None,
) -> dict[str, object]:
    """Run selected phenomena independently and return a JSON-safe report."""
    if repeats <= 0:
        raise ValueError("Phenomenon audit repeats must be positive.")
    requested = PHENOMENON_KEYS if phenomena is None else tuple(str(item) for item in phenomena)
    if len(requested) != len(set(requested)):
        raise ValueError("Phenomenon audit selections must not contain duplicates.")
    unknown = tuple(item for item in requested if item not in PHENOMENON_KEYS)
    if unknown:
        raise ValueError(f"Unknown phenomenon audit key(s): {', '.join(unknown)}")
    if not requested:
        raise ValueError("Phenomenon audit requires at least one selected case.")

    context = _context(width, height, seed)
    source_digests = {
        "image_srgb": _artifact_digest({"image_srgb": context.image_srgb}),
        "image_linear": _artifact_digest({"image_linear": context.image_linear}),
        "density_layers": _artifact_digest({"density_layers": context.density_layers}),
    }
    cases_by_key = {case.key: case for case in PHENOMENON_CASES}
    reports: dict[str, object] = {}
    for key in requested:
        case = cases_by_key[key]
        elapsed_samples: list[float] = []
        peak_samples: list[float] = []
        expected_digest: str | None = None
        summaries: dict[str, object] | None = None
        for _ in range(repeats):
            gc.collect()
            tracemalloc.start()
            started = time.perf_counter()
            try:
                artifacts = case.runner(context)
                elapsed = time.perf_counter() - started
                _, traced_peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            if not artifacts:
                raise RuntimeError(f"Phenomenon audit '{key}' returned no arrays")
            normalized = {
                str(name): np.asarray(array)
                for name, array in artifacts.items()
            }
            digest = _artifact_digest(normalized)
            if expected_digest is None:
                expected_digest = digest
                summaries = {
                    name: _array_summary(array)
                    for name, array in normalized.items()
                }
            elif digest != expected_digest:
                raise RuntimeError(
                    f"Phenomenon audit '{key}' changed output across fixed-seed repetitions"
                )
            elapsed_samples.append(elapsed)
            peak_samples.append(traced_peak / (1024.0 * 1024.0))
            del artifacts, normalized
        reports[key] = {
            "stage": case.stage,
            "description": case.description,
            "elapsed_seconds": _summary(elapsed_samples),
            "python_numpy_traced_peak_mib": _summary(peak_samples),
            "determinism": {
                "sha256": expected_digest,
                "stable_across_repeats": True,
            },
            "arrays": summaries,
        }

    source_unchanged = all(
        (
            source_digests["image_srgb"]
            == _artifact_digest({"image_srgb": context.image_srgb}),
            source_digests["image_linear"]
            == _artifact_digest({"image_linear": context.image_linear}),
            source_digests["density_layers"]
            == _artifact_digest({"density_layers": context.density_layers}),
        )
    )
    if not source_unchanged:
        raise RuntimeError("A phenomenon audit case modified the shared source arrays")
    return {
        "schema_version": 1,
        "image": {
            "width": int(width),
            "height": int(height),
            "pixels": int(width * height),
        },
        "runtime": {
            "seed": int(seed),
            "repeats": int(repeats),
            "selected": list(requested),
            "quality_contract": "native_case_boundaries_no_pipeline_substitution",
        },
        "source_arrays_unchanged": True,
        "phenomena": reports,
        "notes": [
            "Each case calls an existing formation, process, grain, or scan boundary directly.",
            "Traced peak excludes the shared synthetic input allocated before each case.",
            "Timing is a same-machine audit signal, not a cross-machine performance promise.",
            "The audit tool is not registered as a production media pipeline.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=320)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20260721)
    parser.add_argument(
        "--phenomenon",
        action="append",
        choices=PHENOMENON_KEYS,
        help="Run only this phenomenon; repeat the option to select several.",
    )
    parser.add_argument("--list", action="store_true", help="List available phenomenon keys and exit.")
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for case in PHENOMENON_CASES:
            print(f"{case.key}\t{case.stage}\t{case.description}")
        return 0
    result = run_phenomenon_audit(
        width=args.width,
        height=args.height,
        repeats=args.repeats,
        seed=args.seed,
        phenomena=args.phenomenon,
    )
    if args.json_output is not None:
        atomic_write_json(args.json_output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
