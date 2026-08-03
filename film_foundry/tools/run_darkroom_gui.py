"""Film Foundry / Electronic Negative Factory 简易调参面板。

直接在 IDE 里运行本文件即可打开窗口。

GUI 现在按阶段工作：
- 完整流程：显示冲洗参数 + 扫描参数
- 只冲洗介质：只显示 film/develop 参数，输出负片或正片 .npz 母版
- 只扫描介质：用户明确选择按负片或正片解释，不依赖文件记录的极性
"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from threading import Thread
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, ttk

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import numpy as np
from PIL import Image, ImageTk

from half_frame_darkroom.core.engine import (
    apply_optical_observation_snapshot,
    configure_scan_interpretation,
    develop_negative,
    process_array,
    process_file,
    scan_medium_output,
    scan_scanner_raw_direct,
    save_developed_medium_at_path,
    seed_from_path,
)
from half_frame_darkroom.core.electronic_negative import (
    halation_alpha,
    halation_alpha_linear,
    load_linear_rgb_tiff,
    split_scanner_raw_border,
)
from half_frame_darkroom.core.execution import processing_long_edge, resolve_execution_mode
from half_frame_darkroom.core.io_utils import SUPPORTED_EXTENSIONS, assert_unique_output_stems, iter_images, load_image, output_target_is_file, save_image_bundle, scan_output_stem
from half_frame_darkroom.core.negative_io import load_developed_medium_for_scan
from half_frame_darkroom.core.preview import (
    negative_visual_preview,
    optical_density_visual_preview,
    resize_to_long_edge,
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
from half_frame_darkroom.model.session import load_session, preset_reference, preset_value_from_reference, save_session, session_payload
from half_frame_darkroom.ui.i18n import current_language, language_from_label, language_label, language_options, set_language, tr
from half_frame_darkroom.ui.widgets import VerticalScrolledFrame
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
APP_TITLE = "Film Foundry - Imaging Medium Workflow"
PRESET_DIR = RESOURCE_ROOT / "half_frame_darkroom" / "presets"
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


class Tooltip:
    def __init__(self, widget: tk.Widget, key: str, delay_ms: int = 450, wraplength: int = 360) -> None:
        self.widget = widget
        self.key = key
        self.delay_ms = delay_ms
        self.wraplength = wraplength
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        text = tr(self.key)
        if not text or text == self.key or self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 18
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            tip,
            text=text,
            padding=(8, 5),
            relief="solid",
            borderwidth=1,
            wraplength=self.wraplength,
        )
        label.pack()
        self._tip = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def attach_tooltip(widget: tk.Widget, key: str | None) -> None:
    if key:
        Tooltip(widget, key)


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


def scan_from_file(path: Path, config: DarkroomConfig):
    # Sidecar optics are scoped to this observation and must not leak into the
    # GUI state or a later file in a directory scan.
    config = copy.deepcopy(config)
    interpretation = str(config.scanner.interpretation_mode or "auto")
    if is_scanner_raw_tiff(path):
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
        return scan_scanner_raw_direct(
            inner,
            config,
            interpretation,
            base_samples=border_samples,
            source_path=path,
            raw_source_kind=source_kind,
        ), source_kind, scanner_raw

    scanner_raw_path = transmission_raw_path_for_medium(path)
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
            scanner_raw,
        )

    if path.suffix.lower() != ".npz":
        raise ValueError(f"不支持的底片文件：{path}。请选择 .npz 或 .scanner_raw.tiff，不要选择 sidecar .json。")
    negative = load_developed_medium(path)
    scanned = scan_medium_output(negative, config, interpretation)
    runtime_config = scanned.metadata.get("runtime_config")
    preview_film = runtime_config.film if isinstance(runtime_config, DarkroomConfig) else config.film
    if negative.optical_density_rgb is not None:
        preview = optical_density_visual_preview(negative.optical_density_rgb)
    else:
        preview = negative_visual_preview(negative.density_grain, preview_film)
    return scanned, "density_npz", preview


class DarkroomPanel:
    def __init__(self) -> None:
        ensure_user_dirs()

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("940x760")
        self.root.minsize(760, 560)

        self.pipeline_mode = tk.StringVar(value="full")
        self.input_path = tk.StringVar(value=str(DEFAULT_INPUT_DIR))
        self.negative_path = tk.StringVar(value=str(DEFAULT_NEGATIVE_DIR))
        self.output_path = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.scan_output_near_source = tk.BooleanVar(value=True)
        self.film_preset = tk.StringVar(value="clear_modern_negative")
        self.material_polarity_display = tk.StringVar(value=material_polarity_label("negative"))
        self.film_preset_display = tk.StringVar(value="")
        self.develop_preset = tk.StringVar(value="standard_color_negative")
        self.develop_preset_display = tk.StringVar(value="")
        self.scanner_preset = tk.StringVar(value="neutral_scan")
        self.scanner_preset_display = tk.StringVar(value="")
        self.scan_interpretation = tk.StringVar(value="manual")
        self.scan_interpretation_display = tk.StringVar(value=tr("scan.mode.auto"))
        self.scan_remove_base = tk.BooleanVar(value=True)
        self.scan_invert = tk.BooleanVar(value=True)
        self.scan_recommendation = tk.StringVar(value="")
        self.film_preset_source = tk.StringVar(value="")
        self.develop_preset_source = tk.StringVar(value="")
        self.scanner_preset_source = tk.StringVar(value="")
        self.language = tk.StringVar(value=language_label(current_language()))
        self.expert_mode = tk.BooleanVar(value=False)

        self.run_as_preview = tk.BooleanVar(value=True)
        self.preview_long_edge = tk.IntVar(value=1600)
        self.fast_mode = tk.BooleanVar(value=True)
        self.quality_mode = tk.StringVar(value="standard")
        self.seed_strategy = tk.StringVar(value="random")
        self.seed_strategy_display = tk.StringVar(value=seed_strategy_label("random"))
        self.random_seed = tk.StringVar(value="0")

        self.exposure_ev = tk.DoubleVar(value=-0.35)
        self.negative_contrast = tk.DoubleVar(value=1.05)
        self.developer_type = tk.StringVar(value="standard")
        self.fixer_type = tk.StringVar(value="standard")
        self.frame_size = tk.StringVar(value="35mm")
        self.material_degradation = tk.DoubleVar(value=0.0)
        self.develop_time = tk.DoubleVar(value=8.0)
        self.develop_temperature = tk.DoubleVar(value=20.0)
        self.developer_concentration = tk.DoubleVar(value=1.0)
        self.agitation = tk.DoubleVar(value=1.0)
        self.exhaustion = tk.DoubleVar(value=0.0)
        self.fixer_exhaustion = tk.DoubleVar(value=0.0)
        self.silver_retention = tk.DoubleVar(value=0.0)
        self.silver_plating = tk.DoubleVar(value=0.0)
        self.compensation = tk.DoubleVar(value=0.0)
        self.light_leak = tk.DoubleVar(value=0.0)
        self.chemical_stain = tk.DoubleVar(value=0.0)
        self.uneven_development = tk.DoubleVar(value=0.0)
        self.development_adjacency_strength = tk.DoubleVar(value=0.0)
        self.development_adjacency_radius = tk.DoubleVar(value=0.0025)
        self.process_variation = tk.DoubleVar(value=0.0)
        self.halation = tk.DoubleVar(value=0.90)
        self.halation_sensitivity = tk.DoubleVar(value=0.0)
        self.grain = tk.DoubleVar(value=0.85)
        self.grain_size = tk.DoubleVar(value=1.00)
        self.emulsion_mtf = tk.DoubleVar(value=0.25)
        self.artifact_suppression = tk.DoubleVar(value=0.15)
        self.halation_edge = tk.DoubleVar(value=0.35)
        self.push = tk.DoubleVar(value=0.0)
        self.force_bw = tk.BooleanVar(value=False)
        self.enable_mtf = tk.BooleanVar(value=True)
        self.enable_halation = tk.BooleanVar(value=True)
        self.enable_grain = tk.BooleanVar(value=True)

        self.print_contrast = tk.DoubleVar(value=1.12)
        self.print_exposure = tk.DoubleVar(value=0.0)
        self.saturation = tk.DoubleVar(value=1.00)
        self.scan_normalize = tk.BooleanVar(value=True)
        self.scan_strength = tk.DoubleVar(value=0.45)
        self.anti_banding = tk.DoubleVar(value=0.18)
        self.print_shift_r = tk.DoubleVar(value=0.06)
        self.print_shift_g = tk.DoubleVar(value=0.00)
        self.print_shift_b = tk.DoubleVar(value=-0.08)
        self.highlight_green = tk.DoubleVar(value=1.04)
        self.highlight_blue = tk.DoubleVar(value=0.94)
        self.light_table_ev = tk.DoubleVar(value=0.35)
        self.light_table_temperature = tk.DoubleVar(value=5400.0)
        self.positive_color_control = tk.DoubleVar(value=0.25)
        self.projection_white_softness = tk.DoubleVar(value=0.22)
        self.projection_black_adaptation = tk.DoubleVar(value=0.10)
        self.negative_channel_compensation = tk.BooleanVar(value=False)
        self.negative_channel_compensation_strength = tk.DoubleVar(value=0.35)
        self.scan_pipeline_description = tk.StringVar(value="")

        self.debug_output = tk.BooleanVar(value=False)
        self.comparison_grid = tk.BooleanVar(value=False)
        self.save_sidecar = tk.BooleanVar(value=True)
        self.save_scanner_raw = tk.BooleanVar(value=True)
        self.scanner_raw_border = tk.DoubleVar(value=4.0)
        self.include_clear_base_border = tk.BooleanVar(value=False)
        self.export_layer_pack = tk.BooleanVar(value=False)
        self.export_transparent_plate = tk.BooleanVar(value=True)
        self.export_plate_set = tk.BooleanVar(value=False)
        self.output_format = tk.StringVar(value="jpg")

        self.status = tk.StringVar(value=tr("status.ready"))
        self.preview_window: tk.Toplevel | None = None
        self.preview_left_label: ttk.Label | None = None
        self.preview_right_label: ttk.Label | None = None
        self.preview_left_title = tk.StringVar(value=tr("preview.input"))
        self.preview_right_title = tk.StringVar(value=tr("preview.result"))
        self.preview_left_photo = None
        self.preview_right_photo = None
        self.slider_specs: list[dict] = []
        self._material_ui_syncing = False
        self._processing = False
        self.process_button: ttk.Button | None = None
        self._film_display_key_map: dict[str, str] = {}
        self._develop_display_key_map: dict[str, str] = {}
        self._scanner_display_key_map: dict[str, str] = {}

        self._build()
        self.film_preset.trace_add("write", lambda *_: self._sync_film_preset_controls())
        self.develop_preset.trace_add("write", lambda *_: self._sync_develop_preset_controls())
        self.scanner_preset.trace_add("write", lambda *_: self._sync_scanner_preset_controls())
        self.scan_interpretation.trace_add("write", lambda *_: self._sync_scan_interpretation_controls())
        self._sync_film_preset_controls()
        self._sync_develop_preset_controls()
        self._sync_scanner_preset_controls()
        self._update_mode_visibility()


    def _build(self) -> None:
        self.root.title(tr("app.title"))
        scroll_shell = ttk.Frame(self.root)
        scroll_shell.pack(fill="both", expand=True)
        self.main_scroller = VerticalScrolledFrame(scroll_shell, canvas_width=900)
        self.main_scroller.pack(fill="both", expand=True)
        outer = self.main_scroller.content
        outer.configure(padding=12)

        header = ttk.LabelFrame(outer, text=tr("section.stage"), padding=10)
        header.pack(fill="x", pady=(0, 8))
        modes = (
            (tr("mode.full"), "full"),
            (tr("mode.develop"), "develop"),
            (tr("mode.scan"), "scan"),
        )
        for text, value in modes:
            ttk.Radiobutton(
                header,
                text=text,
                value=value,
                variable=self.pipeline_mode,
                command=self._update_mode_visibility,
            ).pack(side="left", padx=(0, 18))

        self.paths_frame = ttk.LabelFrame(outer, text=tr("section.paths"), padding=10)
        self.paths_frame.pack(fill="x", pady=8)
        self._path_row(self.paths_frame, tr("label.input_image"), self.input_path, self._choose_input_file, self._choose_input_folder, 0)
        self._path_row(self.paths_frame, tr("label.negative_npz"), self.negative_path, self._choose_negative_file, self._choose_negative_folder, 1)
        self._path_row(self.paths_frame, tr("label.output"), self.output_path, None, self._choose_output_folder, 2)
        self.scan_output_follow_check = ttk.Checkbutton(
            self.paths_frame,
            text=tr("label.scan_output_near_source"),
            variable=self.scan_output_near_source,
        )
        self.scan_output_follow_check.grid(row=3, column=1, columnspan=3, sticky="w", pady=3)

        common = ttk.LabelFrame(outer, text=tr("section.common"), padding=10)
        common.pack(fill="x", pady=8)
        self.material_category_label = ttk.Label(common, text=tr("label.material_category"))
        self.material_polarity_combo = ttk.Combobox(
            common,
            textvariable=self.material_polarity_display,
            values=(material_polarity_label("negative"), material_polarity_label("positive")),
            state="readonly",
            width=16,
        )
        self.material_category_label.grid(row=0, column=0, sticky="w", pady=4)
        self.material_polarity_combo.grid(row=0, column=1, sticky="w", pady=4)
        self.film_editor_button = ttk.Button(common, text=tr("button.film_editor"), command=lambda: self._open_editor("film_foundry.tools.run_film_material_editor"))
        self.film_editor_button.grid(row=0, column=3, sticky="e", pady=4)

        self.film_preset_label = ttk.Label(common, text=tr("label.film_preset"))
        self.film_preset_combo = ttk.Combobox(
            common,
            textvariable=self.film_preset_display,
            values=(),
            state="readonly",
        )
        self.film_preset_label.grid(row=1, column=0, sticky="w", pady=4)
        self.film_preset_combo.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        self.material_polarity_combo.bind("<<ComboboxSelected>>", self._on_material_category_selected)
        self.film_preset_combo.bind("<<ComboboxSelected>>", self._on_film_preset_display_selected)
        self.film_preset_source_label = ttk.Label(common, textvariable=self.film_preset_source)

        self.develop_preset_label = ttk.Label(common, text=tr("label.develop_preset"))
        self.develop_preset_combo = ttk.Combobox(
            common,
            textvariable=self.develop_preset_display,
            values=(),
            state="readonly",
        )
        self.develop_preset_label.grid(row=2, column=0, sticky="w", pady=4)
        self.develop_preset_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        self.develop_preset_combo.bind("<<ComboboxSelected>>", self._on_develop_preset_display_selected)
        self.develop_preset_source_label = ttk.Label(common, textvariable=self.develop_preset_source)
        self.develop_editor_button = ttk.Button(common, text=tr("button.develop_editor"), command=lambda: self._open_editor("film_foundry.tools.run_develop_process_editor"))
        self.develop_editor_button.grid(row=2, column=3, sticky="e", padx=(8, 0), pady=4)

        self.scan_interpretation_label = ttk.Label(common, text=tr("label.scan_interpretation"))
        self.scan_interpretation_label.grid(row=3, column=0, sticky="w", pady=4)
        self.scan_interpretation_controls = ttk.Frame(
            common,
        )
        self.scan_interpretation_controls.grid(
            row=3,
            column=1,
            columnspan=2,
            sticky="ew",
            pady=4,
        )
        self.scan_remove_base_check = ttk.Checkbutton(
            self.scan_interpretation_controls,
            text=tr("label.scan_remove_base"),
            variable=self.scan_remove_base,
            command=self._sync_manual_scan_controls,
        )
        self.scan_remove_base_check.pack(side="left")
        self.scan_invert_check = ttk.Checkbutton(
            self.scan_interpretation_controls,
            text=tr("label.scan_invert"),
            variable=self.scan_invert,
            command=self._sync_manual_scan_controls,
        )
        self.scan_invert_check.pack(side="left", padx=(12, 0))
        ttk.Label(
            self.scan_interpretation_controls,
            textvariable=self.scan_recommendation,
            foreground="#555555",
        ).pack(side="left", padx=(14, 0))

        self.scanner_preset_label = ttk.Label(common, text=tr("label.scanner_preset"))
        self.scanner_preset_combo = ttk.Combobox(
            common,
            textvariable=self.scanner_preset_display,
            values=(),
            state="readonly",
        )
        self.scanner_preset_label.grid(row=4, column=0, sticky="w", pady=4)
        self.scanner_preset_combo.grid(row=4, column=1, columnspan=2, sticky="ew", pady=4)
        self.scanner_preset_combo.bind("<<ComboboxSelected>>", self._on_scanner_preset_display_selected)
        self.scanner_preset_source_label = ttk.Label(common, textvariable=self.scanner_preset_source)
        self.scanner_editor_button = ttk.Button(common, text=tr("button.scanner_editor"), command=self._open_scanner_editor)
        self.scanner_editor_button.grid(row=4, column=3, sticky="e", padx=(8, 0), pady=4)
        ttk.Button(common, text=tr("button.refresh_presets"), command=self._refresh_all_preset_combos).grid(row=3, column=3, sticky="e", padx=(8, 0), pady=4)

        ttk.Separator(common).grid(row=5, column=0, columnspan=4, sticky="ew", pady=(7, 5))
        ttk.Checkbutton(common, text=tr("label.preview_output"), variable=self.run_as_preview).grid(row=6, column=0, sticky="w", pady=4)
        self._slider(common, tr("label.preview_long_edge"), self.preview_long_edge, 0, 4000, 7, integer=True)
        ttk.Checkbutton(common, text=tr("label.fast_mode"), variable=self.fast_mode).grid(row=8, column=0, sticky="w", pady=4)
        ttk.Label(common, text=tr("label.quality")).grid(row=8, column=1, sticky="w", pady=4)
        ttk.Combobox(
            common,
            textvariable=self.quality_mode,
            values=("draft", "standard", "high"),
            width=10,
            state="readonly",
        ).grid(row=8, column=2, sticky="w", pady=4)
        ttk.Checkbutton(common, text=tr("label.sidecar"), variable=self.save_sidecar).grid(row=8, column=3, sticky="w", pady=4)
        ttk.Checkbutton(common, text=tr("label.debug"), variable=self.debug_output).grid(row=9, column=3, sticky="w", pady=4)
        ttk.Checkbutton(common, text=tr("label.grid"), variable=self.comparison_grid).grid(row=10, column=3, sticky="w", pady=4)
        ttk.Checkbutton(common, text=tr("label.expert"), variable=self.expert_mode, command=self._update_mode_visibility).grid(row=9, column=0, sticky="w", pady=4)
        ttk.Label(common, text=tr("label.output_format")).grid(row=9, column=1, sticky="w", pady=4)
        ttk.Combobox(common, textvariable=self.output_format, values=("jpg", "png", "tiff"), width=8, state="readonly").grid(
            row=9, column=2, sticky="w", pady=4
        )
        ttk.Button(common, text=tr("button.save_session"), command=self._save_session).grid(row=10, column=0, sticky="w", pady=4)
        ttk.Button(common, text=tr("button.load_session"), command=self._load_session).grid(row=10, column=1, sticky="w", pady=4)

        ttk.Label(common, text=tr("label.seed_strategy")).grid(row=11, column=0, sticky="w", pady=4)
        self.seed_strategy_display.set(seed_strategy_label(self.seed_strategy.get()))
        self.seed_strategy_combo = ttk.Combobox(
            common,
            textvariable=self.seed_strategy_display,
            values=tuple(seed_strategy_labels().values()),
            width=22,
            state="readonly",
        )
        self.seed_strategy_combo.grid(row=11, column=1, sticky="w", pady=4)
        self.seed_strategy_combo.bind("<<ComboboxSelected>>", self._on_seed_strategy_selected)
        ttk.Label(common, text=tr("label.random_seed")).grid(row=11, column=2, sticky="e", padx=(8, 4), pady=4)
        self.random_seed_entry = ttk.Entry(common, textvariable=self.random_seed, width=14)
        self.random_seed_entry.grid(row=11, column=3, sticky="w", pady=4)
        ttk.Label(
            common,
            text=tr("seed.strategy.note"),
            wraplength=820,
        ).grid(row=12, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self._update_seed_strategy_state()

        ttk.Label(common, text=tr("label.language")).grid(row=13, column=0, sticky="w", pady=4)
        self.language.set(language_label(current_language()))
        language_combo = ttk.Combobox(common, textvariable=self.language, values=language_options(), width=14, state="readonly")
        language_combo.grid(row=13, column=1, sticky="w", pady=4)
        language_combo.bind("<<ComboboxSelected>>", self._change_language)
        common.columnconfigure(1, weight=1)
        common.columnconfigure(2, weight=2)

        self.develop_frame = ttk.LabelFrame(outer, text=tr("section.develop"), padding=10)
        self.develop_frame.pack(fill="x", pady=8)
        ttk.Label(self.develop_frame, text=tr("label.developer")).grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(
            self.develop_frame,
            textvariable=self.developer_type,
            values=("standard", "fine_grain", "compensating", "high_contrast", "monobath"),
            state="readonly",
        ).grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(self.develop_frame, text=tr("label.frame_size")).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(
            self.develop_frame,
            textvariable=self.frame_size,
            values=("half_frame", "35mm", "6x6", "6x7", "4x5"),
            state="readonly",
        ).grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        self._slider(self.develop_frame, tr("label.material_degradation"), self.material_degradation, 0.00, 1.00, 2, help_key="help.material_degradation")
        self._slider(self.develop_frame, tr("label.exposure_ev"), self.exposure_ev, -2.0, 2.0, 3, expert_min=-5.0, expert_max=5.0, help_key="help.exposure_ev")

        self._slider(self.develop_frame, tr("label.develop_time"), self.develop_time, 1.0, 20.0, 4, expert_min=0.0, expert_max=60.0, help_key="help.develop_time")
        self._slider(self.develop_frame, tr("label.temperature"), self.develop_temperature, 12.0, 32.0, 5, expert_min=4.0, expert_max=45.0, help_key="help.temperature")
        self._slider(self.develop_frame, tr("label.concentration"), self.developer_concentration, 0.25, 2.00, 6, expert_min=0.05, expert_max=5.00, help_key="help.concentration")
        self._slider(self.develop_frame, tr("label.agitation"), self.agitation, 0.00, 2.50, 7, expert_min=0.0, expert_max=5.0, help_key="help.agitation")
        self._slider(self.develop_frame, tr("label.push"), self.push, -2.0, 3.0, 8, expert_min=-4.0, expert_max=6.0, help_key="help.push")
        self._slider(self.develop_frame, tr("label.developer_exhaustion"), self.exhaustion, 0.00, 1.00, 9, help_key="help.developer_exhaustion")
        self._slider(self.develop_frame, tr("label.compensation"), self.compensation, 0.00, 1.00, 10, help_key="help.compensation")
        ttk.Label(self.develop_frame, text=tr("label.fixer")).grid(row=11, column=0, sticky="w", pady=4)
        ttk.Combobox(
            self.develop_frame,
            textvariable=self.fixer_type,
            values=("standard", "rapid", "hardening", "monobath"),
            state="readonly",
        ).grid(row=11, column=1, columnspan=3, sticky="ew", pady=4)
        self._slider(self.develop_frame, tr("label.fixer_exhaustion"), self.fixer_exhaustion, 0.00, 1.00, 12, help_key="help.fixer_exhaustion")
        self._slider(self.develop_frame, tr("label.silver_retention"), self.silver_retention, 0.00, 1.00, 13, help_key="help.silver_retention")
        self._slider(self.develop_frame, tr("label.silver_plating"), self.silver_plating, 0.00, 1.00, 14, help_key="help.silver_plating")
        self._slider(self.develop_frame, tr("label.light_leak"), self.light_leak, 0.00, 1.00, 15, help_key="help.light_leak")
        self._slider(self.develop_frame, tr("label.chemical_stain"), self.chemical_stain, 0.00, 1.00, 16, help_key="help.chemical_stain")
        self._slider(self.develop_frame, tr("label.uneven_development"), self.uneven_development, 0.00, 1.00, 17, help_key="help.uneven_development")
        self._slider(self.develop_frame, tr("label.development_adjacency_strength"), self.development_adjacency_strength, 0.00, 1.00, 18, help_key="help.development_adjacency_strength")
        self._slider(self.develop_frame, tr("label.development_adjacency_radius"), self.development_adjacency_radius, 0.0002, 0.02, 19, help_key="help.development_adjacency_radius", decimals=4)
        self._slider(self.develop_frame, tr("label.process_variation"), self.process_variation, 0.00, 1.00, 20, help_key="help.process_variation")
        self._slider(self.develop_frame, tr("label.halation"), self.halation, 0.00, 2.00, 21, expert_min=0.0, expert_max=5.0, help_key="help.halation")
        self._slider(self.develop_frame, tr("label.halation_sensitivity"), self.halation_sensitivity, -1.00, 1.00, 22, help_key="help.halation_sensitivity")
        self._slider(self.develop_frame, tr("label.halation_edge"), self.halation_edge, 0.00, 1.00, 23, help_key="help.halation_edge")
        ttk.Checkbutton(self.develop_frame, text="MTF", variable=self.enable_mtf).grid(row=24, column=0, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text=tr("label.enable_halation"), variable=self.enable_halation).grid(row=24, column=1, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text=tr("label.enable_grain"), variable=self.enable_grain).grid(row=24, column=2, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text=tr("label.bw_negative"), variable=self.force_bw).grid(row=24, column=3, sticky="w", pady=4)

        ttk.Checkbutton(self.develop_frame, text=tr("label.save_scanner_raw"), variable=self.save_scanner_raw).grid(row=25, column=0, sticky="w", pady=4)
        self._slider(self.develop_frame, tr("label.scanner_raw_border"), self.scanner_raw_border, 0.0, 12.0, 26, expert_min=0.0, expert_max=30.0)
        self.transparent_export_check = ttk.Checkbutton(self.develop_frame, text=tr("label.export_transparent_plate"), variable=self.export_transparent_plate)
        self.plate_set_export_check = ttk.Checkbutton(self.develop_frame, text=tr("label.export_plate_set"), variable=self.export_plate_set)
        self.layer_pack_export_check = ttk.Checkbutton(
            self.develop_frame,
            text=tr("label.export_layer_pack"),
            variable=self.export_layer_pack,
            command=self._update_export_option_state,
        )
        self.transparent_export_check.grid(row=27, column=0, sticky="w", pady=4)
        self.plate_set_export_check.grid(row=27, column=1, sticky="w", pady=4)
        self.layer_pack_export_check.grid(row=27, column=2, sticky="w", pady=4)
        self._update_export_option_state()

        self.scan_frame = ttk.LabelFrame(outer, text=tr("section.scan"), padding=10)
        self.scan_frame.pack(fill="x", pady=8)
        self._slider(self.scan_frame, tr("label.print_contrast"), self.print_contrast, 0.70, 1.60, 0, expert_min=0.30, expert_max=3.00, help_key="help.print_contrast")
        self._slider(self.scan_frame, tr("label.print_exposure"), self.print_exposure, -2.0, 2.0, 1, expert_min=-5.0, expert_max=5.0, help_key="help.print_exposure")
        self._slider(self.scan_frame, tr("label.scan_saturation"), self.saturation, 0.80, 1.35, 2, expert_min=0.0, expert_max=3.0, help_key="help.scan_saturation")
        self._slider(self.scan_frame, tr("label.scan_strength"), self.scan_strength, 0.00, 1.00, 3, help_key="help.scan_strength")
        self._slider(self.scan_frame, tr("label.anti_banding"), self.anti_banding, 0.00, 1.00, 4, help_key="help.anti_banding")
        self._slider(self.scan_frame, tr("label.filter_r"), self.print_shift_r, -0.12, 0.12, 5, help_key="help.print_color_shift")
        self._slider(self.scan_frame, tr("label.filter_g"), self.print_shift_g, -0.12, 0.12, 6, help_key="help.print_color_shift")
        self._slider(self.scan_frame, tr("label.filter_b"), self.print_shift_b, -0.12, 0.12, 7, help_key="help.print_color_shift")
        self._slider(self.scan_frame, tr("label.highlight_green"), self.highlight_green, 0.85, 1.25, 8, help_key="help.highlight_color_bias")
        self._slider(self.scan_frame, tr("label.highlight_blue"), self.highlight_blue, 0.75, 1.10, 9, help_key="help.highlight_color_bias")
        scan_normalize_check = ttk.Checkbutton(self.scan_frame, text=tr("label.scan_normalize"), variable=self.scan_normalize)
        scan_normalize_check.grid(row=10, column=0, sticky="w", pady=4)
        attach_tooltip(scan_normalize_check, "help.scan_normalize")
        self._slider(self.scan_frame, tr("label.transmission_light_ev"), self.light_table_ev, -2.0, 2.0, 11, expert_min=-4.0, expert_max=4.0, help_key="help.transmission_light_ev")
        self._slider(self.scan_frame, tr("label.transmission_light_temperature"), self.light_table_temperature, 3200.0, 7500.0, 12, expert_min=2400.0, expert_max=10000.0, integer=True, help_key="help.transmission_light_temperature")
        self._slider(self.scan_frame, tr("label.positive_color_control"), self.positive_color_control, 0.0, 1.0, 13)
        self._slider(self.scan_frame, tr("label.projection_white_softness"), self.projection_white_softness, 0.0, 0.75, 14)
        self._slider(self.scan_frame, tr("label.projection_black_adaptation"), self.projection_black_adaptation, 0.0, 0.75, 15)
        self.scan_pipeline_label = ttk.Label(
            self.scan_frame,
            textvariable=self.scan_pipeline_description,
            wraplength=820,
            foreground="#555555",
        )
        self.scan_pipeline_label.grid(row=16, column=0, columnspan=4, sticky="w", pady=(8, 2))
        self.negative_channel_compensation_check = ttk.Checkbutton(
            self.scan_frame,
            text=tr("label.negative_channel_compensation"),
            variable=self.negative_channel_compensation,
        )
        self.negative_channel_compensation_check.grid(row=17, column=0, columnspan=2, sticky="w", pady=4)
        attach_tooltip(self.negative_channel_compensation_check, "help.negative_channel_compensation")
        self._slider(
            self.scan_frame,
            tr("label.negative_channel_compensation_strength"),
            self.negative_channel_compensation_strength,
            0.0,
            1.0,
            18,
            help_key="help.negative_channel_compensation_strength",
        )
        self.clear_base_border_check = ttk.Checkbutton(
            self.scan_frame,
            text=tr("label.include_clear_base_border"),
            variable=self.include_clear_base_border,
        )
        self.clear_base_border_check.grid(
            row=19,
            column=0,
            columnspan=2,
            sticky="w",
            pady=4,
        )
        attach_tooltip(self.clear_base_border_check, "help.include_clear_base_border")
        self._slider(
            self.scan_frame,
            tr("label.clear_base_border_width"),
            self.scanner_raw_border,
            0.0,
            12.0,
            20,
            expert_min=0.0,
            expert_max=30.0,
        )

        buttons = ttk.Frame(self.root, padding=(12, 8))
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text=tr("button.preview"), command=self._start_preview).pack(side="left", fill="x", expand=True, padx=(0, 5))
        self.process_button = ttk.Button(buttons, text=tr("button.process"), command=self._start_process)
        self.process_button.pack(side="left", fill="x", expand=True, padx=(5, 0))
        if self._processing:
            self.process_button.state(["disabled"])

        status_bar = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        status_bar.pack(fill="x", side="bottom")
        ttk.Label(status_bar, textvariable=self.status, wraplength=860).pack(fill="x")

        for frame in (self.paths_frame, self.develop_frame, self.scan_frame):
            frame.columnconfigure(1, weight=1)
            frame.columnconfigure(2, weight=1)
        self.main_scroller.bind_mousewheel()

    def _change_language(self, _event=None) -> None:
        set_language(language_from_label(self.language.get()))
        for child in self.root.winfo_children():
            child.destroy()
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.destroy()
        self.preview_window = None
        self.preview_left_label = None
        self.preview_right_label = None
        self.preview_left_title.set(tr("preview.input"))
        self.preview_right_title.set(tr("preview.result"))
        self.slider_specs = []
        self._build()
        self._set_material_ui_from_key(self.film_preset.get())
        self._set_develop_ui_from_key(self.develop_preset.get())
        self._set_scan_interpretation_display()
        self._refresh_scanner_catalog()
        self._update_mode_visibility()

    def _on_seed_strategy_selected(self, _event=None) -> None:
        self.seed_strategy.set(
            seed_strategy_key_from_label(self.seed_strategy_display.get())
        )
        self._update_seed_strategy_state()

    def _update_seed_strategy_state(self) -> None:
        if not hasattr(self, "random_seed_entry"):
            return
        if self.seed_strategy.get() == "random":
            self.random_seed_entry.state(["disabled"])
        else:
            self.random_seed_entry.state(["!disabled"])

    def _path_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, file_cmd, folder_cmd, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=3, sticky="e", pady=3)
        if file_cmd is not None:
            ttk.Button(buttons, text=tr("button.file"), command=file_cmd).pack(side="left", padx=2)
        if folder_cmd is not None:
            ttk.Button(buttons, text=tr("button.folder"), command=folder_cmd).pack(side="left", padx=2)

    def _slider(
        self,
        parent: ttk.Frame,
        label: str,
        var,
        min_value: float,
        max_value: float,
        row: int,
        integer: bool = False,
        expert_min: float | None = None,
        expert_max: float | None = None,
        help_key: str | None = None,
        decimals: int = 2,
    ) -> None:
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky="w", pady=4)
        scale = ttk.Scale(parent, from_=min_value, to=max_value, variable=var)
        scale.grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        value_text = tk.StringVar()
        entry = ttk.Entry(parent, textvariable=value_text, width=8)
        attach_tooltip(label_widget, help_key)
        attach_tooltip(scale, help_key)
        attach_tooltip(entry, help_key)

        def update_value(*_) -> None:
            value = var.get()
            value_text.set(
                str(int(value)) if integer else f"{value:.{max(0, int(decimals))}f}"
            )

        def commit_value(_event=None) -> None:
            try:
                value = float(value_text.get())
            except ValueError:
                update_value()
                return
            if integer:
                value = int(round(value))
            var.set(value)

        var.trace_add("write", update_value)
        update_value()
        entry.grid(row=row, column=3, sticky="e", pady=4)
        entry.bind("<Return>", commit_value)
        entry.bind("<FocusOut>", commit_value)
        self.slider_specs.append(
            {
                "scale": scale,
                "base": (min_value, max_value),
                "expert": (
                    min_value if expert_min is None else expert_min,
                    max_value if expert_max is None else expert_max,
                ),
            }
        )

    def _set_grid_rows_visible(self, parent: ttk.Frame, rows: set[int], visible: bool) -> None:
        for widget in parent.grid_slaves():
            info = widget.grid_info()
            if "row" in info:
                setattr(widget, "_foundry_grid_row", int(info["row"]))
        for widget in parent.winfo_children():
            row = int(getattr(widget, "_foundry_grid_row", -1))
            if row in rows:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()

    def _open_editor(self, module_name: str) -> None:
        tool_ids = {
            "film_foundry.tools.run_film_material_editor": "film",
            "film_foundry.tools.run_develop_process_editor": "develop",
            "film_foundry.tools.run_scanner_render_editor": "scanner",
            "film_foundry.tools.run_positive_scanner_editor": "positive_scanner",
        }
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--tool", tool_ids.get(module_name, "main")]
        else:
            command = [sys.executable, "-m", module_name]
        try:
            subprocess.Popen(command, cwd=str(PROJECT_ROOT))
            self.status.set(f"已打开外部编辑器：{module_name}")
        except Exception as exc:
            self.status.set(f"打开编辑器失败：{exc}")

    def _refresh_all_preset_combos(self) -> None:
        current_film = self.film_preset.get()
        current_develop = self.develop_preset.get()
        current_scanner = self.scanner_preset.get()
        film_values = film_preset_names()
        develop_values = develop_preset_names()
        scanner_values = scanner_preset_names()
        if current_film not in film_values and film_values:
            self.film_preset.set(film_values[0])
        if current_develop not in develop_values and develop_values:
            self.develop_preset.set(develop_values[0])
        if current_scanner not in scanner_values and scanner_values:
            self.scanner_preset.set(scanner_values[0])
        self._set_material_ui_from_key(self.film_preset.get())
        self._set_develop_ui_from_key(self.develop_preset.get())
        self._refresh_scanner_catalog()
        self.status.set(tr("status.presets_refreshed"))

    def _set_material_ui_from_key(self, key: str) -> None:
        if self._material_ui_syncing:
            return
        self._material_ui_syncing = True
        try:
            current = film_preset_record(key)
            polarity = current["polarity"] if current is not None else "negative"
            self.material_polarity_display.set(material_polarity_label(polarity))
            records = film_preset_catalog(polarity)
            if current is not None and all(record["key"] != key for record in records):
                records.insert(0, current)
            self._film_display_key_map = {record["label"]: record["key"] for record in records}
            labels = list(self._film_display_key_map)
            self.film_preset_combo.configure(values=labels)
            selected = next((record["label"] for record in records if record["key"] == key), "")
            self.film_preset_display.set(selected or (labels[0] if labels else ""))
        finally:
            self._material_ui_syncing = False

    def _on_material_category_selected(self, _event=None) -> None:
        if self._material_ui_syncing:
            return
        reverse = {material_polarity_label(key): key for key in ("negative", "positive")}
        polarity = reverse.get(self.material_polarity_display.get(), "negative")
        records = film_preset_catalog(polarity)
        if records:
            self.film_preset.set(records[0]["key"])

    def _on_film_preset_display_selected(self, _event=None) -> None:
        if self._material_ui_syncing:
            return
        key = self._film_display_key_map.get(self.film_preset_display.get())
        if key and key != self.film_preset.get():
            self.film_preset.set(key)

    def _set_develop_ui_from_key(self, key: str) -> None:
        records = develop_preset_catalog()
        current = develop_preset_record(key)
        if current is not None and all(record["key"] != key for record in records):
            records.insert(0, current)
        self._develop_display_key_map = {record["label"]: record["key"] for record in records}
        labels = list(self._develop_display_key_map)
        self.develop_preset_combo.configure(values=labels)
        selected = next((record["label"] for record in records if record["key"] == key), "")
        self.develop_preset_display.set(selected or (labels[0] if labels else ""))

    def _on_develop_preset_display_selected(self, _event=None) -> None:
        key = self._develop_display_key_map.get(self.develop_preset_display.get())
        if key and key != self.develop_preset.get():
            self.develop_preset.set(key)

    def _scan_mode_labels(self) -> dict[str, str]:
        return {mode: tr(f"scan.mode.{mode}") for mode in ("auto", "negative", "positive")}

    def _set_scan_interpretation_display(self) -> None:
        labels = self._scan_mode_labels()
        self.scan_interpretation_display.set(labels.get(self.scan_interpretation.get(), labels["auto"]))

    def _on_scan_interpretation_display_selected(self, _event=None) -> None:
        reverse = {label: mode for mode, label in self._scan_mode_labels().items()}
        mode = reverse.get(self.scan_interpretation_display.get())
        if mode is not None:
            self.scan_interpretation.set(mode)

    def _expected_scan_polarity(self) -> str:
        path = develop_preset_path(self.develop_preset.get())
        if path.exists():
            config = DarkroomConfig.from_json(path)
            program = str(config.chemistry.program_key).lower()
            if program in {"bw_reversal", "color_reversal"}:
                return "positive"
            if program in {"bw_negative", "color_negative", "color_negative_bleach_bypass"}:
                return "negative"
        film = film_preset_record(self.film_preset.get())
        return film["polarity"] if film is not None else "negative"

    def _refresh_scanner_catalog(self) -> None:
        records = scanner_preset_catalog()
        current = scanner_preset_record(self.scanner_preset.get())
        if current is not None and all(record["key"] != current["key"] for record in records):
            records.insert(0, current)
        self._scanner_display_key_map = {record["label"]: record["key"] for record in records}
        labels = list(self._scanner_display_key_map)
        self.scanner_preset_combo.configure(values=labels)
        selected_label = next((record["label"] for record in records if record["key"] == self.scanner_preset.get()), "")
        self.scanner_preset_display.set(selected_label or (labels[0] if labels else ""))
        if hasattr(self, "scan_frame"):
            self._update_scan_parameter_visibility()

    def _on_scanner_preset_display_selected(self, _event=None) -> None:
        key = self._scanner_display_key_map.get(self.scanner_preset_display.get())
        if key and key != self.scanner_preset.get():
            self.scanner_preset.set(key)

    def _sync_scan_interpretation_controls(self) -> None:
        self._set_scan_interpretation_display()
        if hasattr(self, "scanner_preset_combo"):
            self._refresh_scanner_catalog()
        self._update_scan_parameter_visibility()

    def _sync_manual_scan_controls(self) -> None:
        self.scan_interpretation.set("manual")
        self._update_scan_parameter_visibility()

    def _update_expert_visibility(self) -> None:
        if not hasattr(self, "develop_frame"):
            return
        visible = bool(self.expert_mode.get())
        for spec in getattr(self, "slider_specs", []):
            min_value, max_value = spec["expert"] if visible else spec["base"]
            spec["scale"].configure(from_=min_value, to=max_value)
        self._set_grid_rows_visible(self.develop_frame, {0, 2, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20}, visible)
        if hasattr(self, "scan_frame"):
            self._set_grid_rows_visible(self.scan_frame, {5, 6, 7, 8, 9}, visible)
            self._update_scan_parameter_visibility(visible)

    def _update_export_option_state(self) -> None:
        if not hasattr(self, "transparent_export_check"):
            return
        state = "disabled" if bool(self.export_layer_pack.get()) else "normal"
        self.transparent_export_check.configure(state=state)
        self.plate_set_export_check.configure(state=state)

    def _update_scan_parameter_visibility(self, expert: bool | None = None) -> None:
        if not hasattr(self, "scan_frame"):
            return
        expert_visible = bool(self.expert_mode.get()) if expert is None else expert
        self.scan_frame.configure(text=tr("scan.panel.shared"))
        self._set_grid_rows_visible(
            self.scan_frame,
            {11, 12, 13, 17, 19, 20},
            True,
        )
        self._set_grid_rows_visible(self.scan_frame, {14, 15, 18}, expert_visible)
        formed_negative = self._expected_scan_polarity() == "negative"
        self.scan_recommendation.set(
            tr(
                "scan.recommendation.negative"
                if formed_negative
                else "scan.recommendation.positive"
            )
        )
        if self.scan_invert.get():
            pipeline_key = (
                "scan.pipeline.manual.remove_invert"
                if self.scan_remove_base.get()
                else "scan.pipeline.manual.preserve_invert"
            )
        else:
            pipeline_key = (
                "scan.pipeline.manual.remove_direct"
                if self.scan_remove_base.get()
                else "scan.pipeline.manual.preserve_direct"
            )
        self.scan_pipeline_description.set(tr(pipeline_key))

    def _sync_film_preset_controls(self) -> None:
        self._set_material_ui_from_key(self.film_preset.get())
        path = film_preset_path(self.film_preset.get())
        if not path.exists():
            self.film_preset_source.set(preset_source_text(self.film_preset.get(), USER_FILM_PRESET_DIR, FILM_PRESET_DIR))
            return
        self.film_preset_source.set(preset_source_text(self.film_preset.get(), USER_FILM_PRESET_DIR, FILM_PRESET_DIR))
        config = DarkroomConfig.from_json(path)
        self.material_degradation.set(float(config.film.material_degradation))
        if self.develop_preset.get() in {
            "standard_color_negative",
            "standard_bw_negative",
            "standard_color_reversal",
            "standard_bw_reversal",
        }:
            self.develop_preset.set(recommended_standard_develop_preset(config))
        self.exposure_ev.set(float(config.look.exposure_ev))
        self.negative_contrast.set(float(config.look.negative_contrast))
        self.halation.set(float(config.look.halation_multiplier))
        self.halation_sensitivity.set(float(config.look.halation_sensitivity))
        self.grain.set(float(config.look.grain_multiplier))
        self.grain_size.set(float(config.look.grain_size_multiplier))
        self.emulsion_mtf.set(float(config.look.emulsion_mtf_strength if config.look.emulsion_mtf_strength is not None else config.film.emulsion_mtf_strength))
        self.artifact_suppression.set(float(config.look.digital_artifact_suppression if config.look.digital_artifact_suppression is not None else config.film.digital_artifact_suppression))
        self.halation_edge.set(float(config.look.halation_edge_compensation if config.look.halation_edge_compensation is not None else config.film.halation_gradient_suppression))
        self._refresh_scanner_catalog()

    def _sync_develop_preset_controls(self) -> None:
        self._set_develop_ui_from_key(self.develop_preset.get())
        path = develop_preset_path(self.develop_preset.get())
        if not path.exists():
            self.develop_preset_source.set(preset_source_text(self.develop_preset.get(), USER_DEVELOP_PRESET_DIR, DEVELOP_PRESET_DIR))
            return
        self.develop_preset_source.set(preset_source_text(self.develop_preset.get(), USER_DEVELOP_PRESET_DIR, DEVELOP_PRESET_DIR))
        config = DarkroomConfig.from_json(path)
        self.exposure_ev.set(float(config.look.exposure_ev))
        self.negative_contrast.set(float(config.look.negative_contrast))
        self.halation.set(float(config.look.halation_multiplier))
        self.halation_sensitivity.set(float(config.look.halation_sensitivity))
        self.grain.set(float(config.look.grain_multiplier))
        self.grain_size.set(float(config.look.grain_size_multiplier))
        self.developer_type.set(str(config.chemistry.developer_type))
        self.fixer_type.set(str(config.chemistry.fixer_type))
        self.frame_size.set(str(config.chemistry.frame_size))
        self.develop_time.set(float(config.chemistry.time_min))
        self.develop_temperature.set(float(config.chemistry.temperature_c))
        self.developer_concentration.set(float(config.chemistry.concentration))
        self.agitation.set(float(config.chemistry.agitation))
        self.push.set(float(config.chemistry.push_stops))
        self.exhaustion.set(float(config.chemistry.developer_exhaustion))
        self.fixer_exhaustion.set(float(config.chemistry.fixer_exhaustion))
        self.silver_retention.set(float(config.chemistry.silver_retention))
        self.silver_plating.set(float(config.chemistry.silver_plating))
        self.compensation.set(float(config.chemistry.compensation))
        self.light_leak.set(float(config.chemistry.light_leak_strength))
        self.chemical_stain.set(float(config.chemistry.chemical_stain))
        self.uneven_development.set(float(config.chemistry.uneven_development))
        self.development_adjacency_strength.set(float(config.chemistry.development_adjacency_strength))
        self.development_adjacency_radius.set(float(config.chemistry.development_adjacency_radius))
        self.process_variation.set(float(config.chemistry.process_variation))
        self.force_bw.set(str(config.mode).lower() == "bw_negative")
        self._refresh_scanner_catalog()

    def _sync_scanner_preset_controls(self) -> None:
        self._refresh_scanner_catalog()
        path = scanner_preset_path(self.scanner_preset.get())
        if not path.exists():
            self.scanner_preset_source.set(preset_source_text(self.scanner_preset.get(), USER_SCANNER_PRESET_DIR, SCANNER_PRESET_DIR))
            return
        config = DarkroomConfig.from_json(path)
        source = preset_source_text(self.scanner_preset.get(), USER_SCANNER_PRESET_DIR, SCANNER_PRESET_DIR)
        self.scanner_preset_source.set(source)
        self.print_contrast.set(float(config.look.print_contrast))
        self.print_exposure.set(float(config.look.print_exposure_ev))
        self.saturation.set(float(config.scanner.scan_saturation))
        self.scan_normalize.set(bool(config.scanner.scan_normalize))
        self.scan_strength.set(float(config.scanner.scan_normalize_strength))
        self.anti_banding.set(float(config.output.anti_banding_strength))
        shift = tuple(float(v) for v in config.scanner.print_color_shift)
        self.print_shift_r.set(shift[0])
        self.print_shift_g.set(shift[1])
        self.print_shift_b.set(shift[2])
        highlight = tuple(float(v) for v in config.scanner.highlight_color_bias)
        self.highlight_green.set(highlight[1])
        self.highlight_blue.set(highlight[2])
        positive = scanner_preset_polarity(config) == "positive"
        self.light_table_ev.set(float(
            config.scanner.transmission_light_ev
            if config.scanner.transmission_light_ev is not None
            else (
                config.scanner.light_table_ev
                if positive
                else config.scanner.negative_backlight_ev
            )
        ))
        self.light_table_temperature.set(float(
            config.scanner.transmission_light_temperature_k
            if config.scanner.transmission_light_temperature_k is not None
            else (
                config.scanner.light_table_temperature_k
                if positive
                else config.scanner.negative_backlight_temperature_k
            )
        ))
        self.positive_color_control.set(float(config.scanner.positive_scan_color_control_strength))
        self.projection_white_softness.set(float(config.scanner.projection_white_softness))
        self.projection_black_adaptation.set(float(config.scanner.projection_black_adaptation))
        self.negative_channel_compensation.set(
            bool(config.scanner.negative_channel_compensation_enabled)
        )
        self.negative_channel_compensation_strength.set(
            float(config.scanner.negative_channel_compensation_strength)
        )
        self._update_scan_parameter_visibility()

    def _update_mode_visibility(self) -> None:
        mode = self.pipeline_mode.get()
        self.develop_frame.pack_forget()
        self.scan_frame.pack_forget()
        if mode in ("full", "develop"):
            self.develop_frame.pack(fill="x", pady=8)
        if mode in ("full", "scan"):
            self.scan_frame.pack(fill="x", pady=8)

        for widget in (
            self.material_category_label,
            self.film_preset_label,
            self.material_polarity_combo,
            self.film_preset_combo,
            self.film_editor_button,
            self.develop_preset_label,
            self.develop_preset_combo,
            self.develop_editor_button,
        ):
            if mode in ("full", "develop"):
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.scanner_preset_label, self.scanner_preset_combo, self.scanner_editor_button):
            if mode in ("full", "scan"):
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (
            self.scan_interpretation_label,
            self.scan_interpretation_controls,
        ):
            if mode in ("full", "scan"):
                widget.grid()
            else:
                widget.grid_remove()
        if mode == "scan":
            self.scan_output_follow_check.grid()
        else:
            self.scan_output_follow_check.grid_remove()
        self.scan_interpretation.set("manual")
        self._refresh_scanner_catalog()
        self._update_expert_visibility()

        if mode == "develop":
            self.status.set(tr("status.develop"))
        elif mode == "scan":
            self.status.set(tr("status.scan"))
        else:
            self.status.set(tr("status.full"))

    def _choose_input_file(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        path = filedialog.askopenfilename(filetypes=[("Images", extensions), ("All files", "*.*")])
        if path:
            self.input_path.set(path)

    def _open_scanner_editor(self) -> None:
        self._open_editor("film_foundry.tools.run_scanner_render_editor")

    def _choose_input_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.input_path.set(path)

    def _choose_negative_file(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("Darkroom negatives", "*.npz *.tif *.tiff"), ("All files", "*.*")]
        )
        if path:
            self.negative_path.set(path)

    def _choose_negative_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.negative_path.set(path)

    def _choose_output_folder(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.output_path.set(path)

    def _preset_references(self) -> dict[str, dict]:
        return {
            "film": preset_reference(self.film_preset.get(), "film", USER_FILM_PRESET_DIR, FILM_PRESET_DIR),
            "develop": preset_reference(self.develop_preset.get(), "develop", USER_DEVELOP_PRESET_DIR, DEVELOP_PRESET_DIR),
            "scanner": preset_reference(self.scanner_preset.get(), "scanner", USER_SCANNER_PRESET_DIR, SCANNER_PRESET_DIR),
        }

    def _session_payload(self) -> dict:
        payload = session_payload(
            config=self._config(),
            pipeline_mode=self.pipeline_mode.get(),
            input_path=self.input_path.get(),
            negative_path=self.negative_path.get(),
            output_path=self.output_path.get(),
            presets=self._preset_references(),
        )
        payload["ui_options"] = {
            "scan_output_near_source": bool(self.scan_output_near_source.get()),
            "run_as_preview": bool(self.run_as_preview.get()),
        }
        return payload

    def _session_payload_for_config(self, config: DarkroomConfig) -> dict:
        payload = session_payload(
            config=config,
            pipeline_mode=self.pipeline_mode.get(),
            input_path=self.input_path.get(),
            negative_path=self.negative_path.get(),
            output_path=self.output_path.get(),
            presets=self._preset_references(),
        )
        payload["ui_options"] = {
            "scan_output_near_source": bool(self.scan_output_near_source.get()),
            "run_as_preview": bool(self.run_as_preview.get()),
        }
        return payload

    def _save_session(self) -> None:
        path = filedialog.asksaveasfilename(
            initialdir=str(USER_PRESET_DIR),
            initialfile="film_foundry_session.json",
            defaultextension=".json",
            filetypes=[("Film Foundry session", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            save_session(path, self._session_payload())
            self.status.set(f"{tr('status.session_saved')}: {path}")
        except Exception as exc:
            self.status.set(f"{tr('status.session_save_failed')}: {exc}")

    def _apply_loaded_config(self, config: DarkroomConfig) -> None:
        self.scan_interpretation.set("manual")
        self.scan_remove_base.set(bool(config.scanner.remove_base_mask))
        self.scan_invert.set(bool(config.scanner.invert_transmission))
        self.fast_mode.set(bool(config.fast_mode))
        self.quality_mode.set(str(config.processing.quality_mode))
        strategy = str(config.seed_strategy).strip().lower()
        self.seed_strategy.set(strategy if strategy in SEED_STRATEGY_KEYS else "random")
        self.seed_strategy_display.set(seed_strategy_label(self.seed_strategy.get()))
        self.random_seed.set("0" if config.random_seed is None else str(int(config.random_seed)))
        self._update_seed_strategy_state()
        self.exposure_ev.set(float(config.look.exposure_ev))
        self.print_contrast.set(float(config.look.print_contrast))
        self.print_exposure.set(float(config.look.print_exposure_ev))
        self.output_format.set(str(config.output.format))
        self.preview_long_edge.set(0 if config.output.preview_long_edge is None else int(config.output.preview_long_edge))
        self.save_scanner_raw.set(bool(config.output.save_scanner_raw))
        self.scanner_raw_border.set(float(config.output.scanner_raw_border_percent) * 100.0)
        self.include_clear_base_border.set(
            bool(config.scanner.include_clear_base_border)
        )
        self.export_layer_pack.set(bool(config.output.export_layer_pack))
        self.export_transparent_plate.set(bool(config.output.export_transparent_plate))
        self.export_plate_set.set(bool(config.output.export_plate_set))
        self.enable_mtf.set(bool(config.enable_mtf))
        self.enable_halation.set(bool(config.enable_halation))
        self.enable_grain.set(bool(config.enable_grain))
        self.save_sidecar.set(bool(config.save_sidecar))
        self.debug_output.set(bool(config.debug_output))
        self.comparison_grid.set(bool(config.comparison_grid))

        self.force_bw.set(str(config.mode).lower() == "bw_negative")
        self.material_degradation.set(float(config.film.material_degradation))
        self.negative_contrast.set(float(config.look.negative_contrast))
        self.halation.set(float(config.look.halation_multiplier))
        self.halation_sensitivity.set(float(config.look.halation_sensitivity))
        self.grain.set(float(config.look.grain_multiplier))
        self.grain_size.set(float(config.look.grain_size_multiplier))
        self.emulsion_mtf.set(float(config.look.emulsion_mtf_strength if config.look.emulsion_mtf_strength is not None else config.film.emulsion_mtf_strength))
        self.artifact_suppression.set(float(config.look.digital_artifact_suppression if config.look.digital_artifact_suppression is not None else config.film.digital_artifact_suppression))
        self.halation_edge.set(float(config.look.halation_edge_compensation if config.look.halation_edge_compensation is not None else config.film.halation_gradient_suppression))

        self.developer_type.set(str(config.chemistry.developer_type))
        self.fixer_type.set(str(config.chemistry.fixer_type))
        self.frame_size.set(str(config.chemistry.frame_size))
        self.develop_time.set(float(config.chemistry.time_min))
        self.develop_temperature.set(float(config.chemistry.temperature_c))
        self.developer_concentration.set(float(config.chemistry.concentration))
        self.agitation.set(float(config.chemistry.agitation))
        self.push.set(float(config.chemistry.push_stops))
        self.exhaustion.set(float(config.chemistry.developer_exhaustion))
        self.fixer_exhaustion.set(float(config.chemistry.fixer_exhaustion))
        self.silver_retention.set(float(config.chemistry.silver_retention))
        self.silver_plating.set(float(config.chemistry.silver_plating))
        self.compensation.set(float(config.chemistry.compensation))
        self.light_leak.set(float(config.chemistry.light_leak_strength))
        self.chemical_stain.set(float(config.chemistry.chemical_stain))
        self.uneven_development.set(float(config.chemistry.uneven_development))
        self.development_adjacency_strength.set(float(config.chemistry.development_adjacency_strength))
        self.development_adjacency_radius.set(float(config.chemistry.development_adjacency_radius))
        self.process_variation.set(float(config.chemistry.process_variation))

        self.scan_normalize.set(bool(config.scanner.scan_normalize))
        self.scan_strength.set(float(config.scanner.scan_normalize_strength))
        self.anti_banding.set(float(config.output.anti_banding_strength))
        self.saturation.set(float(config.scanner.scan_saturation))
        shift = tuple(float(v) for v in config.scanner.print_color_shift)
        self.print_shift_r.set(shift[0])
        self.print_shift_g.set(shift[1])
        self.print_shift_b.set(shift[2])
        highlight = tuple(float(v) for v in config.scanner.highlight_color_bias)
        self.highlight_green.set(highlight[1])
        self.highlight_blue.set(highlight[2])
        positive = not bool(config.scanner.invert_transmission)
        self.light_table_ev.set(float(
            config.scanner.transmission_light_ev
            if config.scanner.transmission_light_ev is not None
            else (
                config.scanner.light_table_ev
                if positive
                else config.scanner.negative_backlight_ev
            )
        ))
        self.light_table_temperature.set(float(
            config.scanner.transmission_light_temperature_k
            if config.scanner.transmission_light_temperature_k is not None
            else (
                config.scanner.light_table_temperature_k
                if positive
                else config.scanner.negative_backlight_temperature_k
            )
        ))
        self.positive_color_control.set(float(config.scanner.positive_scan_color_control_strength))
        self.projection_white_softness.set(float(config.scanner.projection_white_softness))
        self.projection_black_adaptation.set(float(config.scanner.projection_black_adaptation))
        self.negative_channel_compensation.set(
            bool(config.scanner.negative_channel_compensation_enabled)
        )
        self.negative_channel_compensation_strength.set(
            float(config.scanner.negative_channel_compensation_strength)
        )

    def _load_session(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(USER_PRESET_DIR),
            filetypes=[("Film Foundry session", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            payload = load_session(path)
            paths = payload.get("paths", {})
            if isinstance(paths, dict):
                self.input_path.set(str(paths.get("input", self.input_path.get())))
                self.negative_path.set(
                    str(paths.get("developed_medium", paths.get("negative", self.negative_path.get())))
                )
                self.output_path.set(str(paths.get("output", self.output_path.get())))
            presets = payload.get("presets", {})
            if isinstance(presets, dict):
                if isinstance(presets.get("film"), dict):
                    self.film_preset.set(preset_value_from_reference(presets["film"]))
                if isinstance(presets.get("develop"), dict):
                    self.develop_preset.set(preset_value_from_reference(presets["develop"]))
                if isinstance(presets.get("scanner"), dict):
                    self.scanner_preset.set(preset_value_from_reference(presets["scanner"]))
            config_data = payload.get("config")
            if isinstance(config_data, dict):
                self._apply_loaded_config(DarkroomConfig.from_dict(config_data))
            ui_options = payload.get("ui_options")
            if isinstance(ui_options, dict):
                self.scan_output_near_source.set(
                    bool(ui_options.get("scan_output_near_source", True))
                )
                self.run_as_preview.set(
                    bool(ui_options.get("run_as_preview", self.run_as_preview.get()))
                )
            self.pipeline_mode.set(str(payload.get("pipeline_mode", self.pipeline_mode.get())))
            self._update_mode_visibility()
            self.status.set(f"{tr('status.session_loaded')}: {path}")
        except Exception as exc:
            self.status.set(f"{tr('status.session_load_failed')}: {exc}")

    def _config(self) -> DarkroomConfig:
        mode = self.pipeline_mode.get()
        film_config = None
        develop_config = None
        scanner_config = None
        if mode in ("full", "develop"):
            film_config = DarkroomConfig.from_json(film_preset_path(self.film_preset.get()))
            develop_config = DarkroomConfig.from_json(develop_preset_path(self.develop_preset.get()))
        if mode in ("full", "scan"):
            scanner_config = DarkroomConfig.from_json(scanner_preset_path(self.scanner_preset.get()))
        config = merge_config_presets(film_config, scanner_config, develop_config=develop_config)
        config.fast_mode = bool(self.fast_mode.get())
        config.processing.execution_mode = (
            "reduced_fast" if config.fast_mode else "quality"
        )
        config.processing.quality_mode = str(self.quality_mode.get())
        config.seed_strategy = str(self.seed_strategy.get())
        if config.seed_strategy == "random":
            config.random_seed = None
        else:
            seed_text = self.random_seed.get().strip()
            try:
                config.random_seed = int(seed_text or "0")
            except ValueError as exc:
                raise ValueError(tr("error.random_seed_integer")) from exc
        config.look.exposure_ev = float(self.exposure_ev.get())
        config.look.print_contrast = float(self.print_contrast.get())
        config.look.print_exposure_ev = float(self.print_exposure.get())
        config.output.format = str(self.output_format.get())
        config.output.render_long_edge = None
        config.output.preview_long_edge = None if self.preview_long_edge.get() <= 0 else int(self.preview_long_edge.get())
        config.output.save_scanner_raw = bool(self.save_scanner_raw.get())
        config.output.scanner_raw_border_percent = float(self.scanner_raw_border.get()) / 100.0
        config.output.scanner_raw_border_min_px = 32
        config.scanner.include_clear_base_border = bool(
            self.include_clear_base_border.get()
        )
        config.output.export_layer_pack = bool(self.export_layer_pack.get())
        config.output.export_transparent_plate = bool(self.export_transparent_plate.get())
        config.output.export_plate_set = bool(self.export_plate_set.get())
        config.enable_mtf = bool(self.enable_mtf.get())
        config.enable_halation = bool(self.enable_halation.get())
        config.enable_grain = bool(self.enable_grain.get())
        config.save_sidecar = bool(self.save_sidecar.get())
        config.debug_output = bool(self.debug_output.get())
        config.comparison_grid = bool(self.comparison_grid.get())
        if mode in ("full", "develop"):
            config.mode = "bw_negative" if self.force_bw.get() else "color_negative"
            config.look.negative_contrast = float(self.negative_contrast.get())
            config.look.halation_multiplier = float(self.halation.get())
            config.look.halation_sensitivity = float(self.halation_sensitivity.get())
            config.look.grain_multiplier = float(self.grain.get())
            config.look.grain_size_multiplier = float(self.grain_size.get())
            config.look.emulsion_mtf_strength = float(self.emulsion_mtf.get())
            config.look.digital_artifact_suppression = float(self.artifact_suppression.get())
            config.look.halation_edge_compensation = float(self.halation_edge.get())
            config.chemistry.developer_type = str(self.developer_type.get())
            config.film.material_degradation = float(self.material_degradation.get())
            config.chemistry.developer_name = str(self.developer_type.get()).replace("_", " ").title()
            config.chemistry.fixer_type = str(self.fixer_type.get())
            config.chemistry.fixer_name = str(self.fixer_type.get()).replace("_", " ").title()
            config.chemistry.frame_size = str(self.frame_size.get())
            config.chemistry.time_min = float(self.develop_time.get())
            config.chemistry.temperature_c = float(self.develop_temperature.get())
            config.chemistry.concentration = float(self.developer_concentration.get())
            config.chemistry.agitation = float(self.agitation.get())
            config.chemistry.push_stops = float(self.push.get())
            config.chemistry.developer_exhaustion = float(self.exhaustion.get())
            config.chemistry.fixer_exhaustion = float(self.fixer_exhaustion.get())
            config.chemistry.silver_retention = float(self.silver_retention.get())
            config.chemistry.silver_plating = float(self.silver_plating.get())
            config.chemistry.compensation = float(self.compensation.get())
            config.chemistry.light_leak_strength = float(self.light_leak.get())
            config.chemistry.chemical_stain = float(self.chemical_stain.get())
            config.chemistry.uneven_development = float(self.uneven_development.get())
            config.chemistry.development_adjacency_strength = float(self.development_adjacency_strength.get())
            config.chemistry.development_adjacency_radius = float(self.development_adjacency_radius.get())
            config.chemistry.process_variation = float(self.process_variation.get())
        if mode in ("full", "scan"):
            config.scanner.remove_base_mask = bool(self.scan_remove_base.get())
            config.scanner.invert_transmission = bool(self.scan_invert.get())
            configure_scan_interpretation(config, "manual")
            config.scanner.scan_normalize = bool(self.scan_normalize.get())
            config.scanner.scan_normalize_strength = float(self.scan_strength.get())
            config.scanner.scan_normalize_mode = "luma"
            config.output.anti_banding_strength = float(self.anti_banding.get())
            config.scanner.scan_saturation = float(self.saturation.get())
            config.scanner.print_color_shift = (
                float(self.print_shift_r.get()),
                float(self.print_shift_g.get()),
                float(self.print_shift_b.get()),
            )
            config.scanner.print_color_bias = (1.0, 1.0, 1.0)
            config.scanner.highlight_color_bias = (
                1.00,
                float(self.highlight_green.get()),
                float(self.highlight_blue.get()),
            )
            config.scanner.transmission_light_ev = float(self.light_table_ev.get())
            config.scanner.transmission_light_temperature_k = float(
                self.light_table_temperature.get()
            )
            # Keep legacy field banks synchronized for older presets/tools.
            config.scanner.light_table_ev = float(self.light_table_ev.get())
            config.scanner.light_table_temperature_k = float(
                self.light_table_temperature.get()
            )
            config.scanner.negative_backlight_ev = float(self.light_table_ev.get())
            config.scanner.negative_backlight_temperature_k = float(
                self.light_table_temperature.get()
            )
            config.scanner.positive_scan_color_control_strength = float(self.positive_color_control.get())
            config.scanner.projection_white_softness = float(self.projection_white_softness.get())
            config.scanner.projection_black_adaptation = float(self.projection_black_adaptation.get())
            config.scanner.negative_channel_compensation_enabled = bool(
                self.negative_channel_compensation.get()
            )
            config.scanner.negative_channel_compensation_strength = float(
                self.negative_channel_compensation_strength.get()
            )
        return config

    def _first_input_image(self) -> Path | None:
        inputs = iter_images(Path(self.input_path.get()))
        return inputs[0] if inputs else None

    def _first_negative_file(self) -> Path | None:
        negatives = iter_developed_medium_files(Path(self.negative_path.get()))
        return negatives[0] if negatives else None

    def _array_to_photo(self, image: np.ndarray) -> ImageTk.PhotoImage:
        image = np.clip(image, 0.0, 1.0)
        arr = np.round(image * 255.0).astype(np.uint8)
        pil_image = Image.fromarray(arr, mode="RGB")
        pil_image.thumbnail((PREVIEW_DISPLAY_LONG_EDGE, PREVIEW_DISPLAY_LONG_EDGE), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(pil_image)

    def _ensure_preview_window(self) -> None:
        if self.preview_window is not None and self.preview_window.winfo_exists():
            self.preview_window.lift()
            return
        self.preview_window = tk.Toplevel(self.root)
        self.preview_window.title(tr("preview.window"))
        self.preview_window.geometry("1120x620")

        frame = ttk.Frame(self.preview_window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, textvariable=self.preview_left_title).grid(row=0, column=0, sticky="w", pady=(0, 8))
        ttk.Label(frame, textvariable=self.preview_right_title).grid(row=0, column=1, sticky="w", pady=(0, 8))
        self.preview_left_label = ttk.Label(frame)
        self.preview_left_label.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.preview_right_label = ttk.Label(frame)
        self.preview_right_label.grid(row=1, column=1, sticky="nsew", padx=(8, 0))
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(1, weight=1)

    def _show_preview(self, source_name: str, left: np.ndarray, right: np.ndarray, left_title: str, right_title: str) -> None:
        self._ensure_preview_window()
        self.preview_window.title(f"{tr('preview.window')} - {source_name}")
        self.preview_left_title.set(left_title)
        self.preview_right_title.set(right_title)
        self.preview_left_photo = self._array_to_photo(left)
        self.preview_right_photo = self._array_to_photo(right)
        self.preview_left_label.configure(image=self.preview_left_photo)
        self.preview_right_label.configure(image=self.preview_right_photo)
        self.status.set(f"预览已更新：{source_name}")

    def _start_preview(self) -> None:
        Thread(target=self._render_preview, daemon=True).start()

    def _render_preview(self) -> None:
        try:
            mode = self.pipeline_mode.get()
            config = self._config()
            config.fast_mode = True
            config.debug_output = False
            config.comparison_grid = False
            config.save_sidecar = False

            self.root.after(0, lambda: self.status.set(tr("status.previewing")))
            if mode in ("full", "develop"):
                input_path = self._first_input_image()
                if input_path is None:
                    self.root.after(0, lambda: self.status.set(tr("status.no_input")))
                    return
                original = resize_to_long_edge(load_image(input_path), PREVIEW_PROCESS_LONG_EDGE)
                if mode == "develop":
                    negative = develop_negative(original, config, rng=rng_for_develop(input_path, config))
                    preview = negative_visual_preview(negative.density_grain, config.film)
                    density = density_preview(negative.density_grain, config)
                    halo_preview = gray_to_rgb(halation_alpha(negative.after_mtf, negative.after_halation))
                    halo_linear = gray_to_rgb(halation_alpha_linear(negative.after_mtf, negative.after_halation))
                    inspector = preview_grid(
                        [
                            ("negative visual", preview),
                            ("density debug", density),
                            ("cyan density", gray_to_rgb(density[..., 0])),
                            ("magenta density", gray_to_rgb(density[..., 1])),
                            ("yellow density", gray_to_rgb(density[..., 2])),
                            ("halation preview", halo_preview),
                            ("halation linear", halo_linear),
                        ],
                        tile_size=260,
                        columns=2,
                    )
                    self.root.after(0, lambda: self._show_preview(input_path.name, original, inspector, tr("preview.input_image"), tr("preview.develop_inspector")))
                else:
                    result = process_array(original, config)
                    self.root.after(0, lambda: self._show_preview(input_path.name, original, result, tr("preview.input_image"), tr("preview.final_positive")))
                return

            negative_path = self._first_negative_file()
            if negative_path is None:
                self.root.after(0, lambda: self.status.set(tr("status.no_negative")))
                return
            scanned, scan_source, preview = scan_from_file(negative_path, config)
            positive_observation = str(scanned.input_polarity).strip().lower() == "positive"
            if positive_observation:
                inspector_items = [
                    (tr("preview.stage.transmitted_medium"), preview),
                    (tr("preview.stage.scanner_raw"), scanned.scanner_raw),
                    (tr("preview.stage.positive_tone_input"), scanned.positive_raw),
                    (tr("preview.stage.final_scan"), scanned.output_srgb),
                ]
            else:
                inspector_items = [
                    (tr("preview.negative_visual"), preview),
                    (tr("preview.stage.scanner_raw"), scanned.scanner_raw),
                    (tr("preview.stage.negative_base_balanced"), scanned.negative_base_balanced),
                    (tr("preview.stage.negative_positive_raw"), scanned.positive_raw),
                    (tr("preview.stage.negative_channel_reconstructed"), scanned.negative_channel_reconstructed),
                    (tr("preview.stage.final_scan"), scanned.output_srgb),
                ]
            inspector = preview_grid(
                inspector_items,
                tile_size=300,
                columns=2,
            )
            diagnostic = scanner_capture_summary(scanned.metadata)
            self.root.after(0, lambda: self._show_preview(
                negative_path.name,
                preview,
                inspector,
                tr("preview.negative_visual"),
                f"{tr('preview.scan_inspector')} ({scan_source}) · {diagnostic}",
            ))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self.status.set(f"预览失败：{message}"))

    def _start_process(self) -> None:
        if self._processing:
            return
        try:
            mode = self.pipeline_mode.get()
            config = self._config()
            output_root = Path(self.output_path.get())
            negative_root = Path(self.negative_path.get())
            input_root = Path(self.input_path.get())
            preview = bool(self.run_as_preview.get())
            scan_output_near_source = bool(self.scan_output_near_source.get())
            settings_snapshot = self._session_payload_for_config(config)
        except Exception as exc:
            self.status.set(f"{tr('status.process_failed')}: {exc}")
            return

        self._processing = True
        if self.process_button is not None:
            self.process_button.state(["disabled"])
        self.status.set(tr("status.processing"))
        Thread(
            target=self._process,
            args=(
                mode,
                config,
                input_root,
                negative_root,
                output_root,
                preview,
                scan_output_near_source,
                settings_snapshot,
            ),
            daemon=True,
        ).start()

    def _post_status(self, message: str) -> None:
        self.root.after(0, lambda value=message: self.status.set(value))

    def _finish_process(self) -> None:
        self._processing = False
        if self.process_button is not None and self.process_button.winfo_exists():
            self.process_button.state(["!disabled"])

    def _process(
        self,
        mode: str,
        config: DarkroomConfig,
        input_root: Path,
        negative_root: Path,
        output_root: Path,
        preview: bool,
        scan_output_near_source: bool,
        settings_snapshot: dict,
    ) -> None:
        try:
            if mode == "full":
                inputs = iter_images(input_root)
                if not inputs:
                    self._post_status(tr("status.no_input"))
                    return
                assert_unique_output_stems(inputs, "Full workflow")
                run_root, run_id = create_run_directory(output_root, "full")
                run_settings = self._run_settings_payload(
                    settings_snapshot,
                    run_id=run_id,
                    run_root=run_root,
                    output_root=run_root,
                    developed_root=None,
                )
                manifest_path = run_root / "film_foundry_run.json"
                save_session(manifest_path, run_settings)
                batch_started_at = datetime.now().isoformat(timespec="seconds")
                batch_started_counter = perf_counter()
                item_records: list[dict[str, object]] = []
                completed = 0
                failures: list[tuple[Path, Exception]] = []
                for input_path in inputs:
                    item_started_counter = perf_counter()
                    output_path = output_path_for(input_path, run_root, config.output.format)
                    try:
                        process_file(input_path, output_path, config, preview=preview)
                        completed += 1
                    except Exception as exc:
                        failures.append((input_path, exc))
                        item_records.append(
                            batch_item_record(
                                input_path,
                                output_path,
                                perf_counter() - item_started_counter,
                                exc,
                            )
                        )
                    else:
                        item_records.append(
                            batch_item_record(
                                input_path,
                                output_path,
                                perf_counter() - item_started_counter,
                            )
                        )
                record_error = save_batch_run_record(
                    manifest_path,
                    run_settings,
                    operation="full",
                    started_at=batch_started_at,
                    started_counter=batch_started_counter,
                    items=item_records,
                )
                message = self._batch_status("full", completed, failures, run_root)
                if record_error is not None:
                    message += f" {tr('status.session_save_failed')}: {record_error}"
                self._post_status(message)
                return

            if mode == "develop":
                inputs = iter_images(input_root)
                if not inputs:
                    self._post_status(tr("status.no_input"))
                    return
                assert_unique_output_stems(inputs, "Develop workflow")
                run_root, run_id = create_run_directory(output_root, "develop")
                developed_root = run_root / "negative"
                developed_root.mkdir()
                run_settings = self._run_settings_payload(
                    settings_snapshot,
                    run_id=run_id,
                    run_root=run_root,
                    output_root=run_root,
                    developed_root=developed_root,
                )
                manifest_path = run_root / "film_foundry_run.json"
                save_session(manifest_path, run_settings)
                batch_started_at = datetime.now().isoformat(timespec="seconds")
                batch_started_counter = perf_counter()
                item_records = []
                completed = 0
                failures = []
                for input_path in inputs:
                    item_started_counter = perf_counter()
                    developed_path: Path | None = None
                    try:
                        execution_mode = resolve_execution_mode(
                            config,
                            scaled_override=preview,
                        )
                        long_edge = processing_long_edge(
                            config,
                            scaled_override=preview,
                        )
                        runtime_config = copy.deepcopy(config)
                        runtime_config.processing.execution_mode = execution_mode
                        runtime_config.fast_mode = execution_mode == "reduced_fast"
                        if execution_mode == "scaled_fast":
                            image = load_image(input_path, decode_long_edge=long_edge)
                        else:
                            image = load_image(input_path)
                        image = resize_to_long_edge(image, long_edge)
                        develop_rng, resolved_seed = rng_and_resolved_seed_for_develop(
                            input_path,
                            runtime_config,
                        )
                        negative = develop_negative(
                            image,
                            runtime_config,
                            rng=develop_rng,
                        )
                        del image
                        developed_path = developed_path_for(input_path, developed_root, negative)
                        save_developed_medium(
                            negative,
                            developed_path,
                            input_path,
                            runtime_config,
                            config.save_sidecar,
                            resolved_seed=resolved_seed,
                        )
                        completed += 1
                    except Exception as exc:
                        failures.append((input_path, exc))
                        item_records.append(
                            batch_item_record(
                                input_path,
                                developed_path,
                                perf_counter() - item_started_counter,
                                exc,
                            )
                        )
                    else:
                        item_records.append(
                            batch_item_record(
                                input_path,
                                developed_path,
                                perf_counter() - item_started_counter,
                            )
                        )
                record_error = save_batch_run_record(
                    manifest_path,
                    run_settings,
                    operation="develop",
                    started_at=batch_started_at,
                    started_counter=batch_started_counter,
                    items=item_records,
                )
                self.root.after(0, lambda value=str(developed_root): self.negative_path.set(value))
                message = self._batch_status("develop", completed, failures, run_root)
                if record_error is not None:
                    message += f" {tr('status.session_save_failed')}: {record_error}"
                self._post_status(message)
                return

            negative_paths = iter_developed_medium_files(negative_root)
            if not negative_paths:
                self._post_status(tr("status.no_negative"))
                return
            assert_unique_output_stems(negative_paths, "Scan workflow")
            scan_output_root = (
                default_scan_output_root(negative_root)
                if scan_output_near_source
                else output_root
            )
            if output_target_is_file(scan_output_root):
                raise ValueError(f"扫描输出位置必须是文件夹：{scan_output_root}")
            scan_output_root.mkdir(parents=True, exist_ok=True)
            run_id = unique_scan_run_id(scan_output_root)
            run_settings = self._run_settings_payload(
                settings_snapshot,
                run_id=run_id,
                run_root=scan_output_root,
                output_root=scan_output_root,
                developed_root=negative_root,
            )
            manifest_path = scan_output_root / f"film_foundry_{run_id}.json"
            save_session(manifest_path, run_settings)
            batch_started_at = datetime.now().isoformat(timespec="seconds")
            batch_started_counter = perf_counter()
            item_records = []
            completed = 0
            failures = []
            for negative_path in negative_paths:
                item_started_counter = perf_counter()
                output_path = output_path_for(
                    negative_path,
                    scan_output_root,
                    config.output.format,
                    run_suffix=run_id,
                )
                try:
                    scanned, scan_source, _preview = scan_from_file(negative_path, config)
                    scan_source_value = scanned.metadata.get("source_path")
                    scan_source_path = Path(scan_source_value) if isinstance(scan_source_value, str) and scan_source_value else negative_path
                    sidecar = (
                        final_positive_sidecar(
                                negative_path=negative_path,
                                scan_source=scan_source,
                                scan_source_path=scan_source_path,
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
                        protected_paths=(negative_path, scan_source_path),
                    )
                    completed += 1
                except Exception as exc:
                    failures.append((negative_path, exc))
                    item_records.append(
                        batch_item_record(
                            negative_path,
                            output_path,
                            perf_counter() - item_started_counter,
                            exc,
                        )
                    )
                else:
                    item_records.append(
                        batch_item_record(
                            negative_path,
                            output_path,
                            perf_counter() - item_started_counter,
                        )
                    )
            record_error = save_batch_run_record(
                manifest_path,
                run_settings,
                operation="scan",
                started_at=batch_started_at,
                started_counter=batch_started_counter,
                items=item_records,
            )
            message = self._batch_status("scan", completed, failures, scan_output_root)
            if record_error is not None:
                message += f" {tr('status.session_save_failed')}: {record_error}"
            self._post_status(message)
        except Exception as exc:
            self._post_status(f"{tr('status.process_failed')}: {exc}")
        finally:
            self.root.after(0, self._finish_process)

    @staticmethod
    def _run_settings_payload(
        snapshot: dict,
        *,
        run_id: str,
        run_root: Path,
        output_root: Path,
        developed_root: Path | None,
    ) -> dict:
        payload = copy.deepcopy(snapshot)
        payload["created_at"] = datetime.now().isoformat(timespec="seconds")
        run_record = {
            "id": str(run_id),
            "directory": str(run_root),
            "actual_output": str(output_root),
            "status": "running",
        }
        if developed_root is not None:
            run_record["developed_medium"] = str(developed_root)
        payload["run"] = run_record
        paths = payload.setdefault("paths", {})
        if isinstance(paths, dict) and developed_root is not None:
            # Keep the user-selected output base in ``paths.output`` so loading
            # this snapshot creates a sibling generation instead of nesting a
            # new run inside the completed run. Actual destinations live in
            # the explicit ``run`` record above.
            paths["developed_medium"] = str(developed_root)
            paths["negative"] = str(developed_root)
        return payload

    @staticmethod
    def _batch_status(
        operation: str,
        completed: int,
        failures: list[tuple[Path, Exception]],
        output_root: Path,
    ) -> str:
        message = tr("status.batch_complete").format(
            operation=tr(f"mode.{operation}"),
            completed=completed,
            failed=len(failures),
            output=output_root,
        )
        if failures:
            first_path, first_error = failures[0]
            message += " " + tr("status.batch_first_error").format(path=first_path, error=first_error)
        return message

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DarkroomPanel().run()
