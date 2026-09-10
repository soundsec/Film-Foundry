"""Headless main-console workflow helpers.

Path planning, preset catalogs, batch records, and medium-file observation live
here so CLI, the IDE script, and the Qt panel share one application layer.
The panel still maps widgets and owns thread dispatch; CLI/IDE keep their own
output naming and scan third-value contracts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter

import numpy as np
from PIL import Image

from film_foundry.core.accidents import LIGHT_LEAK_CONFIG_TEMPLATE_KEYS
from film_foundry.core.film_border import (
    FILM_BORDER_IMPRINT_SIDES,
    FILM_BORDER_IMPRINT_STYLES,
)
from film_foundry.core.engine import (
    apply_optical_observation_snapshot,
    configure_scan_interpretation,
    develop_negative,
    process_array,
    process_file,
    scan_medium_output,
    scan_medium_direct,
    scan_scanner_raw_direct,
    save_developed_medium_at_path,
    seed_from_path,
)
from film_foundry.core.electronic_negative import (
    load_linear_rgb_tiff,
    split_scanner_raw_border,
)
from film_foundry.core.execution import (
    processing_long_edge,
    resolve_execution_axes,
)
from film_foundry.core.io_utils import (
    SUPPORTED_EXTENSIONS,
    assert_unique_output_stems,
    iter_images,
    load_image,
    load_image_with_color,
    output_target_is_file,
    save_image_bundle,
    scan_output_stem,
)
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.core.preview import (
    negative_visual_preview,
    optical_density_visual_preview,
    resize_to_long_edge,
)
from film_foundry.core.sidecar import (
    final_positive_sidecar,
    load_scanner_raw_sidecar,
    scanner_raw_border_width_from_sidecar,
    scanner_raw_optical_observation_from_sidecar,
    transmission_raw_source_kind,
)
from film_foundry.core.states import DevelopedNegative
from film_foundry.model.config import DarkroomConfig, merge_config_presets
from film_foundry.model.session import (
    load_session,
    preset_reference,
    preset_value_from_reference,
    save_session,
    session_payload,
)
from film_foundry.ui.i18n import current_language, language_label, set_language, tr
from film_foundry.app.paths import app_root, resource_root

PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
APP_TITLE = "Film Foundry - Imaging Medium Workflow"
PRESET_DIR = RESOURCE_ROOT / "film_foundry" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
DEVELOP_PRESET_DIR = PRESET_DIR / "develop"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
USER_PRESET_DIR = PROJECT_ROOT / "user_presets"
USER_FILM_PRESET_DIR = USER_PRESET_DIR / "film"
USER_DEVELOP_PRESET_DIR = USER_PRESET_DIR / "develop"
USER_SCANNER_PRESET_DIR = USER_PRESET_DIR / "scanner"
INTERNAL_PRESET_PREFIXES = ("diagnostic_", "accident_", "experimental_")
MAIN_GUI_BUILTIN_DEVELOP_PRESETS = {
    "standard_color_negative",
    "standard_bw_negative",
    "standard_color_reversal",
    "standard_bw_reversal",
    # Deliberately exposed as an advanced, explicitly labelled experiment:
    # color-negative stock run through reversal processing, followed by a
    # separate mask/dye bleach. This is not a standard silver-bleach variant.
    "experimental_color_negative_clear_reversal",
}
DEFAULT_INPUT_DIR = PROJECT_ROOT / "input_images"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_NEGATIVE_DIR = DEFAULT_OUTPUT_DIR / "negatives"
PREVIEW_PROCESS_LONG_EDGE = 900
PREVIEW_DISPLAY_LONG_EDGE = 520
NEGATIVE_SUFFIX = ".darkroom_negative.npz"
POSITIVE_SUFFIX = ".darkroom_positive.npz"
SEED_STRATEGY_KEYS = ("random", "fixed", "path")


@dataclass(frozen=True)
class PreviewRequest:
    token: int
    mode: str
    config: DarkroomConfig
    source_path: Path


def ensure_user_dirs() -> None:
    DEFAULT_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_NEGATIVE_DIR.mkdir(parents=True, exist_ok=True)
    USER_FILM_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    USER_DEVELOP_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    USER_SCANNER_PRESET_DIR.mkdir(parents=True, exist_ok=True)


def output_path_for(
    input_path: Path,
    output_root: Path,
    output_format: str,
    *,
    run_suffix: str | None = None,
) -> Path:
    if output_target_is_file(output_root):
        return output_root
    suffix = "." + output_format.lower().lstrip(".")
    stem = f"{scan_output_stem(input_path)}_darkroom"
    if run_suffix:
        stem += f"_{run_suffix}"
    return output_root / f"{stem}{suffix}"


def run_timestamp(now: datetime | None = None) -> str:
    """Return a filesystem-safe local run timestamp."""
    return (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")


def create_run_directory(
    output_root: Path,
    mode: str,
    *,
    timestamp: str | None = None,
) -> tuple[Path, str]:
    """Atomically reserve a non-overwriting directory for one GUI run."""
    output_root = Path(output_root)
    if output_target_is_file(output_root):
        raise ValueError(f"运行输出位置必须是文件夹：{output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    base_id = f"{timestamp or run_timestamp()}_{mode}"
    for index in range(1, 10_000):
        run_id = base_id if index == 1 else f"{base_id}_{index:02d}"
        candidate = output_root / run_id
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate, run_id
    raise FileExistsError(f"无法创建唯一运行目录：{output_root / base_id}")


def default_scan_output_root(developed_source: Path) -> Path:
    """Place scan-only results one directory above the selected source folder."""
    source = Path(developed_source)
    if source.suffix.lower() in {".npz", ".tif", ".tiff"}:
        containing_folder = source.parent
        if containing_folder.name.lower() in {"negative", "negatives"}:
            return containing_folder.parent
        return containing_folder
    return source.parent


def unique_scan_run_id(
    output_root: Path,
    *,
    timestamp: str | None = None,
) -> str:
    """Choose a scan generation id without replacing a previous run manifest."""
    base_id = f"{timestamp or run_timestamp()}_scan"
    for index in range(1, 10_000):
        run_id = base_id if index == 1 else f"{base_id}_{index:02d}"
        if not (Path(output_root) / f"film_foundry_{run_id}.json").exists():
            return run_id
    raise FileExistsError(f"无法创建唯一扫描运行标识：{output_root / base_id}")


def batch_item_record(
    input_path: Path,
    output_path: Path | None,
    elapsed_seconds: float,
    error: BaseException | None = None,
) -> dict[str, object]:
    """Build one lightweight batch record without inspecting output files."""
    record: dict[str, object] = {
        "input": str(input_path),
        "output": str(output_path) if output_path is not None else None,
        "status": "failed" if error is not None else "completed",
        "elapsed_seconds": round(max(float(elapsed_seconds), 0.0), 6),
    }
    if error is not None:
        record["error"] = {
            "type": type(error).__name__,
            "message": str(error),
        }
    return record


def finalize_batch_run(
    payload: dict,
    *,
    operation: str,
    started_at: str,
    elapsed_seconds: float,
    items: list[dict[str, object]],
) -> None:
    """Attach one in-memory batch summary before the final manifest write."""
    completed = sum(item.get("status") == "completed" for item in items)
    failed = len(items) - completed
    status = "completed" if failed == 0 else ("failed" if completed == 0 else "completed_with_errors")
    finished_at = datetime.now().isoformat(timespec="seconds")
    payload["batch"] = {
        "version": 1,
        "operation": str(operation),
        "status": status,
        "started_at": str(started_at),
        "finished_at": finished_at,
        "elapsed_seconds": round(max(float(elapsed_seconds), 0.0), 6),
        "completed": int(completed),
        "failed": int(failed),
        "items": items,
        "recording_strategy": "memory_then_single_atomic_write",
    }
    run_record = payload.get("run")
    if isinstance(run_record, dict):
        run_record["status"] = status


def save_batch_run_record(
    path: Path,
    payload: dict,
    *,
    operation: str,
    started_at: str,
    started_counter: float,
    items: list[dict[str, object]],
) -> Exception | None:
    """Finalize and atomically save a batch record without failing image work."""
    finalize_batch_run(
        payload,
        operation=operation,
        started_at=started_at,
        elapsed_seconds=perf_counter() - float(started_counter),
        items=items,
    )
    try:
        save_session(path, payload)
    except Exception as exc:  # Recording must not invalidate completed outputs.
        return exc
    return None


def developed_path_for(
    input_path: Path,
    output_root: Path,
    medium: DevelopedNegative,
) -> Path:
    if output_root.suffix.lower() == ".npz":
        return output_root
    suffix = POSITIVE_SUFFIX if str(medium.image_polarity).lower() == "positive" else NEGATIVE_SUFFIX
    return output_root / f"{input_path.stem}{suffix}"


def scanner_raw_path_for_negative(negative_path: Path) -> Path:
    return negative_path.with_suffix(".scanner_raw.tiff")


def transmission_raw_path_for_medium(medium_path: Path) -> Path:
    if medium_path.name.lower().endswith(POSITIVE_SUFFIX):
        return medium_path.with_suffix(".light_table_raw.tiff")
    return scanner_raw_path_for_negative(medium_path)


def is_scanner_raw_tiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"} and any(
        token in path.stem.lower() for token in (".scanner_raw", ".light_table_raw")
    )


def iter_developed_medium_files(path: Path) -> list[Path]:
    if path.is_file() and (path.suffix.lower() == ".npz" or is_scanner_raw_tiff(path)):
        return [path]
    if path.is_dir():
        npz_paths = sorted(path.glob(f"*{NEGATIVE_SUFFIX}"))
        npz_paths += sorted(path.glob(f"*{POSITIVE_SUFFIX}"))
        raw_paths = sorted(item for item in path.glob("*.tif*") if is_scanner_raw_tiff(item))
        npz_raw_paths = {transmission_raw_path_for_medium(item).resolve() for item in npz_paths}
        return npz_paths + [item for item in raw_paths if item.resolve() not in npz_raw_paths]
    return []


# Backward-compatible name for sessions/tests created before positive masters.
iter_negative_files = iter_developed_medium_files


def scale_dye_selectivity(matrix, selectivity: float):
    """调节染料吸收矩阵的光谱选择性，而不是直接在 RGB 上拉饱和度。"""
    selectivity = max(0.0, float(selectivity))
    rows = []
    for row in matrix:
        neutral = sum(float(v) for v in row) / 3.0
        rows.append(tuple(max(0.0, neutral + (float(v) - neutral) * selectivity) for v in row))
    return tuple(rows)


def density_preview(density_cmy: np.ndarray, config: DarkroomConfig) -> np.ndarray:
    d_min = np.asarray(config.film.density_min, dtype=np.float32)
    d_max = np.asarray(config.film.density_max, dtype=np.float32)
    return np.clip((density_cmy - d_min) / np.maximum(d_max - d_min, 1e-6), 0.0, 1.0)


def gray_to_rgb(values: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    return np.repeat(values[..., None], 3, axis=-1)


def labeled_preview_tile(image: np.ndarray, label: str, size: int = 360) -> np.ndarray:
    image = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    if image.ndim == 2:
        image = gray_to_rgb(image)
    tile = resize_to_long_edge(image, size)
    canvas = np.full((size + 28, size, 3), 0.055, dtype=np.float32)
    height, width = tile.shape[:2]
    y = 28 + max(0, (size - height) // 2)
    x = max(0, (size - width) // 2)
    canvas[y : y + height, x : x + width] = tile

    from PIL import ImageDraw

    pil = Image.fromarray(np.round(canvas * 255.0).astype(np.uint8), mode="RGB")
    draw = ImageDraw.Draw(pil)
    draw.text((8, 7), label, fill=(235, 235, 228))
    return np.asarray(pil, dtype=np.float32) / 255.0


def preview_grid(items: list[tuple[str, np.ndarray]], tile_size: int = 360, columns: int = 2) -> np.ndarray:
    tiles = [labeled_preview_tile(image, label, tile_size) for label, image in items]
    if not tiles:
        return np.zeros((tile_size, tile_size, 3), dtype=np.float32)
    rows = []
    for start in range(0, len(tiles), columns):
        row_tiles = tiles[start : start + columns]
        while len(row_tiles) < columns:
            row_tiles.append(np.zeros_like(tiles[0]))
        rows.append(np.hstack(row_tiles))
    return np.vstack(rows).astype(np.float32)


def resolved_seed_for(path: Path, config: DarkroomConfig) -> int | None:
    strategy = str(config.seed_strategy).lower()
    if strategy == "fixed":
        return 0 if config.random_seed is None else int(config.random_seed)
    if strategy == "path":
        return seed_from_path(path, 0 if config.random_seed is None else int(config.random_seed))
    return None


def seed_strategy_labels() -> dict[str, str]:
    return {
        key: tr(f"seed.strategy.{key}")
        for key in SEED_STRATEGY_KEYS
    }


def seed_strategy_label(key: str) -> str:
    normalized = str(key).strip().lower()
    return seed_strategy_labels().get(normalized, seed_strategy_labels()["random"])


def seed_strategy_key_from_label(label: str) -> str:
    requested = str(label)
    for key, localized in seed_strategy_labels().items():
        if requested == localized:
            return key
    normalized = requested.strip().lower()
    return normalized if normalized in SEED_STRATEGY_KEYS else "random"


def light_leak_template_labels() -> dict[str, str]:
    return {
        key: tr(f"light_leak.template.{key}")
        for key in LIGHT_LEAK_CONFIG_TEMPLATE_KEYS
    }


def light_leak_template_label(key: str) -> str:
    normalized = str(key).strip().lower()
    labels = light_leak_template_labels()
    return labels.get(normalized, labels["auto"])


def light_leak_template_key_from_label(label: str) -> str:
    requested = str(label)
    for key, localized in light_leak_template_labels().items():
        if requested == localized:
            return key
    normalized = requested.strip().lower()
    return normalized if normalized in LIGHT_LEAK_CONFIG_TEMPLATE_KEYS else "auto"


def imprint_style_labels() -> dict[str, str]:
    return {key: tr(f"film_border.imprint.{key}") for key in FILM_BORDER_IMPRINT_STYLES}


def imprint_style_label(key: str) -> str:
    labels = imprint_style_labels()
    normalized = str(key).strip().lower()
    return labels.get(normalized, labels["none"])


def imprint_style_key_from_label(label: str) -> str:
    requested = str(label)
    for key, localized in imprint_style_labels().items():
        if requested == localized:
            return key
    normalized = requested.strip().lower()
    return normalized if normalized in FILM_BORDER_IMPRINT_STYLES else "none"


def imprint_side_labels() -> dict[str, str]:
    return {key: tr(f"film_border.side.{key}") for key in FILM_BORDER_IMPRINT_SIDES}


def imprint_side_label(key: str) -> str:
    labels = imprint_side_labels()
    normalized = str(key).strip().lower()
    return labels.get(normalized, labels["bottom"])


def imprint_side_key_from_label(label: str) -> str:
    requested = str(label)
    for key, localized in imprint_side_labels().items():
        if requested == localized:
            return key
    normalized = requested.strip().lower()
    return normalized if normalized in FILM_BORDER_IMPRINT_SIDES else "bottom"


def rng_and_resolved_seed_for_develop(
    path: Path,
    config: DarkroomConfig,
) -> tuple[np.random.Generator, int]:
    seed = resolved_seed_for(path, config)
    if seed is None:
        seed = int(np.random.SeedSequence().generate_state(1, dtype=np.uint32)[0])
    return np.random.default_rng(seed), seed


def rng_for_develop(path: Path, config: DarkroomConfig) -> np.random.Generator:
    rng, _ = rng_and_resolved_seed_for_develop(path, config)
    return rng


def prepare_develop_runtime_config(
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> tuple[DarkroomConfig, object]:
    """Freeze execution axes onto a private runtime copy for one develop job."""
    execution_axes = resolve_execution_axes(config, scaled_override=scaled_override)
    runtime_config = copy.deepcopy(config)
    runtime_config.processing.execution_mode = (
        "scaled_fast"
        if execution_axes.scaling_enabled
        else ("reduced_fast" if execution_axes.lower_order_enabled else "quality")
    )
    runtime_config.fast_mode = execution_axes.lower_order_enabled
    return runtime_config, execution_axes


def develop_image_file(
    input_path: Path,
    developed_root: Path,
    config: DarkroomConfig,
    *,
    scaled_override: bool = False,
) -> tuple[DevelopedNegative, Path, dict[str, str]]:
    """Develop one input image and write the polarity-aware medium bundle."""
    runtime_config, execution_axes = prepare_develop_runtime_config(
        config,
        scaled_override=scaled_override,
    )
    long_edge = processing_long_edge(config, scaled_override=scaled_override)
    if execution_axes.scaling_enabled:
        image, input_color = load_image_with_color(
            input_path,
            decode_long_edge=long_edge,
            input_icc_policy=runtime_config.input_icc_policy,
        )
    else:
        image, input_color = load_image_with_color(
            input_path,
            input_icc_policy=runtime_config.input_icc_policy,
        )
    image = resize_to_long_edge(image, long_edge)
    develop_rng, resolved_seed = rng_and_resolved_seed_for_develop(input_path, runtime_config)
    medium = develop_negative(image, runtime_config, rng=develop_rng)
    medium.metadata["input_color"] = input_color
    del image
    developed_path = developed_path_for(input_path, developed_root, medium)
    paths = save_developed_medium_at_path(
        input_path,
        developed_path,
        medium,
        runtime_config,
        resolved_seed=resolved_seed,
    )
    return medium, developed_path, paths


def is_internal_preset_name(name: str) -> bool:
    normalized = name.lower()
    return normalized.startswith(INTERNAL_PRESET_PREFIXES)


def public_builtin_preset_names(directory: Path) -> set[str]:
    return {
        path.stem
        for path in directory.glob("*.json")
        if not is_internal_preset_name(path.stem)
    }


def develop_preset_names() -> list[str]:
    # Keep the daily GUI concise. Specialized B&W recipes remain available in
    # the process editor and CLI, while user presets are always shown. The one
    # experimental clear-base reversal recipe is intentionally exposed because
    # it represents a distinct, user-selectable color-negative reversal path.
    names = {
        path.stem
        for path in DEVELOP_PRESET_DIR.glob("*.json")
        if path.stem in MAIN_GUI_BUILTIN_DEVELOP_PRESETS
    }
    names.update(path.stem for path in USER_DEVELOP_PRESET_DIR.glob("*.json"))
    return sorted(names)


def develop_preset_path(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    user_path = USER_DEVELOP_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return DEVELOP_PRESET_DIR / f"{name}.json"


def film_preset_names() -> list[str]:
    names = public_builtin_preset_names(FILM_PRESET_DIR)
    names.update(path.stem for path in USER_FILM_PRESET_DIR.glob("*.json"))
    return sorted(names)


def film_preset_path(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    user_path = USER_FILM_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return FILM_PRESET_DIR / f"{name}.json"


def material_polarity_label(polarity: str) -> str:
    return tr(f"material.{polarity}")


def material_color_label(color: str) -> str:
    return tr(f"material.{color}")


def localized_preset_name(kind: str, key: str, fallback: str) -> str:
    """Translate built-in preset names without replacing user-defined names."""
    path = Path(key)
    user_dirs = {"film": USER_FILM_PRESET_DIR, "develop": USER_DEVELOP_PRESET_DIR, "scanner": USER_SCANNER_PRESET_DIR}
    builtin_dirs = {"film": FILM_PRESET_DIR, "develop": DEVELOP_PRESET_DIR, "scanner": SCANNER_PRESET_DIR}
    is_builtin = not path.exists() and not (user_dirs[kind] / f"{key}.json").exists() and (builtin_dirs[kind] / f"{key}.json").exists()
    translation_key = f"preset.{kind}.{Path(key).stem}"
    translated = tr(translation_key)
    return translated if is_builtin and translated != translation_key else fallback


def material_polarity_category(config: DarkroomConfig) -> str:
    """Return the material's native result category used by the develop UI."""
    polarity = str(config.film.image_polarity).strip().lower()
    process = str(config.film.medium_process).strip().lower()
    return "positive" if polarity == "positive" or process in {"positive", "reversal", "slide"} else "negative"


