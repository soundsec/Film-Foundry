"""Command-line entry point for Film Foundry.

This CLI is the terminal-oriented sibling of film_foundry/tools/run_darkroom.py.  It supports the
same three-stage workflow without asking users to edit Python variables:

    full      image -> developed negative/reversal-positive -> observed image
    develop   image -> .npz density master + transmissive medium materials
    expose    image -> undeveloped .darkroom_latent.npz
    develop_latent  latent archive -> developed .npz medium
    scan      .npz/scanner raw/light-table raw -> observed image
    share-config  encode or decode a portable recipe-parameter share code
"""

from __future__ import annotations

import argparse
from pathlib import Path
import json
import signal
import sys

from film_foundry.identity import APP_DISPLAY_NAME
from film_foundry.core.engine import (
    configure_scan_interpretation,
)
from film_foundry.core.execution import (
    processing_long_edge,
    resolve_execution_axes,
)
from film_foundry.core.atomic_io import strict_json_load
from film_foundry.core.io_utils import (
    assert_unique_output_stems,
    iter_images,
    output_target_is_file,
    probe_image_dimensions,
    save_image_bundle,
    scan_output_stem,
)
from film_foundry.core.resource_planning import (
    enforce_memory_budget,
    estimate_pipeline_memory,
    warn_outside_comfort_zone,
)
from film_foundry.core.sidecar import (
    final_positive_sidecar,
)
from film_foundry.contracts.config_share import decode_config_share
from film_foundry.contracts.data_kernel import CONFIG_SHARE_PREFIX
from film_foundry.model.config import (
    DarkroomConfig,
    merge_config_presets,
    normalize_scanner_color_curve_rgb,
    normalize_scanner_tone_curve,
)
from film_foundry.model.session import config_from_session
from film_foundry.app import workflow as _workflow
from film_foundry.app.api import WorkflowError, WorkflowRequest, run_request
from film_foundry.app.control import CancelFlag, WorkflowCancelled, WorkflowControl
from film_foundry.app.resume import (
    complete_developed_archive,
    complete_image_file,
    complete_latent_archive,
    find_complete_latent_development,
)
from film_foundry.core.frame_identity import normalize_frame_identity
from film_foundry.core.accidents import (
    LIGHT_LEAK_CONFIG_TEMPLATE_KEYS,
    LIGHT_LEAK_MIX_KEYS,
    LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS,
)


PRESET_DIR = _workflow.PRESET_DIR
FILM_PRESET_DIR = _workflow.FILM_PRESET_DIR
DEVELOP_PRESET_DIR = _workflow.DEVELOP_PRESET_DIR
SCANNER_PRESET_DIR = _workflow.SCANNER_PRESET_DIR
USER_PRESET_DIR = _workflow.USER_PRESET_DIR
USER_FILM_PRESET_DIR = _workflow.USER_FILM_PRESET_DIR
USER_DEVELOP_PRESET_DIR = _workflow.USER_DEVELOP_PRESET_DIR
USER_SCANNER_PRESET_DIR = _workflow.USER_SCANNER_PRESET_DIR
NEGATIVE_SUFFIX = _workflow.NEGATIVE_SUFFIX
POSITIVE_SUFFIX = _workflow.POSITIVE_SUFFIX
LATENT_SUFFIX = _workflow.LATENT_SUFFIX
_developed_path_for = _workflow.developed_path_for
_planned_developed_path = _workflow.planned_developed_path
_latent_path_for = _workflow.latent_path_for
_scanner_raw_path_for_negative = _workflow.scanner_raw_path_for_negative
_raw_path_for_medium = _workflow.transmission_raw_path_for_medium
_is_scanner_raw_tiff = _workflow.is_scanner_raw_tiff
_iter_negative_files = _workflow.iter_developed_medium_files
_iter_latent_files = _workflow.iter_latent_files
_rng_and_resolved_seed_for_develop = _workflow.rng_and_resolved_seed_for_develop
_load_negative = _workflow.load_developed_medium
_process_image_file = _workflow.process_image_file
_run_request = run_request


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
    if kind in {"film", "scanner"}:
        from film_foundry.app.seasonal_collection import preset_path
        optional = preset_path(kind, value)
        if optional is not None:
            return optional
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


