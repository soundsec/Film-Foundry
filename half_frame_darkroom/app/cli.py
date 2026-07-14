"""Command-line entry point for Film Foundry.

This CLI is the terminal-oriented sibling of film_foundry/tools/run_darkroom.py.  It supports the
same three-stage workflow without asking users to edit Python variables:

    full      image -> developed negative/reversal-positive -> observed image
    develop   image -> .npz density master + transmissive medium materials
    scan      .npz/scanner raw/light-table raw -> observed image
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import sys

import numpy as np

from half_frame_darkroom.core.engine import (
    apply_optical_observation_snapshot,
    configure_scan_interpretation,
    develop_negative,
    process_file,
    scan_medium_direct,
    scan_scanner_raw_direct,
    save_developed_medium_at_path,
    seed_from_path,
)
from half_frame_darkroom.core.electronic_negative import (
    load_linear_rgb_tiff,
    split_scanner_raw_border,
)
from half_frame_darkroom.core.execution import processing_long_edge, resolve_execution_mode
from half_frame_darkroom.core.io_utils import SUPPORTED_EXTENSIONS, assert_unique_output_stems, iter_images, load_image, output_target_is_file, probe_image_dimensions, save_image_bundle, scan_output_stem
from half_frame_darkroom.core.negative_io import load_developed_negative_npz
from half_frame_darkroom.core.preview import negative_visual_preview, resize_to_long_edge
from half_frame_darkroom.core.resource_planning import (
    enforce_memory_budget,
    estimate_pipeline_memory,
    warn_outside_comfort_zone,
)
from half_frame_darkroom.core.sidecar import (
    final_positive_sidecar,
    load_scanner_raw_sidecar,
    scanner_raw_border_width_from_sidecar,
    scanner_raw_optical_observation_from_sidecar,
    transmission_raw_source_kind,
)
from half_frame_darkroom.core.states import DevelopedNegative
from half_frame_darkroom.model.config import DarkroomConfig, merge_config_presets
from half_frame_darkroom.model.session import config_from_session
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
DEVELOP_PRESET_DIR = PRESET_DIR / "develop"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
USER_PRESET_DIR = PROJECT_ROOT / "user_presets"
USER_FILM_PRESET_DIR = USER_PRESET_DIR / "film"
USER_DEVELOP_PRESET_DIR = USER_PRESET_DIR / "develop"
USER_SCANNER_PRESET_DIR = USER_PRESET_DIR / "scanner"
NEGATIVE_SUFFIX = ".darkroom_negative.npz"
POSITIVE_SUFFIX = ".darkroom_positive.npz"


def _preset_path(value: str | None, kind: str | None = None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.exists():
        return path
    if kind == "film":
        candidates = (USER_FILM_PRESET_DIR / f"{value}.json", FILM_PRESET_DIR / f"{value}.json")
    elif kind == "develop":
        candidates = (USER_DEVELOP_PRESET_DIR / f"{value}.json", DEVELOP_PRESET_DIR / f"{value}.json")
    elif kind == "scanner":
        candidates = (USER_SCANNER_PRESET_DIR / f"{value}.json", SCANNER_PRESET_DIR / f"{value}.json")
    else:
        candidates = (USER_PRESET_DIR / f"{value}.json", PRESET_DIR / f"{value}.json")
    for named in candidates:
        if named.exists():
            return named
    raise FileNotFoundError(f"Preset not found: {value}")


def _clean_stem(path: Path) -> str:
    return scan_output_stem(path)


def _output_path_for(input_path: Path, output_root: Path, output_format: str) -> Path:
    suffix = "." + output_format.lower().lstrip(".")
    if output_target_is_file(output_root):
        return output_root
    return output_root / f"{_clean_stem(input_path)}_foundry{suffix}"


def _negative_path_for(input_path: Path, negative_root: Path) -> Path:
    if negative_root.suffix.lower() == ".npz":
        return negative_root
    return negative_root / f"{input_path.stem}{NEGATIVE_SUFFIX}"


def _developed_path_for(input_path: Path, output_root: Path, medium: DevelopedNegative) -> Path:
    if output_root.suffix.lower() == ".npz":
        return output_root
    suffix = POSITIVE_SUFFIX if str(medium.image_polarity).lower() == "positive" else NEGATIVE_SUFFIX
    return output_root / f"{input_path.stem}{suffix}"


def _ensure_batch_output_target(items: list[Path], output_root: Path, label: str) -> None:
    """批处理时禁止把多个结果写进同一个文件路径。"""
    if len(items) > 1 and output_target_is_file(output_root):
        raise ValueError(
            f"{label} output must be a folder when processing multiple files: {output_root}"
        )
    if len(items) > 1:
        assert_unique_output_stems(items, label)


def _scanner_raw_path_for_negative(negative_path: Path) -> Path:
    return negative_path.with_suffix(".scanner_raw.tiff")


def _raw_path_for_medium(medium_path: Path) -> Path:
    if medium_path.name.lower().endswith(POSITIVE_SUFFIX):
        return medium_path.with_suffix(".light_table_raw.tiff")
    return _scanner_raw_path_for_negative(medium_path)


def _is_scanner_raw_tiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"} and any(token in path.stem.lower() for token in (".scanner_raw", ".light_table_raw"))


def _iter_negative_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".npz" or _is_scanner_raw_tiff(path) else []
    if path.is_dir():
        npz_paths = sorted(path.glob(f"*{NEGATIVE_SUFFIX}")) + sorted(path.glob(f"*{POSITIVE_SUFFIX}"))
        raw_paths = sorted(item for item in path.glob("*.tif*") if _is_scanner_raw_tiff(item))
        npz_raw_paths = {_raw_path_for_medium(item).resolve() for item in npz_paths}
        return npz_paths + [item for item in raw_paths if item.resolve() not in npz_raw_paths]
    return []


def _resolved_seed_for(path: Path, config: DarkroomConfig) -> int | None:
    strategy = str(config.seed_strategy).lower()
    if strategy == "fixed":
        return 0 if config.random_seed is None else int(config.random_seed)
    if strategy == "path":
        return seed_from_path(path, 0 if config.random_seed is None else int(config.random_seed))
    return None


def _rng_for_develop(path: Path, config: DarkroomConfig) -> np.random.Generator:
    return np.random.default_rng(_resolved_seed_for(path, config))


def _load_negative(path: Path) -> DevelopedNegative:
    return load_developed_negative_npz(path)


def _save_negative(
    negative: DevelopedNegative,
    path: Path,
    input_path: Path,
    config: DarkroomConfig,
) -> dict[str, str]:
    """Compatibility name for the unified developed-medium exporter."""
    return save_developed_medium_at_path(
        input_path,
        path,
        negative,
        config,
        resolved_seed=_resolved_seed_for(input_path, config),
    )


def _scan_from_file(path: Path, config: DarkroomConfig):
    # Isolate per-file sidecar optics so batch items cannot contaminate the
    # editable scanner configuration or each other.
    config = copy.deepcopy(config)
    interpretation = str(config.scanner.interpretation_mode or "auto")
    if _is_scanner_raw_tiff(path):
        raw_sidecar = load_scanner_raw_sidecar(path)
        apply_optical_observation_snapshot(
            config,
            scanner_raw_optical_observation_from_sidecar(raw_sidecar),
        )
        scanner_raw = load_linear_rgb_tiff(path)
        source_kind = transmission_raw_source_kind(path, raw_sidecar)
        border_width = scanner_raw_border_width_from_sidecar(raw_sidecar, scanner_raw.shape)
        if border_width is not None:
            inner, border_samples = split_scanner_raw_border(
                scanner_raw,
                border_width_px=border_width,
            )
        elif source_kind == "light_table_raw_tiff":
            inner, border_samples = scanner_raw, None
        else:
            inner, border_samples = split_scanner_raw_border(
                scanner_raw,
                border_percent=config.output.scanner_raw_border_percent,
                border_min_px=config.output.scanner_raw_border_min_px,
            )
        scanned = scan_scanner_raw_direct(
            inner,
            config,
            interpretation,
            base_samples=border_samples,
            source_path=path,
            raw_source_kind=source_kind,
        )
        return scanned, source_kind, path

    scanner_raw_path = _raw_path_for_medium(path)
    if scanner_raw_path.exists():
        raw_sidecar = load_scanner_raw_sidecar(scanner_raw_path)
        apply_optical_observation_snapshot(
            config,
            scanner_raw_optical_observation_from_sidecar(raw_sidecar),
        )
        scanner_raw = load_linear_rgb_tiff(scanner_raw_path)
        source_kind = transmission_raw_source_kind(scanner_raw_path, raw_sidecar)
        border_width = scanner_raw_border_width_from_sidecar(raw_sidecar, scanner_raw.shape)
        if border_width is not None:
            inner, border_samples = split_scanner_raw_border(
                scanner_raw,
                border_width_px=border_width,
            )
        elif source_kind == "light_table_raw_tiff":
            inner, border_samples = scanner_raw, None
        else:
            inner, border_samples = split_scanner_raw_border(
                scanner_raw,
                border_percent=config.output.scanner_raw_border_percent,
                border_min_px=config.output.scanner_raw_border_min_px,
            )
        return (
            scan_scanner_raw_direct(
                inner,
                config,
                interpretation,
                base_samples=border_samples,
                source_path=scanner_raw_path,
                raw_source_kind=source_kind,
            ),
            source_kind,
            scanner_raw_path,
        )

    if path.suffix.lower() != ".npz":
        raise ValueError(f"Unsupported negative file: {path}. Use .npz or .scanner_raw.tiff, not sidecar .json.")
    negative = _load_negative(path)
    return scan_medium_direct(negative, config, interpretation), "density_npz", path


def _add_common_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session", type=Path, help="GUI-exported Film Foundry session JSON; CLI options override it.")
    parser.add_argument("--preset", help="Full config example name or JSON path; overrides split preset loading.")
    parser.add_argument("--seed", type=int, help="Random seed.")
    parser.add_argument("--seed-strategy", choices=("random", "fixed", "path"), help="Random seed strategy.")
    parser.add_argument("--fast", action="store_true", help="Legacy alias for --processing-mode reduced_fast.")
    parser.add_argument(
        "--processing-mode",
        choices=("quality", "scaled_fast", "reduced_fast"),
        help="Execution policy: requested size, resized fast, or same-size reduced fast.",
    )
    parser.add_argument("--quality-mode", choices=("draft", "standard", "high"), help="Internal processing quality preset.")
    parser.add_argument("--halation-work-long-edge", type=int, help="Work long edge for halation low-frequency spread; 0 disables resizing.")
    parser.add_argument("--grain-work-long-edge", type=int, help="Work long edge for density grain random field; 0 disables resizing.")
    parser.add_argument("--material-tile-rows", type=int, help="Exact large-frame material-pool tile rows; 0 disables tiling.")
    parser.add_argument("--material-tile-threshold-mp", type=float, help="Megapixel threshold for exact material-pool tiling.")
    parser.add_argument("--scan-tile-rows", type=int, help="Exact output-only scan tile rows; 0 disables scan tiling.")
    parser.add_argument("--scan-tile-threshold-mp", type=float, help="Megapixel threshold for output-only scan tiling.")
    parser.add_argument("--memory-budget-mb", type=float, help="Explicit estimated pipeline memory budget in MiB.")
    parser.add_argument("--memory-budget-policy", choices=("allow", "warn", "error"), help="Action when the conservative memory estimate exceeds the budget.")
    parser.add_argument("--comfort-zone-mp", type=float, help="Best-supported working-size boundary; larger jobs remain best-effort.")
    parser.add_argument("--format", choices=("png", "jpg", "jpeg", "tif", "tiff", "webp"), help="Output format.")
    parser.add_argument("--bit-depth", type=int, choices=(8, 16), help="Output bit depth.")
    parser.add_argument("--quality", type=int, help="JPEG/WebP quality.")
    parser.add_argument("--anti-banding", type=float, help="8-bit output anti-banding dither strength in [0, 1].")
    parser.add_argument("--encode-tile-rows", type=int, help="Bounded output quantization rows; 0 disables tiling.")
    parser.add_argument("--encode-tile-threshold-mp", type=float, help="Megapixel threshold for tiled output quantization.")
    parser.add_argument("--medium-npz-compression", choices=("compressed", "store"), help="Developed-medium archive: smaller compressed file or faster stored arrays.")
    parser.add_argument("--render-long-edge", type=int, help="Resize final render longest edge.")
    parser.add_argument("--preview-long-edge", type=int, help="Resize preview longest edge.")
    parser.add_argument("--debug-output", action="store_true", help="Save intermediate debug outputs.")
    parser.add_argument("--comparison-grid", action="store_true", help="Save comparison grid with debug output.")
    parser.add_argument("--no-sidecar", action="store_true", help="Do not save sidecar JSON.")


def _add_develop_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--film-preset", help="Film material preset name or JSON path.")
    parser.add_argument("--material-degradation", type=float, help="Film ageing / poor-storage severity in [0, 1].")
    parser.add_argument("--develop-preset", help="Darkroom process preset name or JSON path.")
    parser.add_argument("--exposure-ev", type=float, help="Input exposure proxy EV before negative formation.")
    parser.add_argument("--negative-contrast", type=float, help="H-D gamma multiplier.")
    parser.add_argument("--dye-selectivity", type=float, help="Film dye absorption selectivity multiplier.")
    parser.add_argument("--halation", type=float, help="Halation multiplier.")
    parser.add_argument("--halation-sensitivity", type=float, help="Temporary halation trigger sensitivity; positive lowers the effective threshold.")
    parser.add_argument("--grain", type=float, help="Grain multiplier.")
    parser.add_argument("--grain-size", type=float, help="Grain correlation size multiplier; base size is relative to image frame.")
    parser.add_argument("--developer-type", help="Developer response type, e.g. standard, fine_grain, compensating, push, monobath.")
    parser.add_argument("--fixer-type", help="Fixer or clearing type, e.g. standard, rapid, hardening, monobath.")
    parser.add_argument("--frame-size", help="Frame size for visible grain scaling, e.g. half_frame, 35mm, 6x6, 6x7, 4x5.")
    parser.add_argument("--develop-time", type=float, help="Development time in minutes.")
    parser.add_argument("--concentration", type=float, help="Developer concentration multiplier.")
    parser.add_argument("--agitation", type=float, help="Agitation intensity multiplier.")
    parser.add_argument("--process-mode", help="Process mode, e.g. normal_negative, bw_reversal, cross_process.")
    parser.add_argument("--compensation", type=float, help="Compensating development strength in [0, 1].")
    parser.add_argument("--push", type=float, help="Chemistry push stops.")
    parser.add_argument("--temperature", type=float, help="Chemistry temperature in Celsius.")
    parser.add_argument("--exhaustion", type=float, help="Developer exhaustion in [0, 1].")
    parser.add_argument("--fixer-exhaustion", type=float, help="Fixer exhaustion / clearing failure in [0, 1].")
    parser.add_argument("--silver-retention", type=float, help="Retained image silver / bleach-bypass strength in [0, 1].")
    parser.add_argument("--silver-plating", type=float, help="Surface metallic-silver deposition accident in [0, 1].")
    parser.add_argument("--light-leak", type=float, help="Intentional light leak accident strength in [0, 1].")
    parser.add_argument("--chemical-stain", type=float, help="Dirty chemistry / kelp-like stain accident strength in [0, 1].")
    parser.add_argument("--uneven-development", type=float, help="Uneven development mottling accident strength in [0, 1].")
    parser.add_argument("--process-variation", type=float, help="Per-run process variation strength in [0, 1].")
    parser.add_argument("--bw", action="store_true", help="Use black-and-white negative mode.")
    parser.add_argument("--no-mtf", action="store_true", help="Disable emulsion MTF.")
    parser.add_argument("--no-halation", action="store_true", help="Disable halation.")
    parser.add_argument("--no-grain", action="store_true", help="Disable grain.")


def _add_scan_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scanner-preset", help="Scanner/render preset name or JSON path.")
    parser.add_argument(
        "--interpretation",
        choices=("auto", "negative", "positive"),
        help="Interpretation mode. Default auto follows NPZ/raw medium identity.",
    )
    parser.add_argument("--print-contrast", type=float, help="Scan/render contrast multiplier.")
    parser.add_argument("--print-exposure-ev", type=float, help="Scan/render exposure EV.")
    parser.add_argument("--saturation", type=float, help="Scan/output saturation multiplier.")
    parser.add_argument("--no-subtractive", action="store_true", help="Disable subtractive scan/render.")
    parser.add_argument("--no-scan-normalize", action="store_true", help="Disable scan black/white normalization.")
    parser.add_argument("--scan-normalize-strength", type=float, help="Scan normalization blend strength.")


def _apply_common_config(args: argparse.Namespace) -> DarkroomConfig:
    session_path = getattr(args, "session", None)
    preset = _preset_path(getattr(args, "preset", None))
    if preset is not None:
        config = DarkroomConfig.from_json(preset)
    elif session_path is not None:
        config = config_from_session(session_path)
    else:
        command = str(getattr(args, "command", "full"))
        film_value = getattr(args, "film_preset", None)
        develop_value = getattr(args, "develop_preset", None)
        scanner_value = getattr(args, "scanner_preset", None)
        if film_value is None and command in {"full", "develop"}:
            film_value = "clear_modern_negative"
        if develop_value is None and command in {"full", "develop"}:
            develop_value = "standard_color_negative"
        if scanner_value is None and command in {"full", "scan"}:
            scanner_value = "neutral_scan"
        film_preset = _preset_path(film_value, "film")
        develop_preset = _preset_path(develop_value, "develop")
        scanner_preset = _preset_path(scanner_value, "scanner")
        film_config = DarkroomConfig.from_json(film_preset) if film_preset is not None else None
        develop_config = DarkroomConfig.from_json(develop_preset) if develop_preset is not None else None
        scanner_config = DarkroomConfig.from_json(scanner_preset) if scanner_preset is not None else None
        config = merge_config_presets(film_config, scanner_config, develop_config=develop_config)

    if getattr(args, "seed", None) is not None:
        config.random_seed = args.seed
    if getattr(args, "seed_strategy", None) is not None:
        config.seed_strategy = args.seed_strategy
    requested_processing_mode = getattr(args, "processing_mode", None)
    if requested_processing_mode is not None:
        config.processing.execution_mode = str(requested_processing_mode)
        config.fast_mode = requested_processing_mode == "reduced_fast"
    if getattr(args, "fast", False):
        if requested_processing_mode not in {None, "reduced_fast"}:
            raise ValueError("--fast conflicts with the selected --processing-mode.")
        config.fast_mode = True
        config.processing.execution_mode = "reduced_fast"
    if getattr(args, "quality_mode", None) is not None:
        config.processing.quality_mode = args.quality_mode
    if getattr(args, "halation_work_long_edge", None) is not None:
        value = int(args.halation_work_long_edge)
        config.processing.halation_work_long_edge = None if value <= 0 else value
    if getattr(args, "grain_work_long_edge", None) is not None:
        value = int(args.grain_work_long_edge)
        config.processing.grain_work_long_edge = None if value <= 0 else value
    if getattr(args, "material_tile_rows", None) is not None:
        config.processing.material_tile_rows = int(args.material_tile_rows)
    if getattr(args, "material_tile_threshold_mp", None) is not None:
        config.processing.material_tile_threshold_megapixels = float(
            args.material_tile_threshold_mp
        )
    if getattr(args, "scan_tile_rows", None) is not None:
        config.processing.scan_tile_rows = int(args.scan_tile_rows)
    if getattr(args, "scan_tile_threshold_mp", None) is not None:
        config.processing.scan_tile_threshold_megapixels = float(
            args.scan_tile_threshold_mp
        )
    if getattr(args, "memory_budget_mb", None) is not None:
        config.processing.memory_budget_mb = float(args.memory_budget_mb)
    if getattr(args, "memory_budget_policy", None) is not None:
        config.processing.memory_budget_policy = str(args.memory_budget_policy)
    if getattr(args, "comfort_zone_mp", None) is not None:
        config.processing.comfort_zone_megapixels = float(args.comfort_zone_mp)
    if getattr(args, "format", None) is not None:
        config.output.format = args.format
    if getattr(args, "bit_depth", None) is not None:
        config.output.bit_depth = args.bit_depth
    if getattr(args, "quality", None) is not None:
        config.output.quality = args.quality
    if getattr(args, "anti_banding", None) is not None:
        config.output.anti_banding_strength = args.anti_banding
    if getattr(args, "encode_tile_rows", None) is not None:
        config.output.encode_tile_rows = int(args.encode_tile_rows)
    if getattr(args, "encode_tile_threshold_mp", None) is not None:
        config.output.encode_tile_threshold_megapixels = float(
            args.encode_tile_threshold_mp
        )
    if getattr(args, "medium_npz_compression", None) is not None:
        config.output.medium_npz_compression = str(args.medium_npz_compression)
    if getattr(args, "render_long_edge", None) is not None:
        config.output.render_long_edge = args.render_long_edge
    if getattr(args, "preview_long_edge", None) is not None:
        config.output.preview_long_edge = args.preview_long_edge
    if getattr(args, "exposure_ev", None) is not None:
        config.look.exposure_ev = args.exposure_ev
    if getattr(args, "material_degradation", None) is not None:
        config.film.material_degradation = args.material_degradation
    if getattr(args, "negative_contrast", None) is not None:
        config.look.negative_contrast = args.negative_contrast
    if getattr(args, "print_contrast", None) is not None:
        config.look.print_contrast = args.print_contrast
    if getattr(args, "print_exposure_ev", None) is not None:
        config.look.print_exposure_ev = args.print_exposure_ev
    if getattr(args, "saturation", None) is not None:
        config.scanner.scan_saturation = args.saturation
    if getattr(args, "interpretation", None) is not None:
        configure_scan_interpretation(config, args.interpretation)
    if getattr(args, "dye_selectivity", None) is not None:
        config.look.saturation_multiplier = args.dye_selectivity
    if getattr(args, "halation", None) is not None:
        config.look.halation_multiplier = args.halation
    if getattr(args, "halation_sensitivity", None) is not None:
        config.look.halation_sensitivity = args.halation_sensitivity
    if getattr(args, "grain", None) is not None:
        config.look.grain_multiplier = args.grain
    if getattr(args, "grain_size", None) is not None:
        config.look.grain_size_multiplier = args.grain_size
    if getattr(args, "developer_type", None) is not None:
        config.chemistry.developer_type = args.developer_type
        config.chemistry.developer_name = str(args.developer_type).replace("_", " ").title()
    if getattr(args, "fixer_type", None) is not None:
        config.chemistry.fixer_type = args.fixer_type
        config.chemistry.fixer_name = str(args.fixer_type).replace("_", " ").title()
    if getattr(args, "frame_size", None) is not None:
        config.chemistry.frame_size = args.frame_size
    if getattr(args, "develop_time", None) is not None:
        config.chemistry.time_min = args.develop_time
    if getattr(args, "concentration", None) is not None:
        config.chemistry.concentration = args.concentration
    if getattr(args, "agitation", None) is not None:
        config.chemistry.agitation = args.agitation
    if getattr(args, "process_mode", None) is not None:
        config.chemistry.process_mode = args.process_mode
    if getattr(args, "compensation", None) is not None:
        config.chemistry.compensation = args.compensation
    if getattr(args, "push", None) is not None:
        config.chemistry.push_stops = args.push
    if getattr(args, "temperature", None) is not None:
        config.chemistry.temperature_c = args.temperature
    if getattr(args, "exhaustion", None) is not None:
        config.chemistry.developer_exhaustion = args.exhaustion
    if getattr(args, "fixer_exhaustion", None) is not None:
        config.chemistry.fixer_exhaustion = args.fixer_exhaustion
    if getattr(args, "silver_retention", None) is not None:
        config.chemistry.silver_retention = args.silver_retention
    if getattr(args, "silver_plating", None) is not None:
        config.chemistry.silver_plating = args.silver_plating
    if getattr(args, "light_leak", None) is not None:
        config.chemistry.light_leak_strength = args.light_leak
    if getattr(args, "chemical_stain", None) is not None:
        config.chemistry.chemical_stain = args.chemical_stain
    if getattr(args, "uneven_development", None) is not None:
        config.chemistry.uneven_development = args.uneven_development
    if getattr(args, "process_variation", None) is not None:
        config.chemistry.process_variation = args.process_variation
    if getattr(args, "bw", False):
        config.mode = "bw_negative"
    if getattr(args, "no_mtf", False):
        config.enable_mtf = False
    if getattr(args, "no_halation", False):
        config.enable_halation = False
    if getattr(args, "no_grain", False):
        config.enable_grain = False
    if getattr(args, "no_subtractive", False):
        config.enable_subtractive = False
    if getattr(args, "no_scan_normalize", False):
        config.scanner.scan_normalize = False
    if getattr(args, "scan_normalize_strength", None) is not None:
        config.scanner.scan_normalize_strength = args.scan_normalize_strength
    if getattr(args, "debug_output", False):
        config.debug_output = True
    if getattr(args, "comparison_grid", False):
        config.comparison_grid = True
    if getattr(args, "no_sidecar", False):
        config.save_sidecar = False
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="film-foundry",
        description="Film Foundry / Electronic Negative Factory command-line workflow.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    full = subparsers.add_parser("full", help="Input image(s) -> final scanned positive.")
    full.add_argument("input", type=Path, help="Input image file or folder.")
    full.add_argument("output", type=Path, help="Output image file or folder.")
    full.add_argument("--preview", action="store_true", help="Use preview_long_edge for processing.")
    _add_common_config_args(full)
    _add_develop_config_args(full)
    _add_scan_config_args(full)

    develop = subparsers.add_parser("develop", help="Input image(s) -> reusable negative/material masters.")
    develop.add_argument("input", type=Path, help="Input image file or folder.")
    develop.add_argument("negative_output", type=Path, help="Output .npz file or folder for negatives.")
    develop.add_argument("--preview", action="store_true", help="Use preview_long_edge for developing.")
    develop.add_argument("--no-scanner-raw", action="store_true", help="Do not save scanner_raw.tiff.")
    develop.add_argument("--scanner-raw-border", type=float, help="Scanner raw clear-base border percent, e.g. 0.04.")
    develop.add_argument("--scanner-raw-border-min-px", type=int, help="Minimum scanner raw border width.")
    develop.add_argument("--layer-pack", action="store_true", help="Export full layer material pack.")
    develop.add_argument("--no-transparent-plate", action="store_true", help="Disable transparent negative export.")
    develop.add_argument("--plate-set", action="store_true", help="Export CMY/density/grain/halation plate layers.")
    develop.add_argument("--no-plate-set", action="store_true", help="Disable CMY/grain/halation plate export.")
    _add_common_config_args(develop)
    _add_develop_config_args(develop)

    scan = subparsers.add_parser("scan", help="Observe developed .npz, negative scanner raw, or positive light-table raw.")
    scan.add_argument("negative", type=Path, help=".npz/.scanner_raw.tiff file or folder.")
    scan.add_argument("output", type=Path, help="Output image file or folder.")
    scan.add_argument("--scanner-raw-border", type=float, help="Scanner raw clear-base border percent.")
    scan.add_argument("--scanner-raw-border-min-px", type=int, help="Minimum scanner raw border width.")
    _add_common_config_args(scan)
    _add_scan_config_args(scan)
    return parser


def _run_full(args: argparse.Namespace, config: DarkroomConfig) -> int:
    inputs = iter_images(args.input)
    if not inputs:
        raise FileNotFoundError(f"No supported images found in {args.input}")
    _ensure_batch_output_target(inputs, args.output, "Final image")
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for input_path in inputs:
        try:
            output_path = _output_path_for(input_path, args.output, config.output.format)
            process_file(input_path, output_path, config, preview=bool(args.preview))
            print(f"[full] {input_path} -> {output_path}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[full:error] {input_path}: {exc}", file=sys.stderr)
    _raise_batch_failures("full", completed, failures)
    return completed


def _run_develop(args: argparse.Namespace, config: DarkroomConfig) -> int:
    config.output.save_scanner_raw = not bool(args.no_scanner_raw)
    if args.scanner_raw_border is not None:
        config.output.scanner_raw_border_percent = args.scanner_raw_border
    if args.scanner_raw_border_min_px is not None:
        config.output.scanner_raw_border_min_px = args.scanner_raw_border_min_px
    if args.layer_pack:
        config.output.export_layer_pack = True
    if args.no_transparent_plate:
        config.output.export_transparent_plate = False
    if args.plate_set:
        config.output.export_plate_set = True
    if args.no_plate_set:
        config.output.export_plate_set = False

    inputs = iter_images(args.input)
    if not inputs:
        raise FileNotFoundError(f"No supported images found in {args.input}")
    _ensure_batch_output_target(inputs, args.negative_output, "Developed negative")
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for input_path in inputs:
        try:
            long_edge = processing_long_edge(config, scaled_override=bool(args.preview))
            width, height = probe_image_dimensions(input_path)
            estimate = estimate_pipeline_memory(
                width,
                height,
                long_edge=long_edge,
                retain_development_stages=True,
                comfort_zone_megapixels=config.processing.comfort_zone_megapixels,
                decoder_reduced=(
                    resolve_execution_mode(config, scaled_override=bool(args.preview))
                    == "scaled_fast"
                    and input_path.suffix.lower() in {".jpg", ".jpeg"}
                ),
            )
            warn_outside_comfort_zone(estimate)
            enforce_memory_budget(
                estimate,
                config.processing.memory_budget_mb,
                config.processing.memory_budget_policy,
            )
            execution_mode = resolve_execution_mode(
                config,
                scaled_override=bool(args.preview),
            )
            runtime_config = copy.deepcopy(config)
            runtime_config.processing.execution_mode = execution_mode
            runtime_config.fast_mode = execution_mode == "reduced_fast"
            if execution_mode == "scaled_fast":
                image = load_image(input_path, decode_long_edge=long_edge)
            else:
                image = load_image(input_path)
            image = resize_to_long_edge(image, long_edge)
            negative = develop_negative(
                image,
                runtime_config,
                rng=_rng_for_develop(input_path, runtime_config),
            )
            del image
            negative_path = _developed_path_for(input_path, args.negative_output, negative)
            paths = _save_negative(negative, negative_path, input_path, runtime_config)
            print(f"[develop] {input_path} -> {negative_path}")
            if "scanner_raw_path" in paths:
                print(f"          scanner raw: {paths['scanner_raw_path']}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[develop:error] {input_path}: {exc}", file=sys.stderr)
    _raise_batch_failures("develop", completed, failures)
    return completed


def _run_scan(args: argparse.Namespace, config: DarkroomConfig) -> int:
    if args.scanner_raw_border is not None:
        config.output.scanner_raw_border_percent = args.scanner_raw_border
    if args.scanner_raw_border_min_px is not None:
        config.output.scanner_raw_border_min_px = args.scanner_raw_border_min_px

    negatives = _iter_negative_files(args.negative)
    if not negatives:
        raise FileNotFoundError(f"No developed .npz, scanner raw, or light-table raw found in {args.negative}")
    _ensure_batch_output_target(negatives, args.output, "Scanned image")
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for negative_path in negatives:
        try:
            scanned, scan_source, source_path = _scan_from_file(negative_path, config)
            output_path = _output_path_for(negative_path, args.output, config.output.format)
            sidecar = (
                final_positive_sidecar(
                        negative_path=negative_path,
                        scan_source=scan_source,
                        scan_source_path=source_path,
                        output_path=output_path,
                        config=config,
                        scanned=scanned,
                    )
                if config.save_sidecar
                else None
            )
            save_image_bundle(
                scanned.output_srgb,
                output_path,
                config.output,
                sidecar,
                protected_paths=(negative_path, source_path),
            )
            print(f"[scan:{scan_source}] {negative_path} -> {output_path}")
            completed += 1
        except Exception as exc:
            failures.append((negative_path, exc))
            print(f"[scan:error] {negative_path}: {exc}", file=sys.stderr)
    _raise_batch_failures("scan", completed, failures)
    return completed


def _raise_batch_failures(
    operation: str,
    completed: int,
    failures: list[tuple[Path, Exception]],
) -> None:
    """Report batch failures after every independent item has been attempted."""
    if not failures:
        return
    first_path, first_error = failures[0]
    raise RuntimeError(
        f"{operation} batch completed {completed} item(s) with {len(failures)} failure(s); "
        f"first failure: {first_path}: {first_error}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] not in {"full", "develop", "scan", "-h", "--help"}:
        argv = ["full", *argv]
    args = parser.parse_args(argv)
    config = _apply_common_config(args)

    try:
        if args.command == "full":
            count = _run_full(args, config)
        elif args.command == "develop":
            count = _run_develop(args, config)
        elif args.command == "scan":
            count = _run_scan(args, config)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Done. Processed {count} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