def material_color_category(config: DarkroomConfig) -> str:
    color_process = str(config.film.color_process).strip().lower()
    return "monochrome" if color_process in {"bw", "black_white", "black-and-white", "monochrome"} else "color"


def film_preset_record(key: str) -> dict[str, str] | None:
    path = film_preset_path(key)
    if not path.exists():
        return None
    try:
        config = DarkroomConfig.from_json(path)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    polarity = material_polarity_category(config)
    color = material_color_category(config)
    name = str(config.film.name).strip() or Path(key).stem
    display_name = localized_preset_name("film", key, name)
    return {
        "key": key,
        "name": name,
        "polarity": polarity,
        "color": color,
        "label": f"{material_color_label(color)} · {material_polarity_label(polarity)} · {display_name}",
    }


def film_preset_catalog(polarity: str | None = None) -> list[dict[str, str]]:
    records = [record for key in film_preset_names() if (record := film_preset_record(key)) is not None]
    if polarity is not None:
        records = [record for record in records if record["polarity"] == polarity]
    return sorted(records, key=lambda record: (record["color"], record["name"].casefold(), record["key"].casefold()))


def develop_preset_record(key: str) -> dict[str, str] | None:
    path = develop_preset_path(key)
    if not path.exists():
        return None
    try:
        config = DarkroomConfig.from_json(path)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    fallback = str(config.chemistry.developer_name).strip() or Path(key).stem
    return {"key": key, "label": localized_preset_name("develop", key, fallback)}