def _ensure_batch_output_target(items: list[Path], output_root: Path, label: str) -> None:
    """批处理时禁止把多个结果写进同一个文件路径。"""
    if len(items) > 1 and output_target_is_file(output_root):
        raise ValueError(
            f"{label} output must be a folder when processing multiple files: {output_root}"
        )
    if len(items) > 1:
        assert_unique_output_stems(items, label)


def _add_frame_identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--roll-id",
        default=None,
        help="Optional roll identity. Required whenever a frame identity is given.",
    )
    parser.add_argument(
        "--frame-id",
        default=None,
        help="Optional frame identity string. Illegal when the input is a folder of multiple files.",
    )
    parser.add_argument(
        "--frame-index",
        type=int,
        default=None,
        help="Optional 1-based frame index. For a folder, this is the starting index (default 1).",
    )


def _cli_has_frame_identity_request(args: argparse.Namespace) -> bool:
    roll_id = str(getattr(args, "roll_id", None) or "").strip()
    frame_id = str(getattr(args, "frame_id", None) or "").strip()
    frame_index = getattr(args, "frame_index", None)
    return bool(roll_id or frame_id or frame_index is not None)


def _validate_cli_frame_identity(args: argparse.Namespace, item_count: int) -> None:
    if not _cli_has_frame_identity_request(args):
        return
    if not str(getattr(args, "roll_id", None) or "").strip():
        raise ValueError("frame_identity 必须包含 roll_id：请提供 --roll-id。")
    if str(getattr(args, "frame_id", None) or "").strip() and item_count > 1:
        raise ValueError(
            "批量输入不能共用 --frame-id；请省略它，并用 --frame-index 作为起始序号。"
        )
    start = getattr(args, "frame_index", None)
    if start is not None and int(start) < 1:
        raise ValueError("frame_identity.frame_index 必须是从 1 起的正整数。")


def _frame_identity_for_cli_item(
    args: argparse.Namespace,
    *,
    item_index: int,
    item_count: int,
) -> dict[str, str | int] | None:
    """Build one request identity. Omits the object when the user gave no flags."""
    if not _cli_has_frame_identity_request(args):
        return None
    roll_id = str(getattr(args, "roll_id", None) or "").strip()
    frame_id = str(getattr(args, "frame_id", None) or "").strip()
    start = getattr(args, "frame_index", None)
    identity: dict[str, str | int] = {"roll_id": roll_id}
    if item_count > 1:
        frame_index = (1 if start is None else int(start)) + int(item_index)
        identity["frame_index"] = frame_index
        identity["frame_id"] = f"{roll_id}:{frame_index}"
        return normalize_frame_identity(identity)
    if start is not None:
        identity["frame_index"] = int(start)
    if frame_id:
        identity["frame_id"] = frame_id
    elif "frame_index" in identity:
        identity["frame_id"] = f"{roll_id}:{identity['frame_index']}"
    else:
        identity["frame_index"] = 1
        identity["frame_id"] = f"{roll_id}:1"
    return normalize_frame_identity(identity)


def _cli_cancel_flag(args: argparse.Namespace) -> CancelFlag:
    flag = getattr(args, "cancel_flag", None)
    if not isinstance(flag, CancelFlag):
        flag = CancelFlag()
        setattr(args, "cancel_flag", flag)
    return flag


def _cli_control(args: argparse.Namespace) -> tuple[CancelFlag, WorkflowControl]:
    flag = _cli_cancel_flag(args)

    def on_progress(progress) -> None:
        print(f"[{progress.command}:{progress.stage}]", file=sys.stderr)

    return flag, WorkflowControl(
        cancel_requested=flag.requested,
        on_progress=on_progress if bool(getattr(args, "progress", False)) else None,
    )


def _is_cancelled(exc: BaseException) -> bool:
    if isinstance(exc, WorkflowCancelled):
        return True
    return isinstance(exc, WorkflowError) and exc.code == "cancelled"


def _skip_remaining_after_cancel(
    flag: CancelFlag,
    *,
    started: bool,
    remaining: int,
    operation: str,
) -> bool:
    if not started or remaining <= 0 or not flag.requested():
        return False
    print(
        f"[{operation}:cancelled] {remaining} remaining item(s) not started.",
        file=sys.stderr,
    )
    return True


def _note_cancelled_remainder(operation: str, remaining: int) -> None:
    if remaining <= 0:
        return
    print(
        f"[{operation}:cancelled] {remaining} remaining item(s) not started.",
        file=sys.stderr,
    )


