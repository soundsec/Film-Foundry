"""Standalone scanner/render preset editor for Film Foundry.

The default entry edits negative scans. ``PositiveScannerEditor`` reuses the
same widgets for the distinct positive-transparency parameter package.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from half_frame_darkroom.core.atomic_io import atomic_write_json
from half_frame_darkroom.core.scanner import (
    normalize_scan_rgb,
    reconstruct_negative_channels,
    render_positive_scan,
    render_positive_transparency_scan,
)
from half_frame_darkroom.model.config import SCAN_LOOK_FIELDS, DarkroomConfig
from film_foundry.tools.editor_ui import localize_widget_tree, localized_preset_name, ui, unique_choice_map
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "half_frame_darkroom" / "presets"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
USER_SCANNER_PRESET_DIR = PROJECT_ROOT / "user_presets" / "scanner"
APP_TITLE = "Film Foundry - Negative Scanner Render Editor"
SUPPORTED_INTERPRETER_KEY = "negative_scan"
SUPPORTED_TARGET_MEDIUM_PROCESS = "negative"
SUPPORTED_INPUT_POLARITY = "negative"
SUPPORTED_OUTPUT_POLARITY = "positive"

SCANNER_EDITOR_EN = {
    "透射扫描 / 解释预设": "Transmission Scan / Interpretation Presets",
    "解释模式": "Interpretation Mode",
    "共享透射采样；这里只切换去罩/反相或正片观看解释。": "Shared transmission sampling; this switches only between mask removal/inversion and positive-transparency viewing.",
    "基准解释预设": "Base interpretation preset",
    "加载": "Load",
    "介质解释": "Medium interpretation",
    "共享透射采样": "Shared Transmission Sampling",
    "扫描光源 RGB": "Scanner Light RGB",
    "扫描器 RGB 响应矩阵": "Scanner RGB Response Matrix",
    "透射光源亮度 EV": "Transmission Light EV",
    "透射光源色温 K": "Transmission Light Temperature K",
    "正片观看解释": "Positive Viewing Interpretation",
    "负片去罩 / 反相 / 通道重建": "Negative Mask Removal / Inversion / Channel Reconstruction",
    "解释方法": "Interpretation Method",
    "启用负片蓝绿 / 染料通道补偿": "Enable Negative Blue-Green / Dye-Channel Compensation",
    "正片滤色控制强度": "Positive Color-Control Strength",
    "负片通道补偿强度": "Negative Channel Compensation Strength",
    "外部 raw 片基估计 percentile（兜底）": "External Raw Base Estimate Percentile (fallback)",
    "负片通道重建矩阵": "Negative Channel Reconstruction Matrix",
    "负片通道曲线 RGB": "Negative Channel Gamma RGB",
    "输出影调 / 滤色": "Output Tone / Filtration",
    "参考密度 RGB": "Reference Density RGB",
    "打印反差倍率": "Print Contrast",
    "打印曝光 EV": "Print Exposure EV",
    "映射曲线": "Mapping Curve",
    "log 域滤色 shift RGB": "Log-Domain Color Shift RGB",
    "RGB 增益 bias": "RGB Gain Bias",
    "高光 / 饱和 / 黑白点": "Highlights / Saturation / Black and White Points",
    "高光偏色 RGB": "Highlight Color Bias RGB",
    "高光偏色阈值": "Highlight Bias Threshold",
    "高光偏色软化": "Highlight Bias Softness",
    "白点软滚降": "White-Point Soft Rolloff",
    "黑位观看适应": "Black-Level Viewing Adaptation",
    "扫描饱和度": "Scan Saturation",
    "扫描黑白点归一化": "Normalize Scan Black / White Points",
    "归一化强度": "Normalization Strength",
    "黑点 percentile": "Black Percentile",
    "白点 percentile": "White Percentile",
    "正片透射扫描预览": "Positive Transmission Scan Preview",
    "scan/render 曲线预览": "Scan / Render Curve Preview",
    "刷新曲线": "Refresh Curve",
}


def safe_preset_stem(name: str) -> str:
    stem = []
    for char in name.strip().lower():
        if char.isalnum() or char in {"_", "-"}:
            stem.append(char)
        elif char.isspace():
            stem.append("_")
    return "".join(stem).strip("_-")


def is_supported_negative_scanner(config: DarkroomConfig) -> bool:
    scanner = config.scanner
    return (
        str(scanner.interpreter_key).lower() in {"", SUPPORTED_INTERPRETER_KEY}
        and str(scanner.target_medium_process).lower() in {"", SUPPORTED_TARGET_MEDIUM_PROCESS}
        and str(scanner.input_polarity).lower() in {"", SUPPORTED_INPUT_POLARITY}
        and str(scanner.output_polarity).lower() in {"", SUPPORTED_OUTPUT_POLARITY}
    )


def is_supported_positive_scanner(config: DarkroomConfig) -> bool:
    scanner = config.scanner
    return (
        str(scanner.interpreter_key).lower() == "positive_transparency_scan"
        and str(scanner.input_polarity).lower() == "positive"
        and str(scanner.output_polarity).lower() == "positive"
    )


def scanner_preset_names(interpretation: str = "negative") -> list[str]:
    names = {path.stem for path in SCANNER_PRESET_DIR.glob("*.json")}
    names.update(path.stem for path in USER_SCANNER_PRESET_DIR.glob("*.json"))
    supported: list[str] = []
    for name in sorted(names):
        path = scanner_preset_path(name)
        try:
            config = DarkroomConfig.from_json(path)
            supported_config = is_supported_positive_scanner(config) if interpretation == "positive" else is_supported_negative_scanner(config)
            if supported_config:
                supported.append(name)
        except Exception:
            continue
    return supported


def scanner_preset_path(name: str) -> Path:
    user_path = USER_SCANNER_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return SCANNER_PRESET_DIR / f"{name}.json"


def scanner_preset_choices(interpretation: str = "negative") -> tuple[list[str], dict[str, str]]:
    items: list[tuple[str, str]] = []
    for key in scanner_preset_names(interpretation):
        path = scanner_preset_path(key)
        fallback = key.replace("_", " ").title()
        items.append((key, localized_preset_name("scanner", key, fallback, selected_path=path, builtin_dir=SCANNER_PRESET_DIR)))
    return unique_choice_map(items)


def save_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


class ScannerRenderEditor:
    def __init__(self, interpretation: str = "negative") -> None:
        USER_SCANNER_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self._set_interpretation_state(interpretation)

        self.root = tk.Tk()
        self.root.title(ui("Film Foundry - 透射扫描 / 解释编辑器", "Film Foundry - Transmission Scanner / Interpreter Editor"))
        self.root.geometry("1040x760")
        self.root.minsize(840, 620)

        self.interpretation_display = tk.StringVar(value=self._interpretation_label())
        self.preset_name = tk.StringVar(value="positive_transparency_scan" if self.is_positive else "neutral_scan")
        self.preset_display = tk.StringVar(value="")
        self.preset_display_to_key: dict[str, str] = {}
        self.target_medium_process = tk.StringVar(value=self.target_process)
        self.input_polarity = tk.StringVar(value=self.input_polarity_value)
        self.output_polarity = tk.StringVar(value="positive")
        self.scan_method = tk.StringVar(value="positive_transparency" if self.is_positive else "negative_inversion")
        self.print_mapping_mode = tk.StringVar(value="printlike")
        self.scan_normalize = tk.BooleanVar(value=True)
        self.negative_channel_compensation = tk.BooleanVar(value=False)
        self.scan_normalize_mode = tk.StringVar(value="luma")
        self.status = tk.StringVar(value=ui(
            "加载正片扫描预设，调整后保存到 user_presets/scanner。" if self.is_positive else "加载负片扫描预设，调整后保存到 user_presets/scanner。",
            "Load a positive scan preset, adjust it, then save to user_presets/scanner." if self.is_positive else "Load a negative scan preset, adjust it, then save to user_presets/scanner.",
        ))
        self.curve_photo = None

        self.vars: dict[str, tk.DoubleVar] = {
            "scan_base_percentile": tk.DoubleVar(value=99.5),
            "print_gamma": tk.DoubleVar(value=0.95),
            "print_contrast": tk.DoubleVar(value=1.10),
            "print_exposure_ev": tk.DoubleVar(value=0.0),
            "scan_saturation": tk.DoubleVar(value=1.0),
            "scan_normalize_strength": tk.DoubleVar(value=0.15),
            "scan_black_percentile": tk.DoubleVar(value=0.3),
            "scan_white_percentile": tk.DoubleVar(value=99.7),
            "highlight_bias_threshold": tk.DoubleVar(value=0.72),
            "highlight_bias_softness": tk.DoubleVar(value=0.18),
            "negative_backlight_ev": tk.DoubleVar(value=0.0),
            "negative_backlight_temperature_k": tk.DoubleVar(value=5500.0),
            "negative_channel_compensation_strength": tk.DoubleVar(value=0.35),
            "light_table_ev": tk.DoubleVar(value=0.0),
            "light_table_temperature_k": tk.DoubleVar(value=5400.0),
            "positive_scan_color_control_strength": tk.DoubleVar(value=0.25),
            "projection_white_softness": tk.DoubleVar(value=0.22),
            "projection_black_adaptation": tk.DoubleVar(value=0.10),
        }
        self.vector_vars: dict[str, tuple[tk.DoubleVar, tk.DoubleVar, tk.DoubleVar]] = {
            "scanner_light_color": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
            "negative_channel_gamma": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
            "print_reference_density": (tk.DoubleVar(value=1.58), tk.DoubleVar(value=1.61), tk.DoubleVar(value=1.53)),
            "print_color_shift": (tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0)),
            "print_color_bias": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
            "highlight_color_bias": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
        }
        self.matrix_vars: dict[str, list[list[tk.DoubleVar]]] = {
            "scanner_response_matrix": [[tk.DoubleVar(value=1.0 if x == y else 0.0) for x in range(3)] for y in range(3)],
            "negative_channel_matrix": [[tk.DoubleVar(value=1.0 if x == y else 0.0) for x in range(3)] for y in range(3)],
        }

        self._build()
        self._refresh_preset_choices()
        self._load_selected_preset()

    def _set_interpretation_state(self, interpretation: str) -> None:
        self.is_positive = str(interpretation).lower() == "positive"
        self.interpretation = "positive" if self.is_positive else "negative"
        self.interpreter_key = "positive_transparency_scan" if self.is_positive else SUPPORTED_INTERPRETER_KEY
        self.target_process = "positive" if self.is_positive else SUPPORTED_TARGET_MEDIUM_PROCESS
        self.input_polarity_value = "positive" if self.is_positive else SUPPORTED_INPUT_POLARITY

    def _interpretation_label(self) -> str:
        return ui("正片解释", "Positive Interpretation") if self.is_positive else ui("负片解释", "Negative Interpretation")

    def _on_interpretation_selected(self, _event=None) -> None:
        selected = "positive" if self.interpretation_display.get() == ui("正片解释", "Positive Interpretation") else "negative"
        if selected == self.interpretation:
            return
        self._set_interpretation_state(selected)
        self.preset_name.set("positive_transparency_scan" if self.is_positive else "neutral_scan")
        self.target_medium_process.set(self.target_process)
        self.input_polarity.set(self.input_polarity_value)
        self.output_polarity.set("positive")
        self.scan_method.set("positive_transparency" if self.is_positive else "negative_inversion")
        for child in self.root.winfo_children():
            child.destroy()
        self._build()
        self._refresh_preset_choices()
        self._load_selected_preset()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(shell, text="透射扫描 / 解释预设", padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="解释模式").grid(row=0, column=0, sticky="w", pady=3)
        interpretation_combo = ttk.Combobox(
            header,
            textvariable=self.interpretation_display,
            values=(ui("负片解释", "Negative Interpretation"), ui("正片解释", "Positive Interpretation")),
            state="readonly",
            width=24,
        )
        interpretation_combo.grid(row=0, column=1, sticky="w", padx=(8, 8), pady=3)
        interpretation_combo.bind("<<ComboboxSelected>>", self._on_interpretation_selected)
        ttk.Label(
            header,
            text="共享透射采样；这里只切换去罩/反相或正片观看解释。",
            wraplength=500,
        ).grid(row=0, column=2, columnspan=2, sticky="w", pady=3)

        ttk.Label(header, text="基准解释预设").grid(row=1, column=0, sticky="w", pady=3)
        self.preset_combo = ttk.Combobox(header, textvariable=self.preset_display, state="readonly")
        self.preset_combo.grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=3)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(header, text="加载", command=self._load_selected_preset).grid(row=1, column=2, padx=(0, 8))
        ttk.Button(header, text=(ui("保存为用户正片解释", "Save User Positive Interpretation") if self.is_positive else ui("保存为用户负片解释", "Save User Negative Interpretation")), command=self._save_user_preset).grid(row=1, column=3)
        ttk.Label(header, text="介质解释").grid(row=2, column=0, sticky="w", pady=3)
        medium_row = ttk.Frame(header)
        medium_row.grid(row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)
        identity = ui("正片透明片扫描 / 正片输入", "Positive transparency scan / positive input") if self.is_positive else ui("负片扫描 / 负片输入 / 正像输出", "Negative scan / negative input / positive output")
        ttk.Label(medium_row, text=identity).pack(side="left", padx=(0, 10))
        ttk.Label(
            medium_row,
            text=(ui("当前编辑器只保存正片透明片扫描预设。", "This editor saves positive-transparency scan presets only.") if self.is_positive else ui("当前编辑器只保存负片扫描预设。", "This editor saves negative-scan presets only.")),
            wraplength=520,
        ).pack(side="left")
        header.columnconfigure(1, weight=1)

        controls_shell = ttk.Frame(shell)
        controls_shell.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        controls_shell.rowconfigure(0, weight=1)
        controls_shell.columnconfigure(0, weight=1)
        canvas = tk.Canvas(controls_shell, highlightthickness=0, width=560)
        scrollbar = ttk.Scrollbar(controls_shell, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        controls = ttk.Frame(canvas, padding=(0, 0, 8, 0))
        window = canvas.create_window((0, 0), window=controls, anchor="nw")
        controls.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.bind("<Enter>", lambda _event: self.root.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units")))
        canvas.bind("<Leave>", lambda _event: self.root.unbind_all("<MouseWheel>"))

        row = 0
        row = self._section(controls, "共享透射采样", row)
        row = self._vector(controls, "scanner_light_color", "扫描光源 RGB", 0.50, 1.50, row)
        row = self._matrix(controls, "scanner_response_matrix", "扫描器 RGB 响应矩阵", -0.25, 1.50, row)
        if self.is_positive:
            row = self._slider(controls, "透射光源亮度 EV", "light_table_ev", -3.0, 3.0, row)
            row = self._slider(controls, "透射光源色温 K", "light_table_temperature_k", 2800.0, 9000.0, row)
        else:
            row = self._slider(controls, "透射光源亮度 EV", "negative_backlight_ev", -3.0, 3.0, row)
            row = self._slider(controls, "透射光源色温 K", "negative_backlight_temperature_k", 2800.0, 9000.0, row)

        row = self._section(controls, ("正片观看解释" if self.is_positive else "负片去罩 / 反相 / 通道重建"), row)
        scan_methods = ("positive_transparency",) if self.is_positive else ("negative_inversion",)
        row = self._combo(controls, "解释方法", self.scan_method, scan_methods, row)
        if self.is_positive:
            row = self._slider(controls, "正片滤色控制强度", "positive_scan_color_control_strength", 0.0, 1.0, row)
        else:
            compensation_check = ttk.Checkbutton(
                controls,
                text="启用负片蓝绿 / 染料通道补偿",
                variable=self.negative_channel_compensation,
                command=self._refresh_curve,
            )
            compensation_check.grid(row=row, column=0, columnspan=4, sticky="w", pady=4)
            row += 1
            row = self._slider(
                controls,
                "负片通道补偿强度",
                "negative_channel_compensation_strength",
                0.0,
                1.0,
                row,
            )
            row = self._slider(controls, "外部 raw 片基估计 percentile（兜底）", "scan_base_percentile", 90.0, 100.0, row)
            row = self._matrix(controls, "negative_channel_matrix", "负片通道重建矩阵", -0.35, 1.50, row)
            row = self._vector(controls, "negative_channel_gamma", "负片通道曲线 RGB", 0.65, 1.45, row)

        row = self._section(controls, "输出影调 / 滤色", row)
        if not self.is_positive:
            row = self._vector(controls, "print_reference_density", "参考密度 RGB", 0.60, 2.40, row)
        row = self._slider(controls, "print gamma", "print_gamma", 0.45, 1.60, row)
        row = self._slider(controls, "打印反差倍率", "print_contrast", 0.60, 1.80, row)
        row = self._slider(controls, "打印曝光 EV", "print_exposure_ev", -2.0, 2.0, row)
        if not self.is_positive:
            row = self._combo(controls, "映射曲线", self.print_mapping_mode, ("printlike", "sigmoid"), row)
        row = self._vector(controls, "print_color_shift", "log 域滤色 shift RGB", -0.18, 0.18, row)
        row = self._vector(controls, "print_color_bias", "RGB 增益 bias", 0.75, 1.25, row)

        row = self._section(controls, "高光 / 饱和 / 黑白点", row)
        row = self._vector(controls, "highlight_color_bias", "高光偏色 RGB", 0.75, 1.25, row)
        row = self._slider(controls, "高光偏色阈值", "highlight_bias_threshold", 0.30, 0.95, row)
        row = self._slider(controls, "高光偏色软化", "highlight_bias_softness", 0.02, 0.45, row)
        if self.is_positive:
            row = self._slider(controls, "白点软滚降", "projection_white_softness", 0.0, 0.75, row)
            row = self._slider(controls, "黑位观看适应", "projection_black_adaptation", 0.0, 0.75, row)
        row = self._slider(controls, "扫描饱和度", "scan_saturation", 0.40, 1.80, row)
        ttk.Checkbutton(controls, text="扫描黑白点归一化", variable=self.scan_normalize, command=self._refresh_curve).grid(
            row=row, column=0, sticky="w", pady=4
        )
        ttk.Combobox(controls, textvariable=self.scan_normalize_mode, values=("luma", "rgb"), state="readonly", width=8).grid(
            row=row, column=1, sticky="w", pady=4
        )
        row += 1
        row = self._slider(controls, "归一化强度", "scan_normalize_strength", 0.0, 1.0, row)
        row = self._slider(controls, "黑点 percentile", "scan_black_percentile", 0.0, 5.0, row)
        row = self._slider(controls, "白点 percentile", "scan_white_percentile", 95.0, 100.0, row)

        preview = ttk.LabelFrame(shell, text=("正片透射扫描预览" if self.is_positive else "scan/render 曲线预览"), padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.curve_label = ttk.Label(preview)
        self.curve_label.grid(row=0, column=0, sticky="nsew")
        ttk.Button(preview, text="刷新曲线", command=self._refresh_curve).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=960).pack(fill="x", side="bottom")
        localize_widget_tree(self.root, SCANNER_EDITOR_EN)

    def _refresh_preset_choices(self) -> None:
        labels, mapping = scanner_preset_choices(self.interpretation)
        self.preset_display_to_key = mapping
        self.preset_combo.configure(values=labels)
        key = self.preset_name.get()
        self.preset_display.set(next((label for label, value in mapping.items() if value == key), key))

    def _on_preset_selected(self, _event=None) -> None:
        self.preset_name.set(self.preset_display_to_key.get(self.preset_display.get(), self.preset_display.get()))

    def _section(self, parent: ttk.Frame, text: str, row: int) -> int:
        ttk.Label(parent, text=text, font=("", 10, "bold")).grid(row=row, column=0, columnspan=5, sticky="w", pady=(12, 4))
        return row + 1

    def _combo(self, parent: ttk.Frame, label: str, var: tk.StringVar, values: tuple[str, ...], row: int) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly").grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
        var.trace_add("write", lambda *_: self._refresh_curve())
        return row + 1

    def _slider(self, parent: ttk.Frame, label: str, key: str, min_value: float, max_value: float, row: int) -> int:
        var = self.vars[key]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Scale(parent, from_=min_value, to=max_value, variable=var, command=lambda _value: self._refresh_curve()).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=4
        )
        ttk.Spinbox(
            parent,
            from_=min_value,
            to=max_value,
            increment=max((max_value - min_value) / 200.0, 0.001),
            textvariable=var,
            width=9,
            justify="right",
            command=self._refresh_curve,
        ).grid(row=row, column=3, sticky="e", pady=4)
        var.trace_add("write", lambda *_: self._refresh_curve())
        parent.columnconfigure(1, weight=1)
        return row + 1

    def _vector(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        vars_ = self.vector_vars[key]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)

        vector_frame = ttk.Frame(parent)
        vector_frame.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
        for index, (channel, var) in enumerate(zip(("R", "G", "B"), vars_)):
            cell = ttk.Frame(vector_frame)
            cell.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            cell.columnconfigure(1, weight=1)
            ttk.Label(cell, text=channel, width=2, anchor="center").grid(row=0, column=0, sticky="w", padx=(0, 3))
            spin = ttk.Spinbox(
                cell,
                from_=min_value,
                to=max_value,
                increment=(max_value - min_value) / 200.0,
                textvariable=var,
                width=9,
                justify="center",
            )
            spin.grid(row=0, column=1, sticky="ew")
            vector_frame.columnconfigure(index, weight=1, uniform="rgb_vector")
            var.trace_add("write", lambda *_: self._refresh_curve())
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)
        parent.columnconfigure(3, weight=1)
        return row + 1

    def _matrix(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        matrix = self.matrix_vars[key]
        matrix_frame = ttk.Frame(parent)
        matrix_frame.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
        for x, channel in enumerate(("R", "G", "B"), start=1):
            ttk.Label(matrix_frame, text=channel, anchor="center").grid(row=0, column=x, sticky="ew", padx=2)
            matrix_frame.columnconfigure(x, weight=1, uniform="rgb_matrix")
        for y in range(3):
            ttk.Label(matrix_frame, text=("R", "G", "B")[y], width=2, anchor="center").grid(row=y + 1, column=0, sticky="ew", padx=(0, 3))
            for x in range(3):
                var = matrix[y][x]
                spin = ttk.Spinbox(
                    matrix_frame,
                    from_=min_value,
                    to=max_value,
                    increment=(max_value - min_value) / 200.0,
                    textvariable=var,
                    width=9,
                    justify="center",
                )
                spin.grid(row=y + 1, column=x + 1, sticky="ew", padx=2, pady=2)
                var.trace_add("write", lambda *_: self._refresh_curve())
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)
        parent.columnconfigure(3, weight=1)
        return row + 1

    def _load_selected_preset(self) -> None:
        path = scanner_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(ui(f"找不到扫描预设：{path}", f"Scan preset not found: {path}"))
            return
        config = DarkroomConfig.from_json(path)
        supported = is_supported_positive_scanner(config) if self.is_positive else is_supported_negative_scanner(config)
        if not supported:
            kind = ui("正片透明片", "positive-transparency") if self.is_positive else ui("负片", "negative")
            self.status.set(ui(f"当前编辑器只支持{kind}扫描预设：{path.name}", f"The current editor supports {kind} scan presets only: {path.name}"))
            return
        self._set_controls_from_config(config)
        source = "用户" if path.parent == USER_SCANNER_PRESET_DIR else "内置"
        self.status.set(ui(
            f"已加载{source}扫描预设：{path.name}",
            f"Loaded {'user' if path.parent == USER_SCANNER_PRESET_DIR else 'built-in'} scan preset: {path.name}",
        ))
        self._refresh_curve()

    def _set_controls_from_config(self, config: DarkroomConfig) -> None:
        scanner = config.scanner
        self.target_medium_process.set(str(scanner.target_medium_process))
        self.input_polarity.set(str(scanner.input_polarity))
        self.output_polarity.set(str(scanner.output_polarity))
        self.scan_method.set(str(scanner.scan_method))
        self.print_mapping_mode.set(str(scanner.print_mapping_mode))
        self.scan_normalize.set(bool(scanner.scan_normalize))
        self.negative_channel_compensation.set(
            bool(scanner.negative_channel_compensation_enabled)
        )
        self.scan_normalize_mode.set(str(scanner.scan_normalize_mode))
        for key in self.vars:
            if key in SCAN_LOOK_FIELDS:
                self.vars[key].set(float(getattr(config.look, key)))
            else:
                self.vars[key].set(float(getattr(scanner, key)))
        for key, vars_ in self.vector_vars.items():
            values = getattr(scanner, key)
            for var, value in zip(vars_, values):
                var.set(float(value))
        for key, vars_ in self.matrix_vars.items():
            matrix = getattr(scanner, key)
            for y in range(3):
                for x in range(3):
                    vars_[y][x].set(float(matrix[y][x]))

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = scanner_preset_path(self.preset_name.get())
        config = DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig()
        scanner = config.scanner
        scanner.interpretation_mode = self.interpretation
        scanner.interpreter_key = self.interpreter_key
        scanner.target_medium_process = self.target_process
        scanner.input_polarity = self.input_polarity_value
        scanner.output_polarity = SUPPORTED_OUTPUT_POLARITY
        scanner.scan_method = str(self.scan_method.get())
        scanner.print_mapping_mode = str(self.print_mapping_mode.get())
        scanner.scan_normalize = bool(self.scan_normalize.get())
        scanner.negative_channel_compensation_enabled = bool(
            self.negative_channel_compensation.get()
        )
        scanner.scan_normalize_mode = str(self.scan_normalize_mode.get())
        for key, var in self.vars.items():
            if key in SCAN_LOOK_FIELDS:
                setattr(config.look, key, float(var.get()))
            else:
                setattr(scanner, key, float(var.get()))
        for key, vars_ in self.vector_vars.items():
            setattr(scanner, key, tuple(float(var.get()) for var in vars_))
        for key, vars_ in self.matrix_vars.items():
            setattr(scanner, key, tuple(tuple(float(vars_[y][x].get()) for x in range(3)) for y in range(3)))
        return config

    def _preset_payload(self, config: DarkroomConfig) -> dict:
        look = {field_name: getattr(config.look, field_name) for field_name in SCAN_LOOK_FIELDS}
        return {
            "scanner": asdict(config.scanner),
            "look": look,
            "enable_subtractive": bool(config.enable_subtractive),
        }

    def _refresh_curve(self) -> None:
        if not hasattr(self, "curve_label"):
            return
        try:
            config = self._config_from_controls()
            image = self._draw_scan_curve(config)
            self.curve_photo = ImageTk.PhotoImage(image)
            self.curve_label.configure(image=self.curve_photo)
        except Exception:
            return

    def _draw_scan_curve(self, config: DarkroomConfig) -> Image.Image:
        width, height = 470, 360
        pad_l, pad_t, pad_r, pad_b = 54, 34, 20, 44
        x0, y0, x1, y1 = pad_l, pad_t, width - pad_r, height - pad_b
        image = Image.new("RGB", (width, height), (252, 250, 246))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width - 1, height - 1), fill=(252, 250, 246), outline=(222, 218, 208))
        draw.rectangle((x0, y0, x1, y1), outline=(90, 86, 78))
        for i in range(1, 5):
            x = x0 + (x1 - x0) * i / 5
            y = y0 + (y1 - y0) * i / 5
            draw.line((x, y0, x, y1), fill=(226, 222, 212))
            draw.line((x0, y, x1, y), fill=(226, 222, 212))

        if self.is_positive:
            raw_d = np.linspace(0.0, 1.0, 420, dtype=np.float32)
            positive_raw = np.repeat(raw_d[:, None, None], 3, axis=2)
            mapped = render_positive_transparency_scan(
                positive_raw,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
            )[:, 0, :]
            input_max = 1.0
        else:
            raw_d = np.linspace(0.0, 2.6, 420, dtype=np.float32)
            positive_raw = np.repeat(raw_d[:, None, None], 3, axis=2)
            positive_raw = reconstruct_negative_channels(positive_raw, config.scanner)
            mapped = render_positive_scan(
                positive_raw,
                config.scanner,
                print_contrast=config.look.print_contrast,
                print_exposure_ev=config.look.print_exposure_ev,
            )[:, 0, :]
            input_max = 2.6
        if config.scanner.scan_normalize:
            curve_image = np.repeat(mapped[None, :, :], 12, axis=0)
            mapped = normalize_scan_rgb(
                curve_image,
                black_percentile=config.scanner.scan_black_percentile,
                white_percentile=config.scanner.scan_white_percentile,
                strength=config.scanner.scan_normalize_strength,
                mode=config.scanner.scan_normalize_mode,
            )[0]

        colors = ((220, 70, 70), (70, 170, 85), (70, 110, 220))
        for channel, color in enumerate(colors):
            xs = x0 + raw_d / input_max * (x1 - x0)
            ys = y1 - np.clip(mapped[:, channel], 0.0, 1.0) * (y1 - y0)
            points = [(int(round(x)), int(round(y))) for x, y in zip(xs, ys)]
            draw.line(points, fill=color, width=3)

        title = "positive transparency" if self.is_positive else f"mapping={config.scanner.print_mapping_mode}"
        draw.text((x0, 12), f"{title}, norm={config.scanner.scan_normalize_mode}", fill=(42, 38, 34))
        draw.text((x0, height - 29), ("transmission" if self.is_positive else "raw positive density"), fill=(70, 66, 58))
        draw.text((12, y0 - 4), "positive", fill=(70, 66, 58))
        return image

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(self.preset_name.get()) or "custom_scan"
        name = simpledialog.askstring(
            ui("保存扫描预设", "Save Scan Preset"),
            ui("请输入扫描预设文件名（建议英文、数字、下划线）：", "Enter a scan preset filename (letters, numbers, and underscores recommended):"),
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            messagebox.showerror(ui("无法保存", "Cannot Save"), ui("预设文件名不能为空，也不能只包含特殊字符。", "The preset filename cannot be empty or contain only special characters."), parent=self.root)
            return
        path = USER_SCANNER_PRESET_DIR / f"{stem}.json"
        existing = scanner_preset_path(stem)
        if existing.exists():
            if existing.parent == SCANNER_PRESET_DIR and not path.exists():
                detail = ui(
                    f"{stem}.json 是内置扫描预设。保存后会创建同名用户扫描预设，并在 GUI/CLI 中优先使用。",
                    f"{stem}.json is built in. Saving creates a same-name user scan preset that takes precedence in the GUI and CLI.",
                )
            else:
                detail = ui(f"{path.name} 已存在。", f"{path.name} already exists.")
            if not messagebox.askyesno(ui("覆盖扫描预设", "Overwrite Scan Preset"), f"{detail}\n{ui('是否继续？', 'Continue?')}", parent=self.root):
                return
        save_json(path, self._preset_payload(config))
        self.preset_name.set(stem)
        self._refresh_preset_choices()
        self.status.set(ui(f"已保存用户扫描预设：{path}", f"Saved user scan preset: {path}"))

    def run(self) -> None:
        self.root.mainloop()


class PositiveScannerEditor(ScannerRenderEditor):
    """Positive-transparency scanner preset editor."""

    def __init__(self) -> None:
        super().__init__(interpretation="positive")


if __name__ == "__main__":
    ScannerRenderEditor().run()