def develop_preset_catalog() -> list[dict[str, str]]:
    return [record for key in develop_preset_names() if (record := develop_preset_record(key)) is not None]


def scanner_preset_polarity(config: DarkroomConfig) -> str:
    scanner = config.scanner
    return "positive" if str(scanner.interpreter_key).lower() == "positive_transparency_scan" or str(scanner.input_polarity).lower() == "positive" else "negative"


def scanner_preset_record(key: str) -> dict[str, str] | None:
    path = scanner_preset_path(key)
    if not path.exists():
        return None
    try:
        config = DarkroomConfig.from_json(path)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    fallback = Path(key).stem.replace("_", " ").title()
    return {
        "key": key,
        "polarity": scanner_preset_polarity(config),
        "label": localized_preset_name("scanner", key, fallback),
    }


def scanner_preset_catalog(polarity: str | None = None) -> list[dict[str, str]]:
    records = [record for key in scanner_preset_names() if (record := scanner_preset_record(key)) is not None]
    if polarity is not None:
        records = [record for record in records if record["polarity"] == polarity]
    return records


def scanner_preset_names() -> list[str]:
    names = public_builtin_preset_names(SCANNER_PRESET_DIR)
    names.update(path.stem for path in USER_SCANNER_PRESET_DIR.glob("*.json"))
    return sorted(names)