def _skip_complete_output(
    args: argparse.Namespace,
    *,
    operation: str,
    output_path: Path,
    kind: str,
) -> bool:
    if not bool(getattr(args, "resume", False)):
        return False
    if kind == "latent":
        complete = complete_latent_archive(output_path)
    elif kind == "developed":
        complete = complete_developed_archive(output_path)
    elif kind == "image":
        complete = complete_image_file(output_path)
    else:
        raise ValueError(f"Unsupported resume artifact kind: {kind!r}")
    if not complete:
        return False
    print(f"[{operation}:resume] skip complete {output_path}")
    return True


def _install_sigint_cancel(flag: CancelFlag):
    previous = signal.getsignal(signal.SIGINT)
    presses = {"count": 0}

    def handler(signum, frame):
        presses["count"] += 1
        flag.cancel()
        if presses["count"] == 1:
            print(
                "[cancel] requested; stopping at the next checkpoint.",
                file=sys.stderr,
            )
            return
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        return lambda: None

    def restore() -> None:
        try:
            signal.signal(signal.SIGINT, previous)
        except (ValueError, OSError):
            pass

    return restore


def _scan_from_file(
    path: Path,
    config: DarkroomConfig,
    *,
    control: WorkflowControl | None = None,
):
    result = _run_request(
        WorkflowRequest(
            command="scan",
            input_path=path,
            output_path=path,
            config=config,
            control=control,
        )
    )
    return result.extra["scanned"], result.source_kind, result.source_path


def _add_common_config_args(
    parser: argparse.ArgumentParser,
    *,
    include_share: bool = True,
) -> None:
    parser.add_argument("--session", type=Path, help="GUI-exported Film Foundry session JSON; CLI options override it.")
    parser.add_argument("--preset", help="Full config example name or JSON path; overrides split preset loading.")
    if include_share:
        parser.add_argument(
            "--config-share",
            help=(
                "Portable FFC2 share code carrying recipe parameter values. "
                "Share the JSON file instead when the other side already has it."
            ),
        )
    parser.add_argument("--seed", type=int, help="Random seed.")
    parser.add_argument("--seed-strategy", choices=("random", "fixed", "path"), help="Random seed strategy.")
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print coarse workflow stages to stderr. Uses the same control contract as cancel.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip items whose intended output already exists as a complete "
            "latent, developed medium, or output image. Incomplete files are retried."
        ),
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Enable reduced-order operators; may be combined with scaling and any quality tier.",
    )
    parser.add_argument(
        "--processing-mode",
        choices=("quality", "scaled_fast", "reduced_fast"),
        help="Compatibility shortcut for one execution axis; combine scaled_fast with --fast for scaled reduced-order processing.",
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
    parser.add_argument(
        "--resize-long-edge",
        "--preview-long-edge",
        dest="preview_long_edge",
        type=int,
        help="Target longest edge for scaled_fast; --preview-long-edge is the legacy spelling.",
    )
    parser.add_argument("--debug-output", action="store_true", help="Save intermediate debug outputs.")
    parser.add_argument("--comparison-grid", action="store_true", help="Save comparison grid with debug output.")
    parser.add_argument("--no-sidecar", action="store_true", help="Do not save sidecar JSON.")
    parser.add_argument(
        "--input-icc",
        choices=("apply", "ignore"),
        help="Embedded ICC policy: convert to working sRGB, or ignore profiles and assume sRGB.",
    )


def _add_develop_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--incident-seed",type=int,help="Shared roll/batch defect identity; keeps leak, gate dirt and bath defects stable while frame grain varies.")
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
    parser.add_argument(
        "--light-leak-template",
        choices=LIGHT_LEAK_CONFIG_TEMPLATE_KEYS,
        help="Primary pre-latent light-leak coverage template.",
    )
    parser.add_argument(
        "--light-leak-secondary-template",
        choices=LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS,
        help="Optional second light-leak template mixed with the primary.",
    )
    parser.add_argument(
        "--light-leak-mix",
        choices=LIGHT_LEAK_MIX_KEYS,
        help="How primary, secondary, and optional mask coverage are combined.",
    )
    parser.add_argument("--occlusion-dust", type=float, help="Pre-develop dust occlusion amount in [0, 1].")
    parser.add_argument("--occlusion-hair", type=float, help="Pre-develop hair occlusion amount in [0, 1].")
    parser.add_argument("--occlusion-insect", type=float, help="Pre-develop insect occlusion amount in [0, 1].")
    parser.add_argument("--occlusion-debris", type=float, help="Pre-develop debris occlusion amount in [0, 1].")
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
        choices=("auto", "negative", "positive", "direct"),
        help="Interpretation mode. Default auto follows NPZ/raw medium identity.",
    )
    parser.add_argument("--print-contrast", type=float, help="Scan/render contrast multiplier.")
    parser.add_argument("--print-exposure-ev", type=float, help="Scan/render exposure EV.")
    parser.add_argument("--saturation", type=float, help="Scan/output saturation multiplier.")
    parser.add_argument(
        "--print-color-shift",
        nargs=3,
        type=float,
        metavar=("R", "G", "B"),
        help="Log-domain scan RGB filter shift. This is the current simple color adjustment, not a full scanner LUT.",
    )
    parser.add_argument(
        "--print-color-bias",
        nargs=3,
        type=float,
        metavar=("R", "G", "B"),
        help="Linear scan RGB gain. Simple observation adjustment, not a full scanner LUT.",
    )
    parser.add_argument(
        "--scanner-tone-curve",
        type=Path,
        help="JSON brightness curve applied to final encoded output after normalization.",
    )
    parser.add_argument(
        "--scanner-color-curve",
        type=Path,
        help="JSON file of three piecewise RGB channel curves applied after print-like mapping.",
    )
    parser.add_argument(
        "--scanner-device-response",
        type=Path,
        help="JSON file of three piecewise RGB 1D curves applied to scanner_raw after the response matrix.",
    )
    parser.add_argument("--no-subtractive", action="store_true", help="Disable subtractive scan/render.")
    parser.add_argument("--no-scan-normalize", action="store_true", help="Disable scan black/white normalization.")
    parser.add_argument("--scan-normalize-strength", type=float, help="Scan normalization blend strength.")


