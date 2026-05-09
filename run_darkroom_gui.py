"""Film Foundry / Electronic Negative Factory 简易调参面板。

直接在 IDE 里运行本文件即可打开窗口。

GUI 现在按阶段工作：
- 完整流程：显示冲洗参数 + 扫描参数
- 只冲洗底片：只显示 film/develop 相关参数，输出 .npz 底片母版
- 只扫描底片：只显示 scan/render 相关参数，读取 .npz 反复测试扫描效果
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from threading import Thread
import tkinter as tk
from tkinter import filedialog, ttk

import numpy as np
from PIL import Image, ImageTk

from half_frame_darkroom.core.engine import develop_negative, process_array, process_file, scan_negative, scan_scanner_raw, seed_from_path
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


PROJECT_ROOT = Path(__file__).resolve().parent
APP_TITLE = "Film Foundry - Electronic Negative Factory"
PRESET_DIR = PROJECT_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
DEFAULT_INPUT_DIR = PROJECT_ROOT / "input_images"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_NEGATIVE_DIR = DEFAULT_OUTPUT_DIR / "negatives"
PREVIEW_PROCESS_LONG_EDGE = 900
PREVIEW_DISPLAY_LONG_EDGE = 520
NEGATIVE_SUFFIX = ".darkroom_negative.npz"


def output_path_for(input_path: Path, output_root: Path, output_format: str) -> Path:
    if output_root.suffix:
        return output_root
    suffix = "." + output_format.lower().lstrip(".")
    stem = input_path.stem.replace(".darkroom_negative", "")
    return output_root / f"{stem}_darkroom{suffix}"


def negative_path_for(input_path: Path, negative_root: Path) -> Path:
    if negative_root.suffix.lower() == ".npz":
        return negative_root
    return negative_root / f"{input_path.stem}{NEGATIVE_SUFFIX}"


def scanner_raw_path_for_negative(negative_path: Path) -> Path:
    return negative_path.with_suffix(".scanner_raw.tiff")


def is_scanner_raw_tiff(path: Path) -> bool:
    return path.suffix.lower() in {".tif", ".tiff"} and ".scanner_raw" in path.stem


def iter_negative_files(path: Path) -> list[Path]:
    if path.is_file() and (path.suffix.lower() == ".npz" or is_scanner_raw_tiff(path)):
        return [path]
    if path.is_dir():
        npz_paths = sorted(path.glob(f"*{NEGATIVE_SUFFIX}"))
        raw_paths = sorted(item for item in path.glob("*.scanner_raw.tif*") if is_scanner_raw_tiff(item))
        npz_raw_paths = {scanner_raw_path_for_negative(item).resolve() for item in npz_paths}
        return npz_paths + [item for item in raw_paths if item.resolve() not in npz_raw_paths]
    return []


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


def resolved_seed_for(path: Path, config: DarkroomConfig) -> int | None:
    strategy = str(config.seed_strategy).lower()
    if strategy == "fixed":
        return 0 if config.random_seed is None else int(config.random_seed)
    if strategy == "path":
        return seed_from_path(path, 0 if config.random_seed is None else int(config.random_seed))
    return None


def rng_for_develop(path: Path, config: DarkroomConfig) -> np.random.Generator:
    return np.random.default_rng(resolved_seed_for(path, config))


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def save_negative(negative: DevelopedNegative, path: Path, input_path: Path, config: DarkroomConfig, save_sidecar: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        density_cmy=negative.density_cmy.astype(np.float32),
        density_grain=negative.density_grain.astype(np.float32),
    )
    preview_path = path.with_suffix(".negative_visual.png")
    save_image(negative_visual_preview(negative.density_grain, config.film), preview_path, config.output)
    scanner_raw_path = None
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
        if save_sidecar:
            save_json(
                scanner_raw_path.with_suffix(scanner_raw_path.suffix + ".json"),
                {
                    "kind": "ScannerRawNegative",
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "input_path": str(input_path),
                    "negative_path": str(path),
                    "scanner_raw_path": str(scanner_raw_path),
                    "encoding": "16-bit linear RGB TIFF, no sRGB gamma",
                    "border": {
                        "meaning": "unexposed clear film base for mask removal",
                        "percent": config.output.scanner_raw_border_percent,
                        "min_px": config.output.scanner_raw_border_min_px,
                    },
                    "config": asdict(config),
                },
            )
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
                metadata={
                    "input_path": str(input_path),
                    "negative_path": str(path),
                    "config": asdict(config),
                },
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
    if save_sidecar:
        save_json(
            path.with_suffix(path.suffix + ".json"),
            {
                "kind": "DevelopedNegative",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "input_path": str(input_path),
                "negative_path": str(path),
                "negative_visual_preview": str(preview_path),
                "scanner_raw_path": str(scanner_raw_path) if scanner_raw_path is not None else None,
                "material_exports": material_paths,
                "resolved_seed": resolved_seed_for(input_path, config),
                "config": asdict(config),
            },
        )


def load_negative(path: Path) -> DevelopedNegative:
    return load_developed_negative_npz(path)


def scan_from_file(path: Path, config: DarkroomConfig):
    if is_scanner_raw_tiff(path):
        scanner_raw = load_linear_rgb_tiff(path)
        inner, border_samples = split_scanner_raw_border(
            scanner_raw,
            border_percent=config.output.scanner_raw_border_percent,
            border_min_px=config.output.scanner_raw_border_min_px,
        )
        return scan_scanner_raw(inner, config, base_samples=border_samples, source_path=path), "scanner_raw_tiff", scanner_raw

    scanner_raw_path = scanner_raw_path_for_negative(path)
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
            scanner_raw,
        )

    if path.suffix.lower() != ".npz":
        raise ValueError(f"不支持的底片文件：{path}。请选择 .npz 或 .scanner_raw.tiff，不要选择 sidecar .json。")
    negative = load_negative(path)
    scanned = scan_negative(negative, config)
    preview = negative_visual_preview(negative.density_grain, config.film)
    return scanned, "density_npz", preview


class DarkroomPanel:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("940x760")
        self.root.minsize(760, 560)

        self.pipeline_mode = tk.StringVar(value="full")
        self.input_path = tk.StringVar(value=str(DEFAULT_INPUT_DIR))
        self.negative_path = tk.StringVar(value=str(DEFAULT_NEGATIVE_DIR))
        self.output_path = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.film_preset = tk.StringVar(value="clear_modern_negative")
        self.scanner_preset = tk.StringVar(value="neutral_scan")

        self.run_as_preview = tk.BooleanVar(value=True)
        self.preview_long_edge = tk.IntVar(value=1600)
        self.fast_mode = tk.BooleanVar(value=True)

        self.exposure_ev = tk.DoubleVar(value=-0.35)
        self.negative_contrast = tk.DoubleVar(value=1.05)
        self.halation = tk.DoubleVar(value=0.90)
        self.grain = tk.DoubleVar(value=0.85)
        self.grain_size = tk.DoubleVar(value=1.00)
        self.emulsion_mtf = tk.DoubleVar(value=0.25)
        self.artifact_suppression = tk.DoubleVar(value=0.15)
        self.halation_edge = tk.DoubleVar(value=0.35)
        self.push = tk.DoubleVar(value=0.0)
        self.use_push = tk.BooleanVar(value=False)
        self.force_bw = tk.BooleanVar(value=False)
        self.enable_mtf = tk.BooleanVar(value=True)
        self.enable_halation = tk.BooleanVar(value=True)
        self.enable_grain = tk.BooleanVar(value=True)

        self.print_contrast = tk.DoubleVar(value=1.12)
        self.print_exposure = tk.DoubleVar(value=0.0)
        self.saturation = tk.DoubleVar(value=1.00)
        self.scan_normalize = tk.BooleanVar(value=True)
        self.scan_strength = tk.DoubleVar(value=0.15)
        self.print_shift_r = tk.DoubleVar(value=0.06)
        self.print_shift_g = tk.DoubleVar(value=0.00)
        self.print_shift_b = tk.DoubleVar(value=-0.08)
        self.highlight_green = tk.DoubleVar(value=1.04)
        self.highlight_blue = tk.DoubleVar(value=0.94)

        self.debug_output = tk.BooleanVar(value=False)
        self.comparison_grid = tk.BooleanVar(value=False)
        self.save_sidecar = tk.BooleanVar(value=True)
        self.save_scanner_raw = tk.BooleanVar(value=True)
        self.scanner_raw_border = tk.DoubleVar(value=4.0)
        self.export_layer_pack = tk.BooleanVar(value=False)
        self.export_transparent_plate = tk.BooleanVar(value=True)
        self.export_plate_set = tk.BooleanVar(value=True)
        self.output_format = tk.StringVar(value="jpg")

        self.status = tk.StringVar(value="选择阶段模式，然后点击预览或开始处理。")
        self.preview_window: tk.Toplevel | None = None
        self.preview_left_label: ttk.Label | None = None
        self.preview_right_label: ttk.Label | None = None
        self.preview_left_title = tk.StringVar(value="输入")
        self.preview_right_title = tk.StringVar(value="结果")
        self.preview_left_photo = None
        self.preview_right_photo = None

        self._build()
        self.film_preset.trace_add("write", lambda *_: self._sync_film_preset_controls())
        self.scanner_preset.trace_add("write", lambda *_: self._sync_scanner_preset_controls())
        self._sync_film_preset_controls()
        self._sync_scanner_preset_controls()
        self._update_mode_visibility()

    def _build(self) -> None:
        scroll_shell = ttk.Frame(self.root)
        scroll_shell.pack(fill="both", expand=True)

        self.scroll_canvas = tk.Canvas(scroll_shell, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_shell, orient="vertical", command=self.scroll_canvas.yview)
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        outer = ttk.Frame(self.scroll_canvas, padding=12)
        canvas_window = self.scroll_canvas.create_window((0, 0), window=outer, anchor="nw")

        def update_scroll_region(_event=None) -> None:
            self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))

        def fit_inner_width(event) -> None:
            self.scroll_canvas.itemconfigure(canvas_window, width=event.width)
            update_scroll_region()

        outer.bind("<Configure>", update_scroll_region)
        self.scroll_canvas.bind("<Configure>", fit_inner_width)
        self.scroll_canvas.bind("<Enter>", self._bind_mousewheel)
        self.scroll_canvas.bind("<Leave>", self._unbind_mousewheel)

        header = ttk.LabelFrame(outer, text="阶段模式", padding=10)
        header.pack(fill="x", pady=(0, 8))
        modes = (
            ("完整流程", "full"),
            ("只冲洗底片", "develop"),
            ("只扫描底片", "scan"),
        )
        for text, value in modes:
            ttk.Radiobutton(
                header,
                text=text,
                value=value,
                variable=self.pipeline_mode,
                command=self._update_mode_visibility,
            ).pack(side="left", padx=(0, 18))

        self.paths_frame = ttk.LabelFrame(outer, text="路径", padding=10)
        self.paths_frame.pack(fill="x", pady=8)
        self._path_row(self.paths_frame, "输入图片", self.input_path, self._choose_input_file, self._choose_input_folder, 0)
        self._path_row(self.paths_frame, "底片 .npz", self.negative_path, self._choose_negative_file, self._choose_negative_folder, 1)
        self._path_row(self.paths_frame, "输出", self.output_path, None, self._choose_output_folder, 2)

        common = ttk.LabelFrame(outer, text="通用", padding=10)
        common.pack(fill="x", pady=8)
        self.film_preset_label = ttk.Label(common, text="胶片 / 冲洗预设")
        self.film_preset_combo = ttk.Combobox(
            common,
            textvariable=self.film_preset,
            values=[path.stem for path in sorted(FILM_PRESET_DIR.glob("*.json"))],
            state="readonly",
        )
        self.film_preset_label.grid(row=0, column=0, sticky="w", pady=4)
        self.film_preset_combo.grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)

        self.scanner_preset_label = ttk.Label(common, text="扫描 / 输出预设")
        self.scanner_preset_combo = ttk.Combobox(
            common,
            textvariable=self.scanner_preset,
            values=[path.stem for path in sorted(SCANNER_PRESET_DIR.glob("*.json"))],
            state="readonly",
        )
        self.scanner_preset_label.grid(row=1, column=0, sticky="w", pady=4)
        self.scanner_preset_combo.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Checkbutton(common, text="按预览长边输出/冲洗", variable=self.run_as_preview).grid(row=2, column=0, sticky="w", pady=4)
        self._slider(common, "预览长边 px（0=原图）", self.preview_long_edge, 0, 4000, 3, integer=True)
        ttk.Checkbutton(common, text="快速模式", variable=self.fast_mode).grid(row=4, column=0, sticky="w", pady=4)
        ttk.Checkbutton(common, text="保存 sidecar JSON", variable=self.save_sidecar).grid(row=4, column=1, sticky="w", pady=4)
        ttk.Checkbutton(common, text="debug 中间结果", variable=self.debug_output).grid(row=4, column=2, sticky="w", pady=4)
        ttk.Checkbutton(common, text="四宫格对比", variable=self.comparison_grid).grid(row=4, column=3, sticky="w", pady=4)
        ttk.Label(common, text="输出格式").grid(row=5, column=0, sticky="w", pady=4)
        ttk.Combobox(common, textvariable=self.output_format, values=("jpg", "png", "tiff"), width=8, state="readonly").grid(
            row=5, column=1, sticky="w", pady=4
        )
        common.columnconfigure(1, weight=1)
        common.columnconfigure(2, weight=1)

        self.develop_frame = ttk.LabelFrame(outer, text="冲洗 / 底片形成参数", padding=10)
        self.develop_frame.pack(fill="x", pady=8)
        self._slider(self.develop_frame, "曝光补偿 EV", self.exposure_ev, -2.0, 2.0, 0)
        self._slider(self.develop_frame, "显影反差 / H-D gamma", self.negative_contrast, 0.70, 1.50, 1)
        self._slider(self.develop_frame, "光晕强度", self.halation, 0.00, 2.00, 2)
        self._slider(self.develop_frame, "颗粒强度", self.grain, 0.00, 2.50, 3)
        self._slider(self.develop_frame, "颗粒尺寸 / 随画幅", self.grain_size, 0.35, 3.00, 4)
        self._slider(self.develop_frame, "乳剂解析力限制", self.emulsion_mtf, 0.00, 1.00, 5)
        self._slider(self.develop_frame, "数字锐化伪影抑制", self.artifact_suppression, 0.00, 1.00, 6)
        self._slider(self.develop_frame, "光晕硬边补偿", self.halation_edge, 0.00, 1.00, 7)
        ttk.Checkbutton(self.develop_frame, text="MTF", variable=self.enable_mtf).grid(row=8, column=0, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text="光晕", variable=self.enable_halation).grid(row=8, column=1, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text="颗粒", variable=self.enable_grain).grid(row=8, column=2, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text="黑白负片模式", variable=self.force_bw).grid(row=8, column=3, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text="使用迫冲档数", variable=self.use_push).grid(row=9, column=0, sticky="w", pady=4)
        ttk.Scale(self.develop_frame, from_=0.0, to=3.0, variable=self.push).grid(row=9, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(self.develop_frame, textvariable=self.push, width=7).grid(row=9, column=3, sticky="e", pady=4)

        ttk.Checkbutton(self.develop_frame, text="保存电子负片 TIFF", variable=self.save_scanner_raw).grid(row=10, column=0, sticky="w", pady=4)
        self._slider(self.develop_frame, "电子负片片基边框 %", self.scanner_raw_border, 0.0, 12.0, 11)
        ttk.Checkbutton(self.develop_frame, text="导出透明片基", variable=self.export_transparent_plate).grid(row=12, column=0, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text="导出制版分层", variable=self.export_plate_set).grid(row=12, column=1, sticky="w", pady=4)
        ttk.Checkbutton(self.develop_frame, text="导出 Layer Pack", variable=self.export_layer_pack).grid(row=12, column=2, sticky="w", pady=4)

        self.scan_frame = ttk.LabelFrame(outer, text="扫描 / 正像解释参数", padding=10)
        self.scan_frame.pack(fill="x", pady=8)
        self._slider(self.scan_frame, "打印反差倍率", self.print_contrast, 0.70, 1.60, 0)
        self._slider(self.scan_frame, "打印曝光 EV", self.print_exposure, -2.0, 2.0, 1)
        self._slider(self.scan_frame, "染料光谱选择性", self.saturation, 0.80, 1.35, 2)
        self._slider(self.scan_frame, "扫描归一化强度", self.scan_strength, 0.00, 1.00, 3)
        self._slider(self.scan_frame, "滤色 R shift", self.print_shift_r, -0.12, 0.12, 4)
        self._slider(self.scan_frame, "滤色 G shift", self.print_shift_g, -0.12, 0.12, 5)
        self._slider(self.scan_frame, "滤色 B shift", self.print_shift_b, -0.12, 0.12, 6)
        self._slider(self.scan_frame, "高光绿偏", self.highlight_green, 0.85, 1.25, 7)
        self._slider(self.scan_frame, "高光蓝压制", self.highlight_blue, 0.75, 1.10, 8)
        ttk.Checkbutton(self.scan_frame, text="扫描归一化", variable=self.scan_normalize).grid(row=9, column=0, sticky="w", pady=4)

        buttons = ttk.Frame(self.root, padding=(12, 8))
        buttons.pack(fill="x", side="bottom")
        ttk.Button(buttons, text="预览当前阶段", command=self._start_preview).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ttk.Button(buttons, text="开始处理", command=self._start_process).pack(side="left", fill="x", expand=True, padx=(5, 0))

        status_bar = ttk.Frame(self.root, padding=(12, 0, 12, 8))
        status_bar.pack(fill="x", side="bottom")
        ttk.Label(status_bar, textvariable=self.status, wraplength=860).pack(fill="x")

        for frame in (self.paths_frame, self.develop_frame, self.scan_frame):
            frame.columnconfigure(1, weight=1)
            frame.columnconfigure(2, weight=1)

    def _bind_mousewheel(self, _event=None) -> None:
        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None) -> None:
        self.root.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event) -> None:
        if getattr(self, "scroll_canvas", None) is not None:
            self.scroll_canvas.yview_scroll(int(-event.delta / 120), "units")

    def _path_row(self, parent: ttk.Frame, label: str, var: tk.StringVar, file_cmd, folder_cmd, row: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        buttons = ttk.Frame(parent)
        buttons.grid(row=row, column=3, sticky="e", pady=3)
        if file_cmd is not None:
            ttk.Button(buttons, text="选文件", command=file_cmd).pack(side="left", padx=2)
        if folder_cmd is not None:
            ttk.Button(buttons, text="选文件夹", command=folder_cmd).pack(side="left", padx=2)

    def _slider(self, parent: ttk.Frame, label: str, var, min_value: float, max_value: float, row: int, integer: bool = False) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Scale(parent, from_=min_value, to=max_value, variable=var).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        value_text = tk.StringVar()

        def update_value(*_) -> None:
            value = var.get()
            value_text.set(str(int(value)) if integer else f"{value:.2f}")

        var.trace_add("write", update_value)
        update_value()
        ttk.Label(parent, textvariable=value_text, width=7).grid(row=row, column=3, sticky="e", pady=4)

    def _sync_film_preset_controls(self) -> None:
        path = FILM_PRESET_DIR / f"{self.film_preset.get()}.json"
        if not path.exists():
            return
        config = DarkroomConfig.from_json(path)
        self.exposure_ev.set(float(config.look.exposure_ev))
        self.negative_contrast.set(float(config.look.negative_contrast))
        self.halation.set(float(config.look.halation_multiplier))
        self.grain.set(float(config.look.grain_multiplier))
        self.grain_size.set(float(config.look.grain_size_multiplier))
        self.emulsion_mtf.set(float(config.look.emulsion_mtf_strength if config.look.emulsion_mtf_strength is not None else config.film.emulsion_mtf_strength))
        self.artifact_suppression.set(float(config.look.digital_artifact_suppression if config.look.digital_artifact_suppression is not None else config.film.digital_artifact_suppression))
        self.halation_edge.set(float(config.look.halation_edge_compensation if config.look.halation_edge_compensation is not None else config.film.halation_gradient_suppression))
        self.push.set(float(config.chemistry.push_stops))
        self.use_push.set(abs(float(config.chemistry.push_stops)) > 1e-6)
        self.force_bw.set(str(config.mode).lower() == "bw_negative")

    def _sync_scanner_preset_controls(self) -> None:
        path = SCANNER_PRESET_DIR / f"{self.scanner_preset.get()}.json"
        if not path.exists():
            return
        config = DarkroomConfig.from_json(path)
        self.print_contrast.set(float(config.look.print_contrast))
        self.print_exposure.set(float(config.look.print_exposure_ev))
        self.saturation.set(float(config.scanner.scan_saturation))
        self.scan_normalize.set(bool(config.scanner.scan_normalize))
        self.scan_strength.set(float(config.scanner.scan_normalize_strength))
        shift = tuple(float(v) for v in config.scanner.print_color_shift)
        self.print_shift_r.set(shift[0])
        self.print_shift_g.set(shift[1])
        self.print_shift_b.set(shift[2])
        highlight = tuple(float(v) for v in config.scanner.highlight_color_bias)
        self.highlight_green.set(highlight[1])
        self.highlight_blue.set(highlight[2])

    def _update_mode_visibility(self) -> None:
        mode = self.pipeline_mode.get()
        self.develop_frame.pack_forget()
        self.scan_frame.pack_forget()
        if mode in ("full", "develop"):
            self.develop_frame.pack(fill="x", pady=8)
        if mode in ("full", "scan"):
            self.scan_frame.pack(fill="x", pady=8)

        for widget in (self.film_preset_label, self.film_preset_combo):
            if mode in ("full", "develop"):
                widget.grid()
            else:
                widget.grid_remove()
        for widget in (self.scanner_preset_label, self.scanner_preset_combo):
            if mode in ("full", "scan"):
                widget.grid()
            else:
                widget.grid_remove()

        if mode == "develop":
            self.status.set("只冲洗模式：输入图片会被保存为 .npz 底片母版，不生成最终正像。")
        elif mode == "scan":
            self.status.set("只扫描模式：读取 .npz 底片，只显示和使用扫描/正像参数。")
        else:
            self.status.set("完整流程：输入图片会先冲洗成底片，再扫描成最终图。")

    def _choose_input_file(self) -> None:
        extensions = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        path = filedialog.askopenfilename(filetypes=[("Images", extensions), ("All files", "*.*")])
        if path:
            self.input_path.set(path)

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

    def _config(self) -> DarkroomConfig:
        mode = self.pipeline_mode.get()
        film_config = None
        scanner_config = None
        if mode in ("full", "develop"):
            film_config = DarkroomConfig.from_json(FILM_PRESET_DIR / f"{self.film_preset.get()}.json")
        if mode in ("full", "scan"):
            scanner_config = DarkroomConfig.from_json(SCANNER_PRESET_DIR / f"{self.scanner_preset.get()}.json")
        config = merge_config_presets(film_config, scanner_config)
        config.fast_mode = bool(self.fast_mode.get())
        config.look.exposure_ev = float(self.exposure_ev.get())
        config.look.print_contrast = float(self.print_contrast.get())
        config.look.print_exposure_ev = float(self.print_exposure.get())
        config.output.format = str(self.output_format.get())
        config.output.render_long_edge = None
        config.output.preview_long_edge = None if self.preview_long_edge.get() <= 0 else int(self.preview_long_edge.get())
        config.output.save_scanner_raw = bool(self.save_scanner_raw.get())
        config.output.scanner_raw_border_percent = float(self.scanner_raw_border.get()) / 100.0
        config.output.scanner_raw_border_min_px = 32
        config.output.export_layer_pack = bool(self.export_layer_pack.get())
        config.output.export_transparent_plate = bool(self.export_transparent_plate.get())
        config.output.export_plate_set = bool(self.export_plate_set.get())
        config.scanner.scan_normalize = bool(self.scan_normalize.get())
        config.scanner.scan_normalize_strength = float(self.scan_strength.get())
        config.scanner.scan_normalize_mode = "luma"
        config.enable_mtf = bool(self.enable_mtf.get())
        config.enable_halation = bool(self.enable_halation.get())
        config.enable_grain = bool(self.enable_grain.get())
        config.save_sidecar = bool(self.save_sidecar.get())
        if self.force_bw.get():
            config.mode = "bw_negative"
        config.debug_output = bool(self.debug_output.get())
        config.comparison_grid = bool(self.comparison_grid.get())
        config.look.negative_contrast = float(self.negative_contrast.get())
        config.scanner.scan_saturation = float(self.saturation.get())
        config.look.halation_multiplier = float(self.halation.get())
        config.look.grain_multiplier = float(self.grain.get())
        config.look.grain_size_multiplier = float(self.grain_size.get())
        config.look.emulsion_mtf_strength = float(self.emulsion_mtf.get())
        config.look.digital_artifact_suppression = float(self.artifact_suppression.get())
        config.look.halation_edge_compensation = float(self.halation_edge.get())
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
        if self.use_push.get():
            config.chemistry.push_stops = float(self.push.get())
        return config

    def _first_input_image(self) -> Path | None:
        inputs = iter_images(Path(self.input_path.get()))
        return inputs[0] if inputs else None

    def _first_negative_file(self) -> Path | None:
        negatives = iter_negative_files(Path(self.negative_path.get()))
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
        self.preview_window.title("效果预览")
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
        self.preview_window.title(f"效果预览 - {source_name}")
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

            self.root.after(0, lambda: self.status.set("正在生成预览..."))
            if mode in ("full", "develop"):
                input_path = self._first_input_image()
                if input_path is None:
                    self.root.after(0, lambda: self.status.set("没有找到输入图片。"))
                    return
                original = resize_to_long_edge(load_image(input_path), PREVIEW_PROCESS_LONG_EDGE)
                if mode == "develop":
                    negative = develop_negative(original, config, rng=rng_for_develop(input_path, config))
                    preview = negative_visual_preview(negative.density_grain, config.film)
                    self.root.after(0, lambda: self._show_preview(input_path.name, original, preview, "输入图像", "负片外观预览"))
                else:
                    result = process_array(original, config)
                    self.root.after(0, lambda: self._show_preview(input_path.name, original, result, "输入图像", "最终正像"))
                return

            negative_path = self._first_negative_file()
            if negative_path is None:
                self.root.after(0, lambda: self.status.set("没有找到 .npz 底片文件，请先运行只冲洗模式。"))
                return
            scanned, scan_source, preview = scan_from_file(negative_path, config)
            self.root.after(0, lambda: self._show_preview(negative_path.name, preview, scanned.output_srgb, "负片外观预览", "扫描正像"))
        except Exception as exc:
            message = str(exc)
            self.root.after(0, lambda: self.status.set(f"预览失败：{message}"))

    def _start_process(self) -> None:
        Thread(target=self._process, daemon=True).start()

    def _process(self) -> None:
        try:
            mode = self.pipeline_mode.get()
            config = self._config()
            output_root = Path(self.output_path.get())
            negative_root = Path(self.negative_path.get())
            self.status.set("处理中，请稍等...")

            if mode == "full":
                inputs = iter_images(Path(self.input_path.get()))
                if not inputs:
                    self.status.set("没有找到输入图片。")
                    return
                for input_path in inputs:
                    output_path = output_path_for(input_path, output_root, config.output.format)
                    process_file(input_path, output_path, config, preview=bool(self.run_as_preview.get()))
                self.status.set(f"完整流程完成：{len(inputs)} 张最终图已保存到 {output_root}")
                return

            if mode == "develop":
                inputs = iter_images(Path(self.input_path.get()))
                if not inputs:
                    self.status.set("没有找到输入图片。")
                    return
                for input_path in inputs:
                    image = load_image(input_path)
                    long_edge = config.output.preview_long_edge if self.run_as_preview.get() else config.output.render_long_edge
                    image = resize_to_long_edge(image, long_edge)
                    negative = develop_negative(image, config, rng=rng_for_develop(input_path, config))
                    save_negative(
                        negative,
                        negative_path_for(input_path, negative_root),
                        input_path,
                        config,
                        bool(self.save_sidecar.get()),
                    )
                self.status.set(f"冲洗完成：{len(inputs)} 个 .npz 底片已保存到 {negative_root}")
                return

            negative_paths = iter_negative_files(negative_root)
            if not negative_paths:
                self.status.set("没有找到 .npz 底片文件，请先运行只冲洗模式。")
                return
            for negative_path in negative_paths:
                scanned, scan_source, _preview = scan_from_file(negative_path, config)
                output_path = output_path_for(negative_path, output_root, config.output.format)
                save_image(scanned.output_srgb, output_path, config.output)
                if self.save_sidecar.get():
                    save_json(
                        output_path.with_suffix(output_path.suffix + ".json"),
                        {
                            "kind": "ScannedPositive",
                            "created_at": datetime.now().isoformat(timespec="seconds"),
                            "negative_path": str(negative_path),
                            "scan_source": scan_source,
                            "output_path": str(output_path),
                            "config": asdict(config),
                        },
                    )
            self.status.set(f"扫描完成：{len(negative_paths)} 张正像已保存到 {output_root}")
        except Exception as exc:
            self.status.set(f"处理失败：{exc}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DarkroomPanel().run()