def scanner_capture_summary(metadata: dict) -> str:
    """Return a compact, localized scanner-raw headroom diagnostic."""
    saturation = float(metadata.get("scanner_saturation_fraction", 0.0)) * 100.0
    floor = float(metadata.get("scanner_floor_fraction", 0.0)) * 100.0
    parts = [
        f"{tr('scan.diagnostic.saturation')} {saturation:.1f}%",
        f"{tr('scan.diagnostic.floor')} {floor:.1f}%",
    ]
    base_channels = metadata.get("base_saturated_channels")
    if isinstance(base_channels, (list, tuple)) and any(bool(value) for value in base_channels):
        clipped = "".join(
            channel for channel, value in zip("RGB", base_channels) if bool(value)
        )
        parts.append(f"{tr('scan.diagnostic.base_clip')} {clipped}")
    return " · ".join(parts)


def scanner_preset_path(name: str) -> Path:
    path = Path(name)
    if path.exists():
        return path
    user_path = USER_SCANNER_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return SCANNER_PRESET_DIR / f"{name}.json"


def recommended_standard_develop_preset(config: DarkroomConfig) -> str:
    positive = material_polarity_category(config) == "positive"
    monochrome = material_color_category(config) == "monochrome"
    if positive:
        return "standard_bw_reversal" if monochrome else "standard_color_reversal"
    return "standard_bw_negative" if monochrome else "standard_color_negative"


