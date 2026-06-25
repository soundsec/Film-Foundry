"""Standalone film material preset editor for Film Foundry.

Run this file directly in an IDE to edit the material layer only.  It saves
custom film presets to user_presets/film so the main GUI/CLI can use them
without modifying bundled presets.
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

from half_frame_darkroom.core.sensitometry import hd_density_curve
from half_frame_darkroom.model.config import DarkroomConfig


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
FILM_PRESET_DIR = PRESET_DIR / "film"
USER_FILM_PRESET_DIR = PROJECT_ROOT / "user_presets" / "film"
APP_TITLE = "Film Foundry - Film Material Editor"


def safe_preset_stem(name: str) -> str:
    stem = []
    for char in name.strip().lower():
        if char.isalnum() or char in {"_", "-"}:
            stem.append(char)
        elif char.isspace():
            stem.append("_")
    return "".join(stem).strip("_-")


def film_preset_names() -> list[str]:
    names = {path.stem for path in FILM_PRESET_DIR.glob("*.json")}
    names.update(path.stem for path in USER_FILM_PRESET_DIR.glob("*.json"))
    return sorted(names)


def film_preset_path(name: str) -> Path:
    user_path = USER_FILM_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return FILM_PRESET_DIR / f"{name}.json"


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


class FilmMaterialEditor:
    def __init__(self) -> None:
        USER_FILM_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1080x780")
        self.root.minsize(860, 620)

        self.preset_name = tk.StringVar(value="clear_modern_negative")
        self.material_name = tk.StringVar(value="")
        self.mode = tk.StringVar(value="color_negative")
        self.medium_family = tk.StringVar(value="film")
        self.medium_process = tk.StringVar(value="negative")
        self.image_polarity = tk.StringVar(value="negative")
        self.color_process = tk.StringVar(value="color")
        self.status = tk.StringVar(value="加载胶片材料 preset，调整后保存到 user_presets/film。")
        self.curve_photo = None

        self.scalar_vars: dict[str, tk.DoubleVar] = {}
        self.vector_vars: dict[str, tuple[tk.DoubleVar, tk.DoubleVar, tk.DoubleVar]] = {}
        self.matrix_vars: dict[str, list[list[tk.DoubleVar]]] = {}

        self._build()
        self._load_selected_preset()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(shell, text="材料预设", padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="基准材料").grid(row=0, column=0, sticky="w", pady=3)
        self.preset_combo = ttk.Combobox(header, textvariable=self.preset_name, values=film_preset_names(), state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", pady=3, padx=(8, 8))
        ttk.Button(header, text="加载", command=self._load_selected_preset).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="保存为用户材料", command=self._save_user_preset).grid(row=0, column=3)
        ttk.Label(header, text="材料名称").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(header, textvariable=self.material_name).grid(row=1, column=1, columnspan=2, sticky="ew", pady=3, padx=(8, 8))
        ttk.Combobox(header, textvariable=self.mode, values=("color_negative", "bw_negative"), state="readonly", width=16).grid(
            row=1, column=3, sticky="ew", pady=3
        )
        ttk.Label(header, text="介质").grid(row=2, column=0, sticky="w", pady=3)
        medium_row = ttk.Frame(header)
        medium_row.grid(row=2, column=1, columnspan=3, sticky="ew", pady=3, padx=(8, 0))
        ttk.Combobox(medium_row, textvariable=self.medium_family, values=("film", "instant", "plate", "paper"), state="readonly", width=12).pack(side="left", padx=(0, 6))
        ttk.Combobox(medium_row, textvariable=self.medium_process, values=("negative", "slide", "reversal", "instant", "direct_positive", "daguerreotype"), state="readonly", width=16).pack(side="left", padx=(0, 6))
        ttk.Combobox(medium_row, textvariable=self.image_polarity, values=("negative", "positive"), state="readonly", width=12).pack(side="left", padx=(0, 6))
        ttk.Combobox(medium_row, textvariable=self.color_process, values=("color", "monochrome"), state="readonly", width=12).pack(side="left")
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
        row = self._section(controls, "H-D 曲线 / 染料密度", row)
        row = self._vector(controls, "hd_gamma", "H-D gamma RGB", 0.20, 1.20, row)
        row = self._vector(controls, "density_min", "D-min / 片基灰雾", 0.00, 0.35, row)
        row = self._vector(controls, "density_max", "D-max / 最大密度", 0.80, 2.80, row)
        row = self._vector(controls, "log_exposure_toe", "toe 位置 logE", -3.40, -1.20, row)
        row = self._vector(controls, "log_exposure_shoulder", "shoulder 位置 logE", -0.20, 1.20, row)
        row = self._scalar(controls, "hd_toe_width", "toe 宽度", 0.04, 0.60, row)
        row = self._scalar(controls, "hd_shoulder_width", "shoulder 宽度", 0.04, 0.70, row)

        row = self._section(controls, "片基 / 颗粒 / 解析力", row)
        row = self._vector(controls, "film_base_density_rgb", "片基 RGB 密度", 0.00, 1.30, row)
        row = self._vector(controls, "granularity_sigma", "颗粒密度 sigma RGB", 0.00, 0.08, row)
        row = self._scalar(controls, "grain_density_correlation_radius", "颗粒相关半径", 0.0003, 0.0040, row)
        row = self._scalar(controls, "emulsion_mtf_strength", "乳剂 MTF 强度", 0.00, 0.70, row)
        row = self._scalar(controls, "digital_artifact_suppression", "数字锐化抑制", 0.00, 0.60, row)

        row = self._section(controls, "Halation 基准", row)
        row = self._scalar(controls, "halation_strength", "halation 强度", 0.00, 0.40, row)
        row = self._scalar(controls, "halation_threshold", "halation 阈值", 0.40, 1.20, row)
        row = self._scalar(controls, "halation_softness", "阈值软化", 0.02, 0.45, row)
        row = self._scalar(controls, "halation_core_radius", "短程散射半径", 0.0005, 0.0120, row)
        row = self._scalar(controls, "halation_outer_radius", "长程散射半径", 0.0030, 0.0500, row)
        row = self._scalar(controls, "halation_exponential_radius", "指数尾半径", 0.0030, 0.0600, row)

        row = self._section(controls, "三层感光 / 染料吸收矩阵", row)
        row = self._matrix(controls, "layer_sensitivity_matrix", "layer sensitivity", 0.0, 1.4, row)
        row = self._matrix(controls, "dye_absorption_matrix", "dye absorption", 0.0, 1.6, row)

        preview = ttk.LabelFrame(shell, text="曲线预览", padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.curve_label = ttk.Label(preview)
        self.curve_label.grid(row=0, column=0, sticky="nsew")
        ttk.Button(preview, text="刷新曲线", command=self._refresh_curve).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=980).pack(fill="x", side="bottom")

    def _section(self, parent: ttk.Frame, text: str, row: int) -> int:
        ttk.Label(parent, text=text, font=("", 10, "bold")).grid(row=row, column=0, columnspan=5, sticky="w", pady=(12, 4))
        return row + 1

    def _scalar(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        var = tk.DoubleVar(value=0.0)
        self.scalar_vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Scale(parent, from_=min_value, to=max_value, variable=var, command=lambda _value: self._refresh_curve()).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=3
        )
        value_text = tk.StringVar()
        var.trace_add("write", lambda *_: value_text.set(f"{var.get():.4f}"))
        value_text.set(f"{var.get():.4f}")
        ttk.Label(parent, textvariable=value_text, width=8).grid(row=row, column=3, sticky="e", pady=3)
        parent.columnconfigure(1, weight=1)
        return row + 1

    def _vector(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        vars_ = (tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0))
        self.vector_vars[key] = vars_
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        for index, var in enumerate(vars_):
            spin = ttk.Spinbox(parent, from_=min_value, to=max_value, increment=(max_value - min_value) / 200.0, textvariable=var, width=8)
            spin.grid(row=row, column=index + 1, sticky="ew", padx=2, pady=3)
            var.trace_add("write", lambda *_: self._refresh_curve())
        return row + 1

    def _matrix(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        matrix: list[list[tk.DoubleVar]] = []
        for y in range(3):
            matrix_row = []
            for x in range(3):
                var = tk.DoubleVar(value=0.0)
                spin = ttk.Spinbox(parent, from_=min_value, to=max_value, increment=(max_value - min_value) / 200.0, textvariable=var, width=8)
                spin.grid(row=row + y, column=x + 1, sticky="ew", padx=2, pady=2)
                var.trace_add("write", lambda *_: self._refresh_curve())
                matrix_row.append(var)
            matrix.append(matrix_row)
        self.matrix_vars[key] = matrix
        return row + 3

    def _load_selected_preset(self) -> None:
        path = film_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(f"找不到材料预设：{path}")
            return
        config = DarkroomConfig.from_json(path)
        self._set_controls_from_config(config)
        source = "用户" if path.parent == USER_FILM_PRESET_DIR else "内置"
        self.status.set(f"已加载{source}材料预设：{path.name}")
        self._refresh_curve()

    def _set_controls_from_config(self, config: DarkroomConfig) -> None:
        self.material_name.set(str(config.film.name))
        self.mode.set(str(config.mode))
        self.medium_family.set(str(config.film.medium_family))
        self.medium_process.set(str(config.film.medium_process))
        self.image_polarity.set(str(config.film.image_polarity))
        self.color_process.set(str(config.film.color_process))
        for key, var in self.scalar_vars.items():
            var.set(float(getattr(config.film, key)))
        for key, vars_ in self.vector_vars.items():
            values = tuple(float(value) for value in getattr(config.film, key))
            for var, value in zip(vars_, values):
                var.set(value)
        for key, matrix in self.matrix_vars.items():
            values = getattr(config.film, key)
            for y in range(3):
                for x in range(3):
                    matrix[y][x].set(float(values[y][x]))

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = film_preset_path(self.preset_name.get())
        config = DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig()
        config.film.name = self.material_name.get().strip() or "Custom Film Material"
        config.mode = str(self.mode.get())
        config.medium = f"{self.medium_family.get()}_{self.medium_process.get()}"
        config.film.medium_family = str(self.medium_family.get())
        config.film.medium_process = str(self.medium_process.get())
        config.film.image_polarity = str(self.image_polarity.get())
        config.film.color_process = str(self.color_process.get())
        for key, var in self.scalar_vars.items():
            setattr(config.film, key, float(var.get()))
        for key, vars_ in self.vector_vars.items():
            setattr(config.film, key, tuple(float(var.get()) for var in vars_))
        for key, matrix in self.matrix_vars.items():
            setattr(
                config.film,
                key,
                tuple(tuple(float(matrix[y][x].get()) for x in range(3)) for y in range(3)),
            )
        return config

    def _film_payload(self, config: DarkroomConfig) -> dict:
        return {
            "mode": str(config.mode),
            "film": asdict(config.film),
        }

    def _refresh_curve(self) -> None:
        if not hasattr(self, "curve_label"):
            return
        try:
            config = self._config_from_controls()
            image = self._draw_hd_curve(config)
            self.curve_photo = ImageTk.PhotoImage(image)
            self.curve_label.configure(image=self.curve_photo)
        except Exception:
            # Spinbox typing can briefly create invalid floats; ignore until input stabilizes.
            return

    def _draw_hd_curve(self, config: DarkroomConfig) -> Image.Image:
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

        log_e = np.linspace(-3.0, 1.0, 360, dtype=np.float32)
        exposure = np.repeat((10.0 ** log_e)[:, None], 3, axis=1)
        density = hd_density_curve(exposure, config.film, config.chemistry)
        y_max = max(float(np.max(density)) * 1.08, 0.6)
        colors = ((220, 70, 70), (70, 170, 85), (70, 110, 220))
        for channel, color in enumerate(colors):
            xs = x0 + (log_e + 3.0) / 4.0 * (x1 - x0)
            ys = y1 - np.clip(density[:, channel] / y_max, 0.0, 1.0) * (y1 - y0)
            points = [(int(round(x)), int(round(y))) for x, y in zip(xs, ys)]
            draw.line(points, fill=color, width=3)

        base = np.asarray(config.film.film_base_density_rgb, dtype=np.float32)
        transmittance = np.clip(10.0 ** (-base), 0.0, 1.0)
        swatch = tuple(int(round(value * 255.0)) for value in transmittance)
        draw.rectangle((x0, 14, x0 + 46, 26), fill=swatch, outline=(90, 86, 78))
        draw.text((x0 + 54, 12), f"{config.film.name}  |  base preview", fill=(42, 38, 34))
        draw.text((x0, height - 29), "log10(relative exposure)", fill=(70, 66, 58))
        draw.text((12, y0 - 4), "density", fill=(70, 66, 58))
        return image

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(config.film.name) or safe_preset_stem(self.preset_name.get()) or "custom_film_material"
        name = simpledialog.askstring(
            "保存胶片材料",
            "请输入材料预设文件名（建议英文、数字、下划线）：",
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            messagebox.showerror("无法保存", "预设文件名不能为空，也不能只包含特殊字符。", parent=self.root)
            return
        path = USER_FILM_PRESET_DIR / f"{stem}.json"
        existing = film_preset_path(stem)
        if existing.exists():
            if existing.parent == FILM_PRESET_DIR and not path.exists():
                detail = f"{stem}.json 是内置材料。保存后会创建同名用户材料，并在 GUI/CLI 中优先使用。"
            else:
                detail = f"{path.name} 已存在。"
            if not messagebox.askyesno("覆盖材料预设", f"{detail}\n是否继续？", parent=self.root):
                return
        save_json(path, self._film_payload(config))
        self.preset_combo.configure(values=film_preset_names())
        self.preset_name.set(stem)
        self.status.set(f"已保存用户胶片材料：{path}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    FilmMaterialEditor().run()
