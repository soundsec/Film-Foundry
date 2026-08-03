"""Audit the runtime cost of darkroom process and accident controls.

This internal tool runs the production ``develop_negative`` boundary on one
immutable deterministic frame.  Each case changes one documented recipe or
material control from the same high-quality baseline, then reports time,
Python/NumPy traced peak, deterministic output hashes, and the expensive
spatial branches that actually activated.  It is a ranking/diagnostic tool,
not a physical calibration or a cross-machine benchmark.
"""

from __future__ import annotations

import argparse
import copy
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
from half_frame_darkroom.core.atomic_io import atomic_write_json
from half_frame_darkroom.core.engine import develop_negative
from half_frame_darkroom.model.config import DarkroomConfig


ConfigMutation = Callable[[DarkroomConfig], None]


@dataclass(frozen=True, slots=True)
class ParameterCostCase:
    key: str
    category: str
    description: str
    mutate: ConfigMutation


def _set_chem(**values: object) -> ConfigMutation:
    def mutate(config: DarkroomConfig) -> None:
        for name, value in values.items():
            setattr(config.chemistry, name, value)

    return mutate


def _set_film(**values: object) -> ConfigMutation:
    def mutate(config: DarkroomConfig) -> None:
        for name, value in values.items():
            setattr(config.film, name, value)

    return mutate


def _set_color_reversal(**values: object) -> ConfigMutation:
    """Place one control in a valid color-reversal process context."""

    def mutate(config: DarkroomConfig) -> None:
        config.film.medium_process = "slide"
        config.film.image_polarity = "positive"
        config.film.color_process = "color"
        config.chemistry.medium_process = "reversal"
        config.chemistry.process_mode = "reversal"
        config.chemistry.program_key = "color_reversal"
        for name, value in values.items():
            setattr(config.chemistry, name, value)

    return mutate


def _identity(_config: DarkroomConfig) -> None:
    return None