def _apply_common_config(args: argparse.Namespace) -> DarkroomConfig:
    session_path = getattr(args, "session", None)
    preset = _preset_path(getattr(args, "preset", None))
    share_text = getattr(args, "config_share", None)
    film_value = getattr(args, "film_preset", None)
    develop_value = getattr(args, "develop_preset", None)
    scanner_value = getattr(args, "scanner_preset", None)
    if share_text and (preset is not None or session_path is not None):
        raise ValueError("--config-share cannot be combined with --preset or --session.")
    if share_text and (film_value or develop_value or scanner_value):
        raise ValueError(
            "--config-share already carries parameter values; "
            "do not combine it with preset files. Share the JSON file instead."
        )
    if preset is not None:
        config = DarkroomConfig.from_json(preset)
    elif session_path is not None:
        config = config_from_session(session_path)
    elif share_text:
        shared = _workflow.config_from_share_code(share_text)
        if str(getattr(args, "command", "full")) == "scan":
            config = merge_config_presets(None, shared, develop_config=None)
        else:
            config = shared
    else:
        command = str(getattr(args, "command", "full"))
        if film_value is None and command in {"full", "develop", "movie-expose"}:
            film_value = "clear_modern_negative"
        if develop_value is None and command in {"full", "develop", "movie-develop"}:
            develop_value = "standard_color_negative"
        if scanner_value is None and command in {"full", "scan", "movie-observe"}:
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
        config.fast_mode = True
        if requested_processing_mode is None:
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
    if getattr(args, "print_color_shift", None) is not None:
        config.scanner.print_color_shift = tuple(float(value) for value in args.print_color_shift)
    if getattr(args, "print_color_bias", None) is not None:
        config.scanner.print_color_bias = tuple(float(value) for value in args.print_color_bias)
    if getattr(args, "scanner_tone_curve", None) is not None:
        config.scanner.tone_curve = normalize_scanner_tone_curve(strict_json_load(args.scanner_tone_curve))
    if getattr(args, "scanner_color_curve", None) is not None:
        config.scanner.color_curve_rgb = normalize_scanner_color_curve_rgb(
            strict_json_load(args.scanner_color_curve)
        )
    if getattr(args, "scanner_device_response", None) is not None:
        config.scanner.device_response_rgb = normalize_scanner_color_curve_rgb(
            strict_json_load(args.scanner_device_response),
            field_name="scanner.device_response_rgb",
        )
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
    if getattr(args,"incident_seed",None) is not None:
        config.chemistry.incident_identity_seed=args.incident_seed
    if getattr(args, "light_leak_template", None) is not None:
        config.chemistry.light_leak_template = str(args.light_leak_template)
    if getattr(args, "light_leak_secondary_template", None) is not None:
        config.chemistry.light_leak_secondary_template = str(args.light_leak_secondary_template)
    if getattr(args, "light_leak_mix", None) is not None:
        config.chemistry.light_leak_mix = str(args.light_leak_mix)
    if getattr(args, "occlusion_dust", None) is not None:
        config.chemistry.exposure_occlusion_dust = args.occlusion_dust
    if getattr(args, "occlusion_hair", None) is not None:
        config.chemistry.exposure_occlusion_hair = args.occlusion_hair
    if getattr(args, "occlusion_insect", None) is not None:
        config.chemistry.exposure_occlusion_insect = args.occlusion_insect
    if getattr(args, "occlusion_debris", None) is not None:
        config.chemistry.exposure_occlusion_debris = args.occlusion_debris
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
    if getattr(args, "input_icc", None) is not None:
        config.input_icc_policy = str(args.input_icc)
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="film-foundry",
        description=f"{APP_DISPLAY_NAME} command-line workflow.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name,help_text in (
        ('movie-expose','Import video into a timestamped latent material master.'),
        ('movie-develop','Develop a latent material master into a separate optical master.'),
        ('movie-observe','Observe one optical-master frame; never modifies material.'),
        ('movie-export','Export a complete material master ZIP for interchange.')):
        command = subparsers.add_parser(name,help=help_text)
        command.add_argument('input',type=Path)
        command.add_argument('output',type=Path)
        if name == 'movie-observe':
            command.add_argument('--frame',type=int,default=1,help='One-based material frame index.')
        _add_common_config_args(command)
        if name in {'movie-expose','movie-develop'}:
            _add_develop_config_args(command)
        elif name == 'movie-observe':
            _add_scan_config_args(command)

    full = subparsers.add_parser("full", help="Input image(s) -> final scanned positive.")
    full.add_argument("input", type=Path, help="Input image file or folder.")
    full.add_argument("output", type=Path, help="Output image file or folder.")
    full.add_argument("--preview", action="store_true", help="Legacy switch: use the scaled long-edge target for processing.")
    _add_common_config_args(full)
    _add_develop_config_args(full)
    _add_scan_config_args(full)

    develop = subparsers.add_parser("develop", help="Input image(s) -> reusable negative/material masters.")
    develop.add_argument("input", type=Path, help="Input image file or folder.")
    develop.add_argument("negative_output", type=Path, help="Output .npz file or folder for developed media.")
    develop.add_argument("--preview", action="store_true", help="Legacy switch: use the scaled long-edge target for developing.")
    develop.add_argument("--no-scanner-raw", action="store_true", help="Do not save scanner_raw.tiff.")
    develop.add_argument("--scanner-raw-border", type=float, help="Scanner raw clear-base border percent, e.g. 0.04.")
    develop.add_argument("--scanner-raw-border-min-px", type=int, help="Minimum scanner raw border width.")
    develop.add_argument("--layer-pack", action="store_true", help="Export full layer material pack.")
    develop.add_argument("--no-transparent-plate", action="store_true", help="Disable transparent negative export.")
    develop.add_argument("--plate-set", action="store_true", help="Export CMY/density/grain/halation plate layers.")
    develop.add_argument("--no-plate-set", action="store_true", help="Disable CMY/grain/halation plate export.")
    _add_common_config_args(develop)
    _add_develop_config_args(develop)
    _add_frame_identity_args(develop)

    expose = subparsers.add_parser(
        "expose",
        help="Input image(s) -> undeveloped .darkroom_latent.npz.",
    )
    expose.add_argument("input", type=Path, help="Input image file or folder.")
    expose.add_argument("latent_output", type=Path, help="Output .darkroom_latent.npz file or folder.")
    expose.add_argument("--preview", action="store_true", help="Legacy switch: use the scaled long-edge target for exposing.")
    _add_common_config_args(expose)
    _add_develop_config_args(expose)
    _add_frame_identity_args(expose)

    develop_latent = subparsers.add_parser(
        "develop_latent",
        help="Undeveloped latent archive(s) -> reusable negative/material masters.",
    )
    develop_latent.add_argument("input", type=Path, help="Input .darkroom_latent.npz file or folder.")
    develop_latent.add_argument("negative_output", type=Path, help="Output .npz file or folder for developed media.")
    develop_latent.add_argument("--preview", action="store_true", help="Legacy switch: use the scaled long-edge target for developing.")
    develop_latent.add_argument("--no-scanner-raw", action="store_true", help="Do not save scanner_raw.tiff.")
    develop_latent.add_argument("--scanner-raw-border", type=float, help="Scanner raw clear-base border percent, e.g. 0.04.")
    develop_latent.add_argument("--scanner-raw-border-min-px", type=int, help="Minimum scanner raw border width.")
    develop_latent.add_argument("--layer-pack", action="store_true", help="Export full layer material pack.")
    develop_latent.add_argument("--no-transparent-plate", action="store_true", help="Disable transparent negative export.")
    develop_latent.add_argument("--plate-set", action="store_true", help="Export CMY/density/grain/halation plate layers.")
    develop_latent.add_argument("--no-plate-set", action="store_true", help="Disable CMY/grain/halation plate export.")
    _add_common_config_args(develop_latent)
    _add_develop_config_args(develop_latent)
    _add_frame_identity_args(develop_latent)

    scan = subparsers.add_parser("scan", help="Observe developed .npz, negative scanner raw, or positive light-table raw.")
    scan.add_argument("negative", type=Path, help=".npz/.scanner_raw.tiff file or folder.")
    scan.add_argument("output", type=Path, help="Output image file or folder.")
    scan.add_argument("--scanner-raw-border", type=float, help="Scanner raw clear-base border percent.")
    scan.add_argument("--scanner-raw-border-min-px", type=int, help="Minimum scanner raw border width.")
    _add_common_config_args(scan)
    _add_scan_config_args(scan)

    share = subparsers.add_parser(
        "share-config",
        help="Encode or decode a portable recipe-parameter share code.",
    )
    share_sub = share.add_subparsers(dest="share_action", required=True)
    encode = share_sub.add_parser(
        "encode",
        help="Print an FFC2 share code carrying the current recipe parameter values.",
    )
    encode.add_argument(
        "json_file",
        nargs="?",
        type=Path,
        help="Custom config JSON to encode. If you already share this file, skip the code.",
    )
    _add_common_config_args(encode, include_share=False)
    _add_develop_config_args(encode)
    _add_scan_config_args(encode)
    decode = share_sub.add_parser(
        "decode",
        help="Print the recipe JSON carried by a share code.",
    )
    decode.add_argument("code", help="Share code starting with FFC2:.")
    return parser


