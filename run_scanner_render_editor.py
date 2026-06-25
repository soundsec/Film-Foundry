"""Standalone scanner/render preset editor for Film Foundry.

Run this file directly in an IDE to edit scanner interpretation presets.  It
saves custom scanner presets to user_presets/scanner so the main GUI/CLI can
use them without modifying bundled presets.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import numpy as np
from PIL import Image, ImageDraw, ImageTk

from half_frame_darkroom.core.scanner import normalize_scan_rgb, render_positive_scan
from half_frame_darkroom.model.config import SCAN_LOOK_FIELDS, DarkroomConfig


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    return Path(__file__).resolve().parent


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "half_frame_darkroom" / "presets"
SCANNER_PRESET_DIR = PRESET_DIR / "scanner"
USER_SCANNER_PRESET_DIR = PROJECT_ROOT / "user_presets" / "scanner"
APP_TITLE = "Film Foundry - Scanner Render Editor"


def safe_preset_stem(name: str) -> str:
    stem = []
    for char in name.strip().lower():
        if char.isalnum() or char in {"_", "-"}:
            stem.append(char)
        elif char.isspace():
            stem.append("_")
    return "".join(stem).strip("_-")


def scanner_preset_names() -> list[str]:
    names = {path.stem for path in SCANNER_PRESET_DIR.glob("*.json")}
    names.update(path.stem for path in USER_SCANNER_PRESET_DIR.glob("*.json"))
    return sorted(names)


def scanner_preset_path(name: str) -> Path:
    user_path = USER_SCANNER_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return SCANNER_PRESET_DIR / f"{name}.json"


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


class ScannerRenderEditor:
    def __init__(self) -> None:
        USER_SCANNER_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1040x760")
        self.root.minsize(840, 620)

        self.preset_name = tk.StringVar(value="neutral_scan")
        self.target_medium_process = tk.StringVar(value="negative")
        self.input_polarity = tk.StringVar(value="negative")
        self.output_polarity = tk.StringVar(value="positive")
        self.scan_method = tk.StringVar(value="negative_inversion")
        self.print_mapping_mode = tk.StringVar(value="printlike")
        self.scan_normalize = tk.BooleanVar(value=True)
        self.scan_normalize_mode = tk.StringVar(value="luma")
        self.status = tk.StringVar(value="加载扫描 preset，调整后保存到 user_presets/scanner。")
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
        }
        self.vector_vars: dict[str, tuple[tk.DoubleVar, tk.DoubleVar, tk.DoubleVar]] = {
            "scanner_light_color": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
            "print_reference_density": (tk.DoubleVar(value=1.58), tk.DoubleVar(value=1.61), tk.DoubleVar(value=1.53)),
            "print_color_shift": (tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0)),
            "print_color_bias": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
            "highlight_color_bias": (tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0), tk.DoubleVar(value=1.0)),
        }
        self.matrix_vars: dict[str, list[list[tk.DoubleVar]]] = {
            "scanner_response_matrix": [[tk.DoubleVar(value=1.0 if x == y else 0.0) for x in range(3)] for y in range(3)]
        }

        self._build()
        self._load_selected_preset()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(shell, text="扫描 / 输出解释预设", padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="基准扫描器").grid(row=0, column=0, sticky="w", pady=3)
        self.preset_combo = ttk.Combobox(header, textvariable=self.preset_name, values=scanner_preset_names(), state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(header, text="加载", command=self._load_selected_preset).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="保存为用户扫描器", command=self._save_user_preset).grid(row=0, column=3)
        ttk.Label(header, text="介质解释").grid(row=1, column=0, sticky="w", pady=3)
        medium_row = ttk.Frame(header)
        medium_row.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=3)
        ttk.Combobox(medium_row, textvariable=self.target_medium_process, values=("negative", "slide", "reversal", "instant", "direct_positive", "daguerreotype"), state="readonly", width=16).pack(side="left", padx=(0, 6))
        ttk.Combobox(medium_row, textvariable=self.input_polarity, values=("negative", "positive"), state="readonly", width=12).pack(side="left", padx=(0, 6))
        ttk.Combobox(medium_row, textvariable=self.output_polarity, values=("positive", "negative"), state="readonly", width=12).pack(side="left")
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
        row = self._section(controls, "底片读取 / 去色罩", row)
        row = self._combo(controls, "扫描方法", self.scan_method, ("negative_inversion", "legacy_density_mapping"), row)
        row = self._vector(controls, "scanner_light_color", "扫描光源 RGB", 0.50, 1.50, row)
        row = self._matrix(controls, "scanner_response_matrix", "扫描器 RGB 响应矩阵", -0.25, 1.50, row)
        row = self._slider(controls, "片基估计 percentile", "scan_base_percentile", 90.0, 100.0, row)

        row = self._section(controls, "正像映射 / 滤色", row)
        row = self._vector(controls, "print_reference_density", "参考密度 RGB", 0.60, 2.40, row)
        row = self._slider(controls, "print gamma", "print_gamma", 0.45, 1.60, row)
        row = self._slider(controls, "打印反差倍率", "print_contrast", 0.60, 1.80, row)
        row = self._slider(controls, "打印曝光 EV", "print_exposure_ev", -2.0, 2.0, row)
        row = self._combo(controls, "映射曲线", self.print_mapping_mode, ("printlike", "sigmoid"), row)
        row = self._vector(controls, "print_color_shift", "log 域滤色 shift RGB", -0.18, 0.18, row)
        row = self._vector(controls, "print_color_bias", "RGB 增益 bias", 0.75, 1.25, row)

        row = self._section(controls, "高光 / 饱和 / 黑白点", row)
        row = self._vector(controls, "highlight_color_bias", "高光偏色 RGB", 0.75, 1.25, row)
        row = self._slider(controls, "高光偏色阈值", "highlight_bias_threshold", 0.30, 0.95, row)
        row = self._slider(controls, "高光偏色软化", "highlight_bias_softness", 0.02, 0.45, row)
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

        preview = ttk.LabelFrame(shell, text="scan/render 曲线预览", padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.curve_label = ttk.Label(preview)
        self.curve_label.grid(row=0, column=0, sticky="nsew")
        ttk.Button(preview, text="刷新曲线", command=self._refresh_curve).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=960).pack(fill="x", side="bottom")

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
        text = tk.StringVar()
        var.trace_add("write", lambda *_: text.set(f"{var.get():.3f}"))
        text.set(f"{var.get():.3f}")
        ttk.Label(parent, textvariable=text, width=8).grid(row=row, column=3, sticky="e", pady=4)
        parent.columnconfigure(1, weight=1)
        return row + 1

    def _vector(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        vars_ = self.vector_vars[key]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        for index, var in enumerate(vars_):
            spin = ttk.Spinbox(parent, from_=min_value, to=max_value, increment=(max_value - min_value) / 200.0, textvariable=var, width=8)
            spin.grid(row=row, column=index + 1, sticky="ew", padx=2, pady=3)
            var.trace_add("write", lambda *_: self._refresh_curve())
        return row + 1

    def _matrix(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        matrix = self.matrix_vars[key]
        for y in range(3):
            for x in range(3):
                var = matrix[y][x]
                spin = ttk.Spinbox(parent, from_=min_value, to=max_value, increment=(max_value - min_value) / 200.0, textvariable=var, width=8)
                spin.grid(row=row + y, column=x + 1, sticky="ew", padx=2, pady=2)
                var.trace_add("write", lambda *_: self._refresh_curve())
        return row + 3

    def _load_selected_preset(self) -> None:
        path = scanner_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(f"找不到扫描预设：{path}")
            return
        config = DarkroomConfig.from_json(path)
        self._set_controls_from_config(config)
        source = "用户" if path.parent == USER_SCANNER_PRESET_DIR else "内置"
        self.status.set(f"已加载{source}扫描预设：{path.name}")
        self._refresh_curve()

    def _set_controls_from_config(self, config: DarkroomConfig) -> None:
        scanner = config.scanner
        self.target_medium_process.set(str(scanner.target_medium_process))
        self.input_polarity.set(str(scanner.input_polarity))
        self.output_polarity.set(str(scanner.output_polarity))
        self.scan_method.set(str(scanner.scan_method))
        self.print_mapping_mode.set(str(scanner.print_mapping_mode))
        self.scan_normalize.set(bool(scanner.scan_normalize))
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
        matrix = scanner.scanner_response_matrix
        for y in range(3):
            for x in range(3):
                self.matrix_vars["scanner_response_matrix"][y][x].set(float(matrix[y][x]))

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = scanner_preset_path(self.preset_name.get())
        config = DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig()
        scanner = config.scanner
        scanner.target_medium_process = str(self.target_medium_process.get())
        scanner.input_polarity = str(self.input_polarity.get())
        scanner.output_polarity = str(self.output_polarity.get())
        scanner.scan_method = str(self.scan_method.get())
        scanner.print_mapping_mode = str(self.print_mapping_mode.get())
        scanner.scan_normalize = bool(self.scan_normalize.get())
        scanner.scan_normalize_mode = str(self.scan_normalize_mode.get())
        for key, var in self.vars.items():
            if key in SCAN_LOOK_FIELDS:
                setattr(config.look, key, float(var.get()))
            else:
                setattr(scanner, key, float(var.get()))
        for key, vars_ in self.vector_vars.items():
            setattr(scanner, key, tuple(float(var.get()) for var in vars_))
        matrix = self.matrix_vars["scanner_response_matrix"]
        scanner.scanner_response_matrix = tuple(tuple(float(matrix[y][x].get()) for x in range(3)) for y in range(3))
        return config

    def _preset_payload(self, config: DarkroomConfig) -> dict:
        look = {field_name: getattr(config.look, field_name) for field_name in SCAN_LOOK_FIELDS}
        return {
            "scanner": asdict(config.scanner),
            "look": look,
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

        raw_d = np.linspace(0.0, 2.6, 420, dtype=np.float32)
        positive_raw = np.repeat(raw_d[:, None, None], 3, axis=2)
        mapped = render_positive_scan(
            positive_raw,
            config.scanner,
            print_contrast=config.look.print_contrast,
            print_exposure_ev=config.look.print_exposure_ev,
        )[:, 0, :]
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
            xs = x0 + raw_d / 2.6 * (x1 - x0)
            ys = y1 - np.clip(mapped[:, channel], 0.0, 1.0) * (y1 - y0)
            points = [(int(round(x)), int(round(y))) for x, y in zip(xs, ys)]
            draw.line(points, fill=color, width=3)

        draw.text((x0, 12), f"mapping={config.scanner.print_mapping_mode}, norm={config.scanner.scan_normalize_mode}", fill=(42, 38, 34))
        draw.text((x0, height - 29), "raw positive density", fill=(70, 66, 58))
        draw.text((12, y0 - 4), "positive", fill=(70, 66, 58))
        return image

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(self.preset_name.get()) or "custom_scan"
        name = simpledialog.askstring(
            "保存扫描预设",
            "请输入扫描预设文件名（建议英文、数字、下划线）：",
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            messagebox.showerror("无法保存", "预设文件名不能为空，也不能只包含特殊字符。", parent=self.root)
            return
        path = USER_SCANNER_PRESET_DIR / f"{stem}.json"
        existing = scanner_preset_path(stem)
        if existing.exists():
            if existing.parent == SCANNER_PRESET_DIR and not path.exists():
                detail = f"{stem}.json 是内置扫描预设。保存后会创建同名用户扫描预设，并在 GUI/CLI 中优先使用。"
            else:
                detail = f"{path.name} 已存在。"
            if not messagebox.askyesno("覆盖扫描预设", f"{detail}\n是否继续？", parent=self.root):
                return
        save_json(path, self._preset_payload(config))
        self.preset_combo.configure(values=scanner_preset_names())
        self.preset_name.set(stem)
        self.status.set(f"已保存用户扫描预设：{path}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ScannerRenderEditor().run()
