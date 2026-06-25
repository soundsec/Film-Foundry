"""Standalone develop process preset editor for Film Foundry.

Run this file directly in an IDE to edit darkroom-facing process recipes.  It
saves custom develop presets to user_presets/develop so the main GUI/CLI can
use them without modifying bundled presets.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from PIL import Image, ImageDraw, ImageTk

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.model.config import DEVELOP_LOOK_FIELDS, DarkroomConfig


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
DEVELOP_PRESET_DIR = PRESET_DIR / "develop"
USER_DEVELOP_PRESET_DIR = PROJECT_ROOT / "user_presets" / "develop"
APP_TITLE = "Film Foundry - Develop Process Editor"


def safe_preset_stem(name: str) -> str:
    stem = []
    for char in name.strip().lower():
        if char.isalnum() or char in {"_", "-"}:
            stem.append(char)
        elif char.isspace():
            stem.append("_")
    return "".join(stem).strip("_-")


def develop_preset_names() -> list[str]:
    names = {path.stem for path in DEVELOP_PRESET_DIR.glob("*.json")}
    names.update(path.stem for path in USER_DEVELOP_PRESET_DIR.glob("*.json"))
    return sorted(names)


def develop_preset_path(name: str) -> Path:
    user_path = USER_DEVELOP_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return DEVELOP_PRESET_DIR / f"{name}.json"


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


class DevelopProcessEditor:
    def __init__(self) -> None:
        USER_DEVELOP_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1020x760")
        self.root.minsize(820, 620)

        self.preset_name = tk.StringVar(value="standard_color_negative")
        self.developer_name = tk.StringVar(value="")
        self.fixer_name = tk.StringVar(value="")
        self.mode = tk.StringVar(value="color_negative")
        self.medium_process = tk.StringVar(value="negative")
        self.developer_type = tk.StringVar(value="standard")
        self.fixer_type = tk.StringVar(value="standard")
        self.process_mode = tk.StringVar(value="normal_negative")
        self.frame_size = tk.StringVar(value="35mm")
        self.status = tk.StringVar(value="加载冲洗流程 preset，调整后保存到 user_presets/develop。")
        self.preview_photo = None

        self.vars: dict[str, tk.DoubleVar] = {
            "time_min": tk.DoubleVar(value=8.0),
            "temperature_c": tk.DoubleVar(value=20.0),
            "concentration": tk.DoubleVar(value=1.0),
            "agitation": tk.DoubleVar(value=1.0),
            "push_stops": tk.DoubleVar(value=0.0),
            "developer_exhaustion": tk.DoubleVar(value=0.0),
            "fixer_exhaustion": tk.DoubleVar(value=0.0),
            "compensation": tk.DoubleVar(value=0.0),
            "silver_retention": tk.DoubleVar(value=0.0),
            "light_leak_strength": tk.DoubleVar(value=0.0),
            "chemical_stain": tk.DoubleVar(value=0.0),
            "uneven_development": tk.DoubleVar(value=0.0),
            "exposure_ev": tk.DoubleVar(value=-0.05),
            "negative_contrast": tk.DoubleVar(value=1.0),
            "halation_multiplier": tk.DoubleVar(value=1.0),
            "grain_multiplier": tk.DoubleVar(value=1.0),
            "grain_size_multiplier": tk.DoubleVar(value=1.0),
            "look_strength": tk.DoubleVar(value=1.0),
        }

        self._build()
        self._load_selected_preset()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(shell, text="冲洗流程预设", padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="基准流程").grid(row=0, column=0, sticky="w", pady=3)
        self.preset_combo = ttk.Combobox(header, textvariable=self.preset_name, values=develop_preset_names(), state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Button(header, text="加载", command=self._load_selected_preset).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="保存为用户流程", command=self._save_user_preset).grid(row=0, column=3)
        ttk.Label(header, text="显影液名称").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(header, textvariable=self.developer_name).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Label(header, text="定影/清除名称").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Entry(header, textvariable=self.fixer_name).grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=3)
        ttk.Combobox(header, textvariable=self.mode, values=("color_negative", "bw_negative"), state="readonly", width=16).grid(
            row=1, column=2, columnspan=2, sticky="ew", pady=3
        )
        ttk.Label(header, text="适用介质").grid(row=2, column=2, sticky="w", pady=3)
        ttk.Combobox(
            header,
            textvariable=self.medium_process,
            values=("negative", "reversal", "slide", "instant", "direct_positive", "daguerreotype"),
            state="readonly",
            width=16,
        ).grid(row=2, column=3, sticky="ew", pady=3)
        header.columnconfigure(1, weight=1)

        controls = ttk.LabelFrame(shell, text="暗房流程参数", padding=10)
        controls.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        controls.columnconfigure(1, weight=1)
        row = 0
        row = self._combo(controls, "显影液 / 药水类型", self.developer_type, ("standard", "fine_grain", "compensating", "high_contrast", "push", "exhausted", "monobath", "hardening"), row)
        row = self._combo(controls, "定影 / 清除类型", self.fixer_type, ("standard", "rapid", "hardening", "monobath"), row)
        row = self._combo(controls, "流程模式", self.process_mode, ("normal_negative", "monobath"), row)
        row = self._combo(controls, "画幅", self.frame_size, ("half_frame", "35mm", "6x6", "6x7", "4x5"), row)
        row = self._slider(controls, "冲洗时间 min", "time_min", 1.0, 20.0, row)
        row = self._slider(controls, "药水/环境温度 C", "temperature_c", 12.0, 32.0, row)
        row = self._slider(controls, "药水浓度 x", "concentration", 0.25, 2.0, row)
        row = self._slider(controls, "搅拌强度", "agitation", 0.0, 2.5, row)
        row = self._slider(controls, "迫冲 / 欠冲 stop", "push_stops", -2.0, 3.0, row)
        row = self._slider(controls, "补偿显影", "compensation", 0.0, 1.0, row)
        row = self._slider(controls, "显影液疲劳", "developer_exhaustion", 0.0, 1.0, row)
        row = self._slider(controls, "定影疲劳 / 清除失败", "fixer_exhaustion", 0.0, 1.0, row)
        row = self._slider(controls, "残银 / 镀银倾向", "silver_retention", 0.0, 1.0, row)
        row = self._slider(controls, "漏光事故", "light_leak_strength", 0.0, 1.0, row)
        row = self._slider(controls, "海带 / 药染浑浊", "chemical_stain", 0.0, 1.0, row)
        row = self._slider(controls, "显影不均 / 药痕", "uneven_development", 0.0, 1.0, row)
        ttk.Separator(controls).grid(row=row, column=0, columnspan=4, sticky="ew", pady=10)
        row += 1
        row = self._slider(controls, "曝光校准 EV", "exposure_ev", -2.0, 2.0, row)
        row = self._slider(controls, "负片反差校准", "negative_contrast", 0.65, 1.45, row)
        row = self._slider(controls, "光晕校准", "halation_multiplier", 0.0, 2.0, row)
        row = self._slider(controls, "颗粒强度校准", "grain_multiplier", 0.3, 2.5, row)
        row = self._slider(controls, "颗粒尺寸校准", "grain_size_multiplier", 0.5, 2.0, row)

        preview = ttk.LabelFrame(shell, text="有效冲洗状态预览", padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.preview_label = ttk.Label(preview)
        self.preview_label.grid(row=0, column=0, sticky="nsew")
        ttk.Button(preview, text="刷新预览", command=self._refresh_preview).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=940).pack(fill="x", side="bottom")

    def _combo(self, parent: ttk.Frame, label: str, var: tk.StringVar, values: tuple[str, ...], row: int) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        widget = ttk.Combobox(parent, textvariable=var, values=values, state="readonly")
        widget.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
        var.trace_add("write", lambda *_: self._refresh_preview())
        return row + 1

    def _slider(self, parent: ttk.Frame, label: str, key: str, min_value: float, max_value: float, row: int) -> int:
        var = self.vars[key]
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Scale(parent, from_=min_value, to=max_value, variable=var, command=lambda _value: self._refresh_preview()).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=4
        )
        text = tk.StringVar()
        var.trace_add("write", lambda *_: text.set(f"{var.get():.2f}"))
        text.set(f"{var.get():.2f}")
        ttk.Label(parent, textvariable=text, width=7).grid(row=row, column=3, sticky="e", pady=4)
        return row + 1

    def _load_selected_preset(self) -> None:
        path = develop_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(f"找不到冲洗流程预设：{path}")
            return
        config = DarkroomConfig.from_json(path)
        self._set_controls_from_config(config)
        source = "用户" if path.parent == USER_DEVELOP_PRESET_DIR else "内置"
        self.status.set(f"已加载{source}冲洗流程：{path.name}")
        self._refresh_preview()

    def _set_controls_from_config(self, config: DarkroomConfig) -> None:
        recipe = config.chemistry
        self.mode.set(str(config.mode))
        self.medium_process.set(str(recipe.medium_process))
        self.developer_name.set(str(recipe.developer_name))
        self.fixer_name.set(str(recipe.fixer_name))
        self.developer_type.set(str(recipe.developer_type))
        self.fixer_type.set(str(recipe.fixer_type))
        self.process_mode.set(str(recipe.process_mode))
        self.frame_size.set(str(recipe.frame_size))
        for key in (
            "time_min",
            "temperature_c",
            "concentration",
            "agitation",
            "push_stops",
            "developer_exhaustion",
            "fixer_exhaustion",
            "compensation",
            "silver_retention",
            "light_leak_strength",
            "chemical_stain",
            "uneven_development",
        ):
            self.vars[key].set(float(getattr(recipe, key)))
        for key in DEVELOP_LOOK_FIELDS:
            if key in self.vars:
                value = getattr(config.look, key)
                if value is not None:
                    self.vars[key].set(float(value))

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = develop_preset_path(self.preset_name.get())
        config = DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig()
        recipe = config.chemistry
        config.mode = str(self.mode.get())
        config.medium = f"film_{self.medium_process.get()}"
        recipe.developer_name = self.developer_name.get().strip() or str(self.developer_type.get()).replace("_", " ").title()
        recipe.fixer_name = self.fixer_name.get().strip() or str(self.fixer_type.get()).replace("_", " ").title()
        recipe.developer_type = str(self.developer_type.get())
        recipe.fixer_type = str(self.fixer_type.get())
        recipe.medium_process = str(self.medium_process.get())
        recipe.process_mode = str(self.process_mode.get())
        recipe.frame_size = str(self.frame_size.get())
        for key in (
            "time_min",
            "temperature_c",
            "concentration",
            "agitation",
            "push_stops",
            "developer_exhaustion",
            "fixer_exhaustion",
            "compensation",
            "silver_retention",
            "light_leak_strength",
            "chemical_stain",
            "uneven_development",
        ):
            setattr(recipe, key, float(self.vars[key].get()))
        for key in DEVELOP_LOOK_FIELDS:
            if key in self.vars:
                setattr(config.look, key, float(self.vars[key].get()))
        return config

    def _preset_payload(self, config: DarkroomConfig) -> dict:
        look = {field_name: getattr(config.look, field_name) for field_name in DEVELOP_LOOK_FIELDS}
        return {
            "develop": asdict(config.chemistry),
            "mode": str(config.mode),
            "look": look,
        }

    def _refresh_preview(self) -> None:
        if not hasattr(self, "preview_label"):
            return
        try:
            config = self._config_from_controls()
            state = build_effective_development(config.chemistry)
            image = self._draw_state_preview(state)
            self.preview_photo = ImageTk.PhotoImage(image)
            self.preview_label.configure(image=self.preview_photo)
        except Exception:
            return

    def _draw_state_preview(self, state) -> Image.Image:
        width, height = 460, 520
        image = Image.new("RGB", (width, height), (252, 250, 246))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, width - 1, height - 1), outline=(222, 218, 208))
        draw.text((18, 16), "Derived process state", fill=(42, 38, 34))
        rows = [
            ("activity", state.activity, 0.0, 2.5),
            ("development progress", state.progress, 0.0, 1.0),
            ("gamma factor", state.gamma_factor, 0.4, 1.8),
            ("D-min fog shift", state.d_min_shift, 0.0, 0.18),
            ("D-max factor", state.d_max_factor, 0.4, 1.2),
            ("toe shift", state.toe_shift, -0.35, 0.20),
            ("shoulder shift", state.shoulder_shift, -0.45, 0.15),
            ("grain factor", state.grain_factor, 0.3, 2.8),
            ("grain radius", state.grain_radius_factor, 0.3, 2.0),
            ("clearing failure", state.clearing_failure, 0.0, 1.0),
            ("silvering", state.silvering_factor, 0.0, 2.0),
            ("residue", state.residue_factor, 0.0, 2.0),
            ("light leak", state.light_leak_strength, 0.0, 1.0),
            ("chemical stain", state.chemical_stain, 0.0, 1.0),
            ("uneven dev", state.uneven_development, 0.0, 1.0),
        ]
        x0, x1 = 172, 420
        y = 52
        for label, value, min_value, max_value in rows:
            ratio = (float(value) - min_value) / max(max_value - min_value, 1e-6)
            ratio = max(0.0, min(1.0, ratio))
            draw.text((18, y - 2), label, fill=(70, 66, 58))
            draw.rectangle((x0, y, x1, y + 11), fill=(229, 224, 214), outline=(205, 200, 190))
            color = (90, 130, 180)
            if label in {"D-min fog shift", "clearing failure", "silvering", "residue", "light leak", "chemical stain", "uneven dev"} and ratio > 0.55:
                color = (190, 100, 80)
            draw.rectangle((x0, y, x0 + int((x1 - x0) * ratio), y + 11), fill=color)
            draw.text((x1 - 58, y + 14), f"{value:.3f}", fill=(70, 66, 58))
            y += 29
        draw.text((18, height - 36), f"process={state.process_mode}", fill=(70, 66, 58))
        return image

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(config.chemistry.developer_name) or safe_preset_stem(self.preset_name.get()) or "custom_develop"
        name = simpledialog.askstring(
            "保存冲洗流程",
            "请输入流程预设文件名（建议英文、数字、下划线）：",
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            messagebox.showerror("无法保存", "预设文件名不能为空，也不能只包含特殊字符。", parent=self.root)
            return
        path = USER_DEVELOP_PRESET_DIR / f"{stem}.json"
        existing = develop_preset_path(stem)
        if existing.exists():
            if existing.parent == DEVELOP_PRESET_DIR and not path.exists():
                detail = f"{stem}.json 是内置流程。保存后会创建同名用户流程，并在 GUI/CLI 中优先使用。"
            else:
                detail = f"{path.name} 已存在。"
            if not messagebox.askyesno("覆盖冲洗流程", f"{detail}\n是否继续？", parent=self.root):
                return
        save_json(path, self._preset_payload(config))
        self.preset_combo.configure(values=develop_preset_names())
        self.preset_name.set(stem)
        self.status.set(f"已保存用户冲洗流程：{path}")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DevelopProcessEditor().run()