def preset_source_text(value: str, user_dir: Path, builtin_dir: Path) -> str:
    path = Path(value)
    if path.exists():
        return "直接文件 / Direct"
    if (user_dir / f"{value}.json").exists():
        return "用户预设 / User"
    if (builtin_dir / f"{value}.json").exists():
        return "官方预设 / Built-in"
    return "未找到 / Missing"


def save_developed_medium(
    negative: DevelopedNegative,
    path: Path,
    input_path: Path,
    config: DarkroomConfig,
    save_sidecar: bool,
    *,
    resolved_seed: int | None = None,
) -> None:
    """Save through the shared polarity-aware medium exporter."""
    config.save_sidecar = bool(save_sidecar)
    save_developed_medium_at_path(
        input_path,
        path,
        negative,
        config,
        resolved_seed=(
            resolved_seed_for(input_path, config)
            if resolved_seed is None
            else int(resolved_seed)
        ),
    )


def load_developed_medium(path: Path) -> DevelopedNegative:
    return load_developed_medium_for_scan(path)


# Compatibility aliases for the older electronic-negative-only GUI API.
save_negative = save_developed_medium
load_negative = load_developed_medium


def _observe_transmission_raw(
    path: Path,
    config: DarkroomConfig,
    interpretation: str,
):
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
    return scanned, source_kind, path, scanner_raw