PARAMETER_COST_CASES: tuple[ParameterCostCase, ...] = (
    ParameterCostCase("baseline", "baseline", "Standard color-negative high-quality development", _identity),
    ParameterCostCase("time_short", "kinetics", "Short development time", _set_chem(time_min=4.0)),
    ParameterCostCase("time_long", "kinetics", "Long development time", _set_chem(time_min=16.0)),
    ParameterCostCase("temperature_low", "kinetics", "Low bath temperature", _set_chem(temperature_c=15.0)),
    ParameterCostCase("temperature_high", "kinetics", "High bath temperature", _set_chem(temperature_c=30.0)),
    ParameterCostCase("concentration_low", "kinetics", "Dilute developer", _set_chem(concentration=0.5)),
    ParameterCostCase("concentration_high", "kinetics", "Concentrated developer", _set_chem(concentration=2.0)),
    ParameterCostCase("agitation_low", "kinetics", "Low agitation without explicit unevenness", _set_chem(agitation=0.25)),
    ParameterCostCase("agitation_high", "kinetics", "High agitation", _set_chem(agitation=2.0)),
    ParameterCostCase("pull_two_stops", "kinetics", "Two-stop pull", _set_chem(push_stops=-2.0)),
    ParameterCostCase("push_two_stops", "kinetics", "Two-stop push", _set_chem(push_stops=2.0)),
    ParameterCostCase("developer_exhaustion", "kinetics", "Strong developer exhaustion", _set_chem(developer_exhaustion=0.8)),
    ParameterCostCase("compensation", "kinetics", "Strong compensating-development control", _set_chem(compensation=0.8)),
    ParameterCostCase("fixer_exhaustion", "retained_material", "Strong fixer exhaustion", _set_chem(fixer_exhaustion=0.8)),
    ParameterCostCase("silver_retention", "retained_material", "Strong retained image silver", _set_chem(silver_retention=0.8)),
    ParameterCostCase("partial_silver_bleach", "retained_material", "Partial bleach / bleach bypass", _set_chem(silver_bleach_completion=0.35)),
    ParameterCostCase("partial_halide_fixing", "retained_material", "Incomplete fixing", _set_chem(halide_fixing_completion=0.45)),
    ParameterCostCase("partial_auxiliary_removal", "retained_material", "Incomplete auxiliary-layer removal", _set_chem(auxiliary_removal=0.45)),
    ParameterCostCase("low_dye_coupling", "retained_material", "Reduced dye-coupling efficiency", _set_chem(dye_coupling_efficiency=0.55)),
    ParameterCostCase("process_layer_balance", "retained_material", "Layer-selective process balance", _set_chem(process_layer_balance=(0.72, 1.0, 1.24))),
    ParameterCostCase(
        "partial_first_development",
        "operator_completion",
        "Partial first development in the negative topology",
        _set_chem(first_development_completion=0.45),
    ),
    ParameterCostCase(
        "strong_mask_bleach",
        "operator_completion",
        "Experimental strong dye/mask bleach",
        _set_chem(mask_bleach_completion=0.85),
    ),
    ParameterCostCase(
        "color_reversal_baseline",
        "operator_topology",
        "Color-reversal topology reference",
        _set_color_reversal(),
    ),
    ParameterCostCase(
        "color_reversal_partial_first_development",
        "operator_completion",
        "Partial first development in color reversal",
        _set_color_reversal(first_development_completion=0.45),
    ),
    ParameterCostCase(
        "color_reversal_partial_second_development",
        "operator_completion",
        "Partial second development in color reversal",
        _set_color_reversal(second_development_completion=0.45),
    ),
    ParameterCostCase(
        "color_reversal_partial_activation",
        "operator_completion",
        "Partial remaining-halide activation in color reversal",
        _set_color_reversal(reversal_activation=0.45),
    ),
    ParameterCostCase(
        "color_reversal_partial_first_silver_removal",
        "operator_completion",
        "Partial first-image silver removal in color reversal",
        _set_color_reversal(first_silver_removal=0.45),
    ),
    ParameterCostCase("developer_fine_grain", "developer_profile", "Fine-grain developer profile", _set_chem(developer_type="fine_grain")),
    ParameterCostCase("developer_compensating", "developer_profile", "Compensating developer profile", _set_chem(developer_type="compensating")),
    ParameterCostCase("developer_high_contrast", "developer_profile", "High-contrast developer profile", _set_chem(developer_type="high_contrast")),
    ParameterCostCase("developer_push", "developer_profile", "Push developer profile", _set_chem(developer_type="push")),
    ParameterCostCase("developer_exhausted_profile", "developer_profile", "Exhausted developer profile", _set_chem(developer_type="exhausted")),
    ParameterCostCase("fixer_rapid", "fixer_profile", "Rapid fixer profile", _set_chem(fixer_type="rapid")),
    ParameterCostCase("fixer_hardening", "fixer_profile", "Hardening fixer profile", _set_chem(fixer_type="hardening")),
    ParameterCostCase(
        "monobath",
        "developer_profile",
        "Monobath process profile without an explicit accident",
        _set_chem(developer_type="monobath", fixer_type="monobath", process_mode="monobath"),
    ),
    ParameterCostCase("frame_half", "material_scale", "Half-frame grain scale", _set_chem(frame_size="half_frame")),
    ParameterCostCase("frame_large_format", "material_scale", "4x5 grain scale", _set_chem(frame_size="4x5")),
    ParameterCostCase("material_degradation", "material_scale", "Strong material ageing/degradation", _set_film(material_degradation=0.75)),
    ParameterCostCase("light_leak", "spatial_accident", "Pre-latent local light leak", _set_chem(light_leak_strength=0.75)),
    ParameterCostCase("chemical_stain", "spatial_accident", "Post-process chemical stain", _set_chem(chemical_stain=0.70)),
    ParameterCostCase(
        "chemical_stain_exhausted_fix",
        "spatial_accident_interaction",
        "Chemical stain amplified by exhausted fixing and weak agitation",
        _set_chem(chemical_stain=0.70, fixer_exhaustion=0.75, agitation=0.35),
    ),
    ParameterCostCase("uneven_development", "spatial_accident", "Spatial developer-rate variation", _set_chem(uneven_development=0.70)),
    ParameterCostCase(
        "uneven_development_stressed",
        "spatial_accident_interaction",
        "Uneven development under low agitation and exhausted developer",
        _set_chem(uneven_development=0.70, agitation=0.25, developer_exhaustion=0.70),
    ),
    ParameterCostCase("silver_plating", "spatial_accident", "Surface metallic-silver deposition", _set_chem(silver_plating=0.65)),
    ParameterCostCase(
        "silver_plating_monobath",
        "spatial_accident_interaction",
        "Surface plating in an exhausted monobath context",
        _set_chem(
            silver_plating=0.45,
            developer_type="monobath",
            fixer_type="monobath",
            process_mode="monobath",
            developer_exhaustion=0.75,
            fixer_exhaustion=0.75,
        ),
    ),
    ParameterCostCase(
        "development_adjacency",
        "spatial_kinetics",
        "First-development adjacency field",
        _set_chem(development_adjacency_strength=0.75, development_adjacency_radius=0.018),
    ),
    ParameterCostCase(
        "development_adjacency_fine",
        "spatial_kinetics",
        "Fine-radius first-development adjacency field",
        _set_chem(development_adjacency_strength=0.75, development_adjacency_radius=0.0025),
    ),
    ParameterCostCase(
        "development_adjacency_broad",
        "spatial_kinetics",
        "Broad-radius first-development adjacency field",
        _set_chem(development_adjacency_strength=0.75, development_adjacency_radius=0.040),
    ),
    ParameterCostCase("process_variation", "variation", "Stochastic bath/process variation", _set_chem(process_variation=0.85)),
    ParameterCostCase(
        "process_variation_with_accidents",
        "variation",
        "Bath variation with explicitly enabled stain and unevenness",
        _set_chem(
            process_variation=0.85,
            chemical_stain=0.45,
            uneven_development=0.45,
        ),
    ),
    ParameterCostCase(
        "combined_accidents",
        "spatial_accident",
        "Leak, unevenness, stain, plating, exhaustion, and retention together",
        _set_chem(
            light_leak_strength=0.65,
            uneven_development=0.65,
            chemical_stain=0.55,
            silver_plating=0.40,
            silver_retention=0.30,
            developer_exhaustion=0.28,
            fixer_exhaustion=0.25,
            agitation=0.45,
        ),
    ),
)