def _run_share_config(args: argparse.Namespace) -> int:
    if args.share_action == "encode":
        json_file = getattr(args, "json_file", None)
        if json_file is not None:
            token = "".join(str(json_file).split()).lstrip("\ufeff")
            if token.startswith(CONFIG_SHARE_PREFIX) or token.startswith("FFC1:"):
                raise ValueError("that looks like a share code; use share-config decode")
            if getattr(args, "preset", None):
                raise ValueError("pass either a JSON file or --preset, not both")
            args.preset = str(json_file)
        args.command = "full"
        config = _apply_common_config(args)
        print(_workflow.encode_recipe_share(config))
        return 0
    if args.share_action == "decode":
        payload = decode_config_share(args.code)
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0
    raise ValueError(f"Unknown share-config action: {args.share_action}")


def _run_full(args: argparse.Namespace, config: DarkroomConfig) -> int:
    inputs = iter_images(args.input)
    if not inputs:
        raise FileNotFoundError(f"No supported images found in {args.input}")
    _ensure_batch_output_target(inputs, args.output, "Final image")
    flag, control = _cli_control(args)
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for item_index, input_path in enumerate(inputs):
        if _skip_remaining_after_cancel(
            flag,
            started=completed + len(failures) > 0,
            remaining=len(inputs) - item_index,
            operation="full",
        ):
            break
        output_path = _output_path_for(input_path, args.output, config.output.format)
        if _skip_complete_output(
            args,
            operation="full",
            output_path=output_path,
            kind="image",
        ):
            completed += 1
            continue
        try:
            _run_request(
                WorkflowRequest(
                    command="full",
                    input_path=input_path,
                    output_path=output_path,
                    config=config,
                    scaled_override=bool(args.preview),
                    control=control,
                )
            )
            print(f"[full] {input_path} -> {output_path}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[full:error] {input_path}: {exc}", file=sys.stderr)
            if _is_cancelled(exc):
                _note_cancelled_remainder("full", len(inputs) - item_index - 1)
                break
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
    _validate_cli_frame_identity(args, len(inputs))
    flag, control = _cli_control(args)
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for item_index, input_path in enumerate(inputs):
        if _skip_remaining_after_cancel(
            flag,
            started=completed + len(failures) > 0,
            remaining=len(inputs) - item_index,
            operation="develop",
        ):
            break
        planned = _planned_developed_path(
            input_path,
            args.negative_output,
            image_polarity=str(config.film.image_polarity),
        )
        if _skip_complete_output(
            args,
            operation="develop",
            output_path=planned,
            kind="developed",
        ):
            completed += 1
            continue
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
                    resolve_execution_axes(
                        config,
                        scaled_override=bool(args.preview),
                    ).scaling_enabled
                    and input_path.suffix.lower() in {".jpg", ".jpeg"}
                ),
            )
            warn_outside_comfort_zone(estimate)
            enforce_memory_budget(
                estimate,
                config.processing.memory_budget_mb,
                config.processing.memory_budget_policy,
            )
            result = _run_request(
                WorkflowRequest(
                    command="develop",
                    input_path=input_path,
                    output_path=args.negative_output,
                    config=config,
                    scaled_override=bool(args.preview),
                    control=control,
                    frame_identity=_frame_identity_for_cli_item(
                        args,
                        item_index=item_index,
                        item_count=len(inputs),
                    ),
                )
            )
            negative_path = result.output_path
            paths = result.extra.get("paths")
            if not isinstance(paths, dict):
                paths = {}
            print(f"[develop] {input_path} -> {negative_path}")
            if "scanner_raw_path" in paths:
                print(f"          scanner raw: {paths['scanner_raw_path']}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[develop:error] {input_path}: {exc}", file=sys.stderr)
            if _is_cancelled(exc):
                _note_cancelled_remainder("develop", len(inputs) - item_index - 1)
                break
    _raise_batch_failures("develop", completed, failures)
    return completed


