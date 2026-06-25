"""Command-line entry point for Film Foundry.

This CLI is the terminal-oriented sibling of run_darkroom.py.  It supports the
same three-stage workflow without asking users to edit Python variables:

    full      image -> developed negative -> scanned positive
    develop   image -> .npz density master + electronic negative materials
    scan      .npz/.scanner_raw.tiff -> scanned positive
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sys

import numpy as np

from half_frame_darkroom.core.engine import (
    develop_negative,
    process_file,
    scan_negative,
    scan_scanner_raw,
    seed_from_path,
)
from half_frame_darkroom.core.electronic_negative import (
    export_layer_pack,
    export_plate_set,
    export_transparent_plate_set,
    load_linear_rgb_tiff,
    save_linear_rgb_tiff,
    scanner_raw_with_clear_border,
    split_scanner_raw_border,
)
from half_frame_darkroom.core.io_utils import SUPPORTED_EXTENSIONS, iter_images, load_image, save_image
from half_frame_darkroom.core.negative_io import load_developed_negative_npz
from half_frame_darkroom.core.preview import negative_visual_preview, resize_to_long_edge
from half_frame_darkroom.core.states import DevelopedNegative
from half_frame_darkroom.model.config import DarkroomConfig, merge_config_presets


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PRESET_DIR = PROJECT_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
DEVELOP_PRESET_DIR = PRESET_DIR / "develop"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
USER_PRESET_DIR = PROJECT_ROOT / "user_presets"
USER_FILM_PRESET_DIR = USER_PRESET_DIR / "film"
USER_DEVELOP_PRESET_DIR = USER_PRESET_DIR / "develop"
USER_SCANNER_PRESET_DIR = USER_PRESET_DIR / "scanner"
NEGATIVE_SUFFIX = ".darkroom_negative.npz"


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
    stem = path.stem
    for suffix in (".darkroom_negative", ".scanner_raw"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def _output_path_for(input_path: Path, output_root: Path, output_format: str) -> Path:
    suffix = "." + output_format.lower().lstrip(".")
    if output_root.suffix:
        return output_root
    return output_root / f"{_clean_stem(input_path)}_foundry{suffix}"


def _negative_path_for(input_path: Path, negative_root: Path) -> Path:
    if negative_root.suffix.lower() == ".npz":
        return negative_root
    return negative_root / f"{input_path.stem}{NEGATIVE_SUFFIX}"


def _ensure_batch_output_target(items: list[Path], output_root: Path, label: str) -> None:
    """批处理时禁止把多个结果写进同一个文件路径。"""
    if len(items) > 1 and output_root.suffix:
        raise ValueError(
            f"{label} output must be a folder when processing multiple files: {output_root}"
        )


def _scanner_raw_path_for_negative(negative_path: Path) -> Path:
    return negative_path.with_suffix(".scanner_raw.tiff")


def _is_scanner_raw_tiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"} and ".scanner_raw" in path.stem


def _iter_negative_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() == ".npz" or _is_scanner_raw_tiff(path) else []
    if path.is_dir():
        npz_paths = sorted(path.glob(f"*{NEGATIVE_SUFFIX}"))
        raw_paths = sorted(item for item in path.glob("*.scanner_raw.tif*") if _is_scanner_raw_tiff(item))
        npz_raw_paths = {_scanner_raw_path_for_negative(item).resolve() for item in npz_paths}
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


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _load_negative(path: Path) -> DevelopedNegative:
    return load_developed_negative_npz(path)


def _save_negative(
    negative: DevelopedNegative,
    path: Path,
    input_path: Path,
    config: DarkroomConfig,
) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        density_cmy=negative.density_cmy.astype(np.float32),
        density_grain=negative.density_grain.astype(np.float32),
    )

    paths: dict[str, str] = {"negative_path": str(path)}
    preview_path = path.with_suffix(".negative_visual.png")
    save_image(negative_visual_preview(negative.density_grain, config.film), preview_path, config.output)
    paths["negative_visual_preview"] = str(preview_path)

    scanner_raw_path: Path | None = None
    if config.output.save_scanner_raw:
        scanner_raw = scanner_raw_with_clear_border(
            negative.density_grain,
            config.film,
            config.scanner,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        scanner_raw_path = path.with_suffix(".scanner_raw.tiff")
        save_linear_rgb_tiff(scanner_raw, scanner_raw_path)
        paths["scanner_raw_path"] = str(scanner_raw_path)

    material_dir = path.with_suffix("")
    material_paths: dict[str, str] = {}
    if config.output.export_layer_pack:
        material_paths.update(
            export_layer_pack(
                negative,
                config.film,
                material_dir.parent / f"{material_dir.name}_layer_pack",
                source_negative_path=path,
                scanner_raw_path=scanner_raw_path,
                orange_preview_path=preview_path,
                metadata={"input_path": str(input_path), "negative_path": str(path), "config": asdict(config)},
            )
        )
    else:
        if config.output.export_transparent_plate:
            material_paths.update(
                export_transparent_plate_set(
                    negative.density_grain,
                    config.film,
                    material_dir.parent / f"{material_dir.name}_transparent_plate",
                )
            )
        if config.output.export_plate_set:
            material_paths.update(
                export_plate_set(
                    negative.density_cmy,
                    negative.density_grain,
                    negative.after_mtf,
                    negative.after_halation,
                    config.film,
                    material_dir.parent / f"{material_dir.name}_plate_set",
                )
            )
    paths.update({f"material:{key}": value for key, value in material_paths.items()})

    if config.save_sidecar:
        sidecar_path = path.with_suffix(path.suffix + ".json")
        _save_json(
            sidecar_path,
            {
                "kind": "DevelopedNegative",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "project": "Film Foundry / Electronic Negative Factory",
                "input_path": str(input_path),
                "resolved_seed": _resolved_seed_for(input_path, config),
                "paths": paths,
                "config": asdict(config),
            },
        )
        paths["sidecar"] = str(sidecar_path)
    return paths


def _scan_from_file(path: Path, config: DarkroomConfig):
    if _is_scanner_raw_tiff(path):
        scanner_raw = load_linear_rgb_tiff(path)
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        return scan_scanner_raw(inner, config, base_samples=border_samples, source_path=path), "scanner_raw_tiff", path

    scanner_raw_path = _scanner_raw_path_for_negative(path)
    if scanner_raw_path.exists():
        scanner_raw = load_linear_rgb_tiff(scanner_raw_path)
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        return (
            scan_scanner_raw(inner, config, base_samples=border_samples, source_path=scanner_raw_path),
            "scanner_raw_tiff",
            scanner_raw_path,
        )

    if path.suffix.lower() != ".npz":
        raise ValueError(f"Unsupported negative file: {path}. Use .npz or .scanner_raw.tiff, not sidecar .json.")
    negative = _load_negative(path)
    return scan_negative(negative, config), "density_npz", path


def _add_common_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preset", help="Full config example name or JSON path; overrides split preset loading.")
    parser.add_argument("--seed", type=int, help="Random seed.")
    parser.add_argument("--seed-strategy", choices=("random", "fixed", "path"), help="Random seed strategy.")
    parser.add_argument("--fast", action="store_true", help="Enable fast internal processing.")
    parser.add_argument("--quality-mode", choices=("draft", "standard", "high"), help="Internal processing quality preset.")
    parser.add_argument("--halation-work-long-edge", type=int, help="Work long edge for halation low-frequency spread; 0 disables resizing.")
    parser.add_argument("--grain-work-long-edge", type=int, help="Work long edge for density grain random field; 0 disables resizing.")
    parser.add_argument("--format", choices=("png", "jpg", "jpeg", "tif", "tiff", "webp"), help="Output format.")
    parser.add_argument("--bit-depth", type=int, choices=(8, 16), help="Output bit depth.")
    parser.add_argument("--quality", type=int, help="JPEG/WebP quality.")
    parser.add_argument("--render-long-edge", type=int, help="Resize final render longest edge.")
    parser.add_argument("--preview-long-edge", type=int, help="Resize preview longest edge.")
    parser.add_argument("--debug-output", action="store_true", help="Save intermediate debug outputs.")
    parser.add_argument("--comparison-grid", action="store_true", help="Save comparison grid with debug output.")
    parser.add_argument("--no-sidecar", action="store_true", help="Do not save sidecar JSON.")


def _add_develop_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--film-preset", help="Film material preset name or JSON path.")
    parser.add_argument("--develop-preset", help="Darkroom process preset name or JSON path.")
    parser.add_argument("--exposure-ev", type=float, help="Input exposure proxy EV before negative formation.")
    parser.add_argument("--negative-contrast", type=float, help="H-D gamma multiplier.")
    parser.add_argument("--dye-selectivity", type=float, help="Film dye absorption selectivity multiplier.")
    parser.add_argument("--halation", type=float, help="Halation multiplier.")
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
    parser.add_argument("--silver-retention", type=float, help="Retained silver / monobath silvering tendency in [0, 1].")
    parser.add_argument("--light-leak", type=float, help="Intentional light leak accident strength in [0, 1].")
    parser.add_argument("--chemical-stain", type=float, help="Dirty chemistry / kelp-like stain accident strength in [0, 1].")
    parser.add_argument("--uneven-development", type=float, help="Uneven development mottling accident strength in [0, 1].")
    parser.add_argument("--bw", action="store_true", help="Use black-and-white negative mode.")
    parser.add_argument("--no-mtf", action="store_true", help="Disable emulsion MTF.")
    parser.add_argument("--no-halation", action="store_true", help="Disable halation.")
    parser.add_argument("--no-grain", action="store_true", help="Disable grain.")


def _add_scan_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scanner-preset", help="Scanner/render preset name or JSON path.")
    parser.add_argument("--print-contrast", type=float, help="Scan/render contrast multiplier.")
    parser.add_argument("--print-exposure-ev", type=float, help="Scan/render exposure EV.")
    parser.add_argument("--saturation", type=float, help="Scan/output saturation multiplier.")
    parser.add_argument("--no-subtractive", action="store_true", help="Disable subtractive scan/render.")
    parser.add_argument("--no-scan-normalize", action="store_true", help="Disable scan black/white normalization.")
    parser.add_argument("--scan-normalize-strength", type=float, help="Scan normalization blend strength.")


def _apply_common_config(args: argparse.Namespace) -> DarkroomConfig:
    preset = _preset_path(getattr(args, "preset", None))
    if preset is not None:
        config = DarkroomConfig.from_json(preset)
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
    if getattr(args, "fast", False):
        config.fast_mode = True
    if getattr(args, "quality_mode", None) is not None:
        config.processing.quality_mode = args.quality_mode
    if getattr(args, "halation_work_long_edge", None) is not None:
        value = int(args.halation_work_long_edge)
        config.processing.halation_work_long_edge = None if value <= 0 else value
    if getattr(args, "grain_work_long_edge", None) is not None:
        value = int(args.grain_work_long_edge)
        config.processing.grain_work_long_edge = None if value <= 0 else value
    if getattr(args, "format", None) is not None:
        config.output.format = args.format
    if getattr(args, "bit_depth", None) is not None:
        config.output.bit_depth = args.bit_depth
    if getattr(args, "quality", None) is not None:
        config.output.quality = args.quality
    if getattr(args, "render_long_edge", None) is not None:
        config.output.render_long_edge = args.render_long_edge
    if getattr(args, "preview_long_edge", None) is not None:
        config.output.preview_long_edge = args.preview_long_edge
    if getattr(args, "exposure_ev", None) is not None:
        config.look.exposure_ev = args.exposure_ev
    if getattr(args, "negative_contrast", None) is not None:
        config.look.negative_contrast = args.negative_contrast
    if getattr(args, "print_contrast", None) is not None:
        config.look.print_contrast = args.print_contrast
    if getattr(args, "print_exposure_ev", None) is not None:
        config.look.print_exposure_ev = args.print_exposure_ev
    if getattr(args, "saturation", None) is not None:
        config.scanner.scan_saturation = args.saturation
    if getattr(args, "dye_selectivity", None) is not None:
        config.look.saturation_multiplier = args.dye_selectivity
    if getattr(args, "halation", None) is not None:
        config.look.halation_multiplier = args.halation
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
    if getattr(args, "light_leak", None) is not None:
        config.chemistry.light_leak_strength = args.light_leak
    if getattr(args, "chemical_stain", None) is not None:
        config.chemistry.chemical_stain = args.chemical_stain
    if getattr(args, "uneven_development", None) is not None:
        config.chemistry.uneven_development = args.uneven_development
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
    develop.add_argument("--no-plate-set", action="store_true", help="Disable CMY/grain/halation plate export.")
    _add_common_config_args(develop)
    _add_develop_config_args(develop)

    scan = subparsers.add_parser("scan", help="Scan .npz or .scanner_raw.tiff negative(s) to final positive.")
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
    for input_path in inputs:
        output_path = _output_path_for(input_path, args.output, config.output.format)
        process_file(input_path, output_path, config, preview=bool(args.preview))
        print(f"[full] {input_path} -> {output_path}")
    return len(inputs)


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
    if args.no_plate_set:
        config.output.export_plate_set = False

    inputs = iter_images(args.input)
    if not inputs:
        raise FileNotFoundError(f"No supported images found in {args.input}")
    _ensure_batch_output_target(inputs, args.negative_output, "Developed negative")
    for input_path in inputs:
        image = load_image(input_path)
        long_edge = config.output.preview_long_edge if args.preview else config.output.render_long_edge
        image = resize_to_long_edge(image, long_edge)
        negative = develop_negative(image, config, rng=_rng_for_develop(input_path, config))
        negative_path = _negative_path_for(input_path, args.negative_output)
        paths = _save_negative(negative, negative_path, input_path, config)
        print(f"[develop] {input_path} -> {negative_path}")
        if "scanner_raw_path" in paths:
            print(f"          scanner raw: {paths['scanner_raw_path']}")
    return len(inputs)


def _run_scan(args: argparse.Namespace, config: DarkroomConfig) -> int:
    if args.scanner_raw_border is not None:
        config.output.scanner_raw_border_percent = args.scanner_raw_border
    if args.scanner_raw_border_min_px is not None:
        config.output.scanner_raw_border_min_px = args.scanner_raw_border_min_px

    negatives = _iter_negative_files(args.negative)
    if not negatives:
        raise FileNotFoundError(f"No .npz or .scanner_raw.tiff negatives found in {args.negative}")
    _ensure_batch_output_target(negatives, args.output, "Scanned image")
    for negative_path in negatives:
        scanned, scan_source, source_path = _scan_from_file(negative_path, config)
        output_path = _output_path_for(negative_path, args.output, config.output.format)
        save_image(scanned.output_srgb, output_path, config.output)
        if config.save_sidecar:
            _save_json(
                output_path.with_suffix(output_path.suffix + ".json"),
                {
                    "kind": "ScannedPositive",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "project": "Film Foundry / Electronic Negative Factory",
                    "negative_path": str(negative_path),
                    "scan_source": scan_source,
                    "scan_source_path": str(source_path),
                    "output_path": str(output_path),
                    "config": asdict(config),
                },
            )
        print(f"[scan:{scan_source}] {negative_path} -> {output_path}")
    return len(negatives)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] not in {"full", "develop", "scan", "-h", "--help"}:
        argv = ["full", *argv]
    args = parser.parse_args(argv)
    config = _apply_common_config(args)

    if args.command == "full":
        count = _run_full(args, config)
    elif args.command == "develop":
        count = _run_develop(args, config)
    elif args.command == "scan":
        count = _run_scan(args, config)
    else:
        parser.error(f"Unknown command: {args.command}")
        return 2

    print(f"Done. Processed {count} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