PARAMETER_COST_KEYS = tuple(item.key for item in PARAMETER_COST_CASES)


def _base_config(seed: int) -> DarkroomConfig:
    config = DarkroomConfig()
    config.processing.execution_mode = "quality"
    config.processing.quality_mode = "high"
    config.processing.material_tile_rows = 0
    config.seed_strategy = "fixed"
    config.random_seed = int(seed)
    config.enable_mtf = False
    config.enable_halation = False
    config.enable_grain = True
    config.debug_output = False
    config.comparison_grid = False
    return config


def _digest(array: np.ndarray) -> str:
    values = np.ascontiguousarray(array)
    digest = hashlib.sha256()
    digest.update(str(values.dtype).encode("ascii"))
    digest.update(repr(tuple(int(value) for value in values.shape)).encode("ascii"))
    digest.update(memoryview(values).cast("B"))
    return digest.hexdigest()


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "min": float(min(values)),
        "median": float(median(values)),
        "mean": float(mean(values)),
        "max": float(max(values)),
    }


def _active_branches(medium) -> dict[str, object]:
    metadata = medium.metadata
    model = metadata.get("film_process_model", {})
    effective = model.get("effective_development", {}) if isinstance(model, dict) else {}
    adjacency = model.get("development_adjacency", {}) if isinstance(model, dict) else {}
    effective_cost_drivers = {
        key: effective.get(key)
        for key in (
            "grain_factor",
            "grain_radius_factor",
            "residue_factor",
            "silvering_factor",
            "uneven_development",
        )
        if key in effective
    }
    return {
        "has_light_leak_map": bool(metadata.get("has_light_leak_map", False)),
        "accident_maps": list(metadata.get("accident_maps", ())),
        "development_adjacency_applied": bool(
            adjacency.get("applied", False) if isinstance(adjacency, dict) else False
        ),
        "developer_profile": effective.get("developer_profile"),
        "fixer_profile": effective.get("fixer_profile"),
        "process_program": (
            model.get("process_program", {}).get("key")
            if isinstance(model, dict) and isinstance(model.get("process_program"), dict)
            else None
        ),
        "process_variation": copy.deepcopy(metadata.get("process_variation", {})),
        "effective_cost_drivers": effective_cost_drivers,
    }