def _run_expose(args: argparse.Namespace, config: DarkroomConfig) -> int:
    inputs = iter_images(args.input)
    if not inputs:
        raise FileNotFoundError(f"No supported images found in {args.input}")
    _ensure_batch_output_target(inputs, args.latent_output, "Exposed latent")
    _validate_cli_frame_identity(args, len(inputs))
    flag, control = _cli_control(args)
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for item_index, input_path in enumerate(inputs):
        if _skip_remaining_after_cancel(
            flag,
            started=completed + len(failures) > 0,
            remaining=len(inputs) - item_index,
            operation="expose",
        ):
            break
        planned = _latent_path_for(input_path, args.latent_output)
        if _skip_complete_output(
            args,
            operation="expose",
            output_path=planned,
            kind="latent",
        ):
            completed += 1
            continue
        try:
            result = _run_request(
                WorkflowRequest(
                    command="expose",
                    input_path=input_path,
                    output_path=args.latent_output,
                    config=config,
                    scaled_override=bool(args.preview),
                    control=control,
                    frame_identity=_frame_identity_for_cli_item(
                        args,
                        item_index=item_index,
                        item_count=len(inputs),
                    ),
                )
            )
            print(f"[expose] {input_path} -> {result.output_path}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[expose:error] {input_path}: {exc}", file=sys.stderr)
            if _is_cancelled(exc):
                _note_cancelled_remainder("expose", len(inputs) - item_index - 1)
                break
    _raise_batch_failures("expose", completed, failures)
    return completed