def observe_medium_file(path: Path, config: DarkroomConfig, *, retain_stages: bool = False):
    """Scan a developed npz or transmission raw.

    Returns ``(scanned, source_kind, source_path, preview)``. CLI uses
    ``source_path`` for sidecars and overwrite protection; the GUI inspector
    uses ``preview`` (raw pixels or a density visual).
    ``retain_stages=True`` requests Inspector stages for NPZ observations;
    the default keeps only the final output. Transmission raw retains its
    existing full-stage result contract in either mode.
    """
    config = copy.deepcopy(config)
    interpretation = str(config.scanner.interpretation_mode or "auto")
    if is_scanner_raw_tiff(path):
        return _observe_transmission_raw(path, config, interpretation)
    scanner_raw_path = transmission_raw_path_for_medium(path)
    if scanner_raw_path.exists():
        return _observe_transmission_raw(scanner_raw_path, config, interpretation)
    if path.suffix.lower() != ".npz":
        raise ValueError(
            f"不支持的底片文件：{path}。请选择 .npz 或 .scanner_raw.tiff，不要选择 sidecar .json。"
        )
    negative = load_developed_medium(path)
    scan = scan_medium_direct if retain_stages else scan_medium_output
    scanned = scan(negative, config, interpretation)
    runtime_config = scanned.metadata.get("runtime_config")
    preview_film = runtime_config.film if isinstance(runtime_config, DarkroomConfig) else config.film
    if negative.optical_density_rgb is not None:
        preview = optical_density_visual_preview(negative.optical_density_rgb)
    else:
        preview = negative_visual_preview(negative.density_grain, preview_film)
    return scanned, "density_npz", path, preview


def scan_from_file(path: Path, config: DarkroomConfig, *, retain_stages: bool = False):
    scanned, source_kind, _source_path, preview = observe_medium_file(
        path, config, retain_stages=retain_stages,
    )
    return scanned, source_kind, preview