def run_process_parameter_audit(
    *,
    width: int = 1200,
    height: int = 800,
    repeats: int = 1,
    seed: int = 20260722,
    cases: tuple[str, ...] | list[str] | None = None,
) -> dict[str, object]:
    if width <= 0 or height <= 0:
        raise ValueError("Parameter audit width and height must be positive.")
    if repeats <= 0:
        raise ValueError("Parameter audit repeats must be positive.")
    selected = PARAMETER_COST_KEYS if cases is None else tuple(str(item) for item in cases)
    if not selected:
        raise ValueError("Parameter audit requires at least one selected case.")
    if len(selected) != len(set(selected)):
        raise ValueError("Parameter audit selections must not contain duplicates.")
    unknown = tuple(item for item in selected if item not in PARAMETER_COST_KEYS)
    if unknown:
        raise ValueError(f"Unknown parameter audit key(s): {', '.join(unknown)}")

    image = synthetic_reference_image(width, height)
    image.setflags(write=False)
    source_hash = _digest(image)
    cases_by_key = {item.key: item for item in PARAMETER_COST_CASES}
    reports: dict[str, object] = {}
    baseline_seconds: float | None = None
    baseline_peak: float | None = None
    for key in selected:
        case = cases_by_key[key]
        elapsed_samples: list[float] = []
        peak_samples: list[float] = []
        expected_hashes: dict[str, str] | None = None
        branches: dict[str, object] | None = None
        for _ in range(repeats):
            config = _base_config(seed)
            case.mutate(config)
            gc.collect()
            tracemalloc.start()
            started = time.perf_counter()
            try:
                medium = develop_negative(
                    image,
                    config,
                    rng=np.random.default_rng(seed),
                )
                elapsed = time.perf_counter() - started
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            hashes = {
                "density_grain": _digest(medium.density_grain),
                "optical_density_rgb": _digest(medium.optical_density_rgb),
            }
            if expected_hashes is None:
                expected_hashes = hashes
                branches = _active_branches(medium)
            elif hashes != expected_hashes:
                raise RuntimeError(f"Parameter audit '{key}' changed across fixed-seed repetitions")
            elapsed_samples.append(float(elapsed))
            peak_samples.append(float(peak) / (1024.0 * 1024.0))
            del medium, config
        elapsed_summary = _summary(elapsed_samples)
        peak_summary = _summary(peak_samples)
        if key == "baseline":
            baseline_seconds = elapsed_summary["median"]
            baseline_peak = peak_summary["median"]
        reports[key] = {
            "category": case.category,
            "description": case.description,
            "elapsed_seconds": elapsed_summary,
            "python_numpy_traced_peak_mib": peak_summary,
            "determinism": {
                "hashes": expected_hashes,
                "stable_across_repeats": True,
            },
            "active_branches": branches,
        }

    # Relative costs require a baseline even when the caller selected only a
    # subset. Run it once outside the requested report rather than comparing
    # unrelated parameter cases to zero.
    if baseline_seconds is None or baseline_peak is None:
        baseline = run_process_parameter_audit(
            width=width,
            height=height,
            repeats=repeats,
            seed=seed,
            cases=("baseline",),
        )["parameters"]["baseline"]
        baseline_seconds = float(baseline["elapsed_seconds"]["median"])
        baseline_peak = float(baseline["python_numpy_traced_peak_mib"]["median"])
    for result in reports.values():
        result["relative_to_baseline"] = {
            "time_ratio": float(result["elapsed_seconds"]["median"]) / max(baseline_seconds, 1e-12),
            "peak_mib_delta": float(result["python_numpy_traced_peak_mib"]["median"]) - baseline_peak,
        }

    if source_hash != _digest(image):
        raise RuntimeError("Parameter audit modified its shared source image")
    return {
        "schema_version": 1,
        "image": {"width": int(width), "height": int(height), "pixels": int(width * height)},
        "runtime": {
            "seed": int(seed),
            "repeats": int(repeats),
            "selected": list(selected),
            "execution_mode": "quality",
            "quality_mode": "high",
            "material_tile_rows": 0,
            "mtf_enabled": False,
            "halation_enabled": False,
            "grain_enabled": True,
        },
        "source_image_unchanged": True,
        "baseline": {
            "elapsed_seconds_median": baseline_seconds,
            "python_numpy_traced_peak_mib_median": baseline_peak,
        },
        "parameters": reports,
        "notes": [
            "Each case changes one process/material control from the same public develop boundary.",
            "The audit deliberately disables material tiling and retains public diagnostic histories; use the production benchmark for main-flow peak memory.",
            "A time ratio near one is expected for scalar kinetics; spatial fields may add real work.",
            "Color-reversal completion cases should be compared with color_reversal_baseline; the global ratio remains anchored to the standard color-negative baseline.",
            "Traced peak excludes the shared synthetic source and does not include all OpenCV native allocations.",
            "Use larger production/RSS runs only after this audit identifies a candidate hotspot.",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260722)
    parser.add_argument("--case", action="append", choices=PARAMETER_COST_KEYS)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list:
        for item in PARAMETER_COST_CASES:
            print(f"{item.key}\t{item.category}\t{item.description}")
        return 0
    report = run_process_parameter_audit(
        width=args.width,
        height=args.height,
        repeats=args.repeats,
        seed=args.seed,
        cases=args.case,
    )
    if args.json_output is not None:
        atomic_write_json(args.json_output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