def _run_develop_latent(args: argparse.Namespace, config: DarkroomConfig) -> int:
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

    inputs = _iter_latent_files(args.input)
    if not inputs:
        raise FileNotFoundError(f"No latent archives found in {args.input}")
    _ensure_batch_output_target(inputs, args.negative_output, "Developed negative")
    _validate_cli_frame_identity(args, len(inputs))
    flag, control = _cli_control(args)
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for item_index, input_path in enumerate(inputs):
        if _skip_remaining_after_cancel(
            flag,
            started=completed + len(failures) > 0,
            remaining=len(inputs) - item_index,
            operation="develop_latent",
        ):
            break
        planned = _planned_developed_path(
            input_path,
            args.negative_output,
            image_polarity=str(config.film.image_polarity),
        )
        if bool(getattr(args, "resume", False)):
            complete = find_complete_latent_development(input_path, args.negative_output, planned)
            if complete is not None:
                print(f"[develop_latent:resume] skip complete {complete}")
                completed += 1
                continue
        try:
            result = _run_request(
                WorkflowRequest(
                    command="develop_latent",
                    input_path=input_path,
                    output_path=args.negative_output,
                    config=config,
                    scaled_override=bool(args.preview),
                    control=control,
                    frame_identity=_frame_identity_for_cli_item(
                        args,
                        item_index=item_index,
                        item_count=len(inputs),
                    ),
                )
            )
            negative_path = result.output_path
            paths = result.extra.get("paths")
            if not isinstance(paths, dict):
                paths = {}
            print(f"[develop_latent] {input_path} -> {negative_path}")
            if "scanner_raw_path" in paths:
                print(f"          scanner raw: {paths['scanner_raw_path']}")
            completed += 1
        except Exception as exc:
            failures.append((input_path, exc))
            print(f"[develop_latent:error] {input_path}: {exc}", file=sys.stderr)
            if _is_cancelled(exc):
                _note_cancelled_remainder("develop_latent", len(inputs) - item_index - 1)
                break
    _raise_batch_failures("develop_latent", completed, failures)
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
    flag, control = _cli_control(args)
    failures: list[tuple[Path, Exception]] = []
    completed = 0
    for item_index, negative_path in enumerate(negatives):
        if _skip_remaining_after_cancel(
            flag,
            started=completed + len(failures) > 0,
            remaining=len(negatives) - item_index,
            operation="scan",
        ):
            break
        output_path = _output_path_for(negative_path, args.output, config.output.format)
        if _skip_complete_output(
            args,
            operation="scan",
            output_path=output_path,
            kind="image",
        ):
            completed += 1
            continue
        try:
            scanned, scan_source, source_path = _scan_from_file(
                negative_path,
                config,
                control=control,
            )
            control.check_cancel()
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
            if _is_cancelled(exc):
                _note_cancelled_remainder("scan", len(negatives) - item_index - 1)
                break
    _raise_batch_failures("scan", completed, failures)
    return completed


def _raise_batch_failures(
    operation: str,
    completed: int,
    failures: list[tuple[Path, Exception]],
) -> None:
    """Raise after a batch if any item failed.

    Ordinary item failures continue later files. Cooperative cancel stops the
    loop so remaining files are not started. ``--resume`` may skip already
    complete outputs before a request starts.
    """
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
    if argv and argv[0] not in {
        "full",
        "develop",
        "expose",
        "develop_latent",
        "scan",
        "share-config",
        "movie-expose", "movie-develop", "movie-observe", "movie-export",
        "-h",
        "--help",
    }:
        argv = ["full", *argv]
    args = parser.parse_args(argv)
    if args.command == "share-config":
        try:
            return _run_share_config(args)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    config = _apply_common_config(args)
    if args.command != "scan" and not args.command.startswith('movie-'):
        from film_foundry.app.incident_batch import prepare_incident_config
        config=prepare_incident_config(config,roll_id=getattr(args,"roll_id",None),source=args.input)
    _cli_cancel_flag(args)
    restore_sigint = _install_sigint_cancel(args.cancel_flag)

    try:
        if args.command.startswith('movie-'):
            if args.resume:
                raise ValueError('Material masters currently commit as complete new directories; --resume is not supported.')
            from film_foundry.app.movie_master import import_video_master, develop_movie_master, observe_master_frame
            _, control = _cli_control(args)
            if args.command == 'movie-expose':
                written = import_video_master(args.input,args.output,config,control=control)
            elif args.command == 'movie-develop':
                written = develop_movie_master(args.input,args.output,config,control=control)
            elif args.command == 'movie-export':
                from film_foundry.app.movie_zip import export_master_zip
                written = export_master_zip(args.input,args.output,control=control)
            else:
                from film_foundry.core.io_utils import save_image
                control.check_cancel()
                scanned = observe_master_frame(args.input,args.frame,config)
                control.check_cancel()
                written = save_image(scanned.output_srgb,args.output,config.output)
            print(f'Material master: {written}')
            count = 1
        elif args.command == "full":
            count = _run_full(args, config)
        elif args.command == "develop":
            count = _run_develop(args, config)
        elif args.command == "expose":
            count = _run_expose(args, config)
        elif args.command == "develop_latent":
            count = _run_develop_latent(args, config)
        elif args.command == "scan":
            count = _run_scan(args, config)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
    except WorkflowCancelled:
        print('[cancel] material master not committed.',file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        print("[cancel] interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        restore_sigint()

    print(f"Done. Processed {count} item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
