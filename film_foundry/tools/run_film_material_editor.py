"""Shared silver-halide film material preset editor for Film Foundry.

Negative and reversal stocks share the editor shell, but keep explicit native
material classes, identities, curve previews, and material-side controls.
"""

from __future__ import annotations

from dataclasses import asdict
import json
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
from half_frame_darkroom.core.sensitometry import hd_density_curve
from half_frame_darkroom.core.film_process import shape_positive_density_fraction
from half_frame_darkroom.ui.widgets import VerticalScrolledFrame
from half_frame_darkroom.model.config import DarkroomConfig
from film_foundry.tools.editor_ui import localize_widget_tree, localized_preset_name, ui, unique_choice_map
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "half_frame_darkroom" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
USER_FILM_PRESET_DIR = PROJECT_ROOT / "user_presets" / "film"
APP_TITLE = "Film Foundry - Silver-Halide Film Material Editor"
SUPPORTED_MEDIUM_FAMILY = "film"
MATERIAL_CLASS_SPECS: dict[str, dict[str, str]] = {
    "color_negative": {
        "medium_process": "negative",
        "image_polarity": "negative",
        "color_process": "color",
        "mode": "color_negative",
    },
    "bw_negative": {
        "medium_process": "negative",
        "image_polarity": "negative",
        "color_process": "monochrome",
        "mode": "bw_negative",
    },
    "color_reversal": {
        "medium_process": "slide",
        "image_polarity": "positive",
        "color_process": "color",
        "mode": "color_negative",
    },
    "bw_reversal": {
        "medium_process": "reversal",
        "image_polarity": "positive",
        "color_process": "monochrome",
        "mode": "bw_negative",
    },
}
POSITIVE_MATERIAL_FIELDS = (
    "positive_density_contrast",
    "positive_density_bias",
    "positive_latitude_compression",
    "positive_dye_saturation",
    "positive_midtone_density",
    "positive_shadow_toe",
    "positive_shadow_toe_width",
    "positive_highlight_shoulder",
    "positive_highlight_shoulder_width",
    "positive_highlight_chroma_retention",
    "positive_shadow_chroma_retention",
)
HALATION_RETURN_MODE_LABELS = {
    ui("兼容暖色 RGB 回注", "Compatible Warm RGB Return"): "compatibility_rgb",
    ui("实验性材料层回流", "Experimental Material-Layer Return"): "layer_selective",
}
LIGHT_PIPING_EDGE_LABELS = {
    ui("关闭", "Off"): "none",
    ui("上边缘", "Top Edge"): "top",
    ui("右边缘", "Right Edge"): "right",
    ui("下边缘", "Bottom Edge"): "bottom",
    ui("左边缘", "Left Edge"): "left",
    ui("两条长边", "Both Long Edges"): "long_edges",
    ui("两条短边", "Both Short Edges"): "short_edges",
    ui("全部边缘", "All Edges"): "all_edges",
}

MATERIAL_EDITOR_EN = {
    "研究性极端曝光潜影尾部（默认关闭）": "Research Extreme-Exposure Latent Tail (Off by Default)",
    "极端曝光潜影回落强度": "Extreme-Exposure Latent Decline Strength",
    "回落起点 logE RGB": "Tail Start logE RGB",
    "回落过渡宽度": "Tail Transition Width",
    "片基边缘光导（实验）": "Support Edge Light Piping (Experimental)",
    "片基光导强度": "Support Light-Piping Strength",
    "向内传播深度": "Inward Propagation Depth",
    "入光边缘": "Entry Edges",
    "光导材料层权重": "Light-Piping Material-Layer Weights",
    "实验性金属银颗粒强度": "Experimental Metallic-Silver Grain Strength",
    "金属银颗粒相关半径": "Metallic-Silver Grain Radius",
    "金属银粗团聚混合": "Metallic-Silver Coarse-Clump Mix",
    "银盐胶片材料预设（分类共享外壳）": "Silver-Halide Film Material Presets",
    "基准材料": "Base material",
    "加载": "Load",
    "保存为用户胶片材料": "Save user film material",
    "材料名称": "Material name",
    "当前介质": "Current medium",
    "负片与反转片共享编辑器外壳，但调用身份、原生极性和曲线预览分开；即时成像、撕拉片和银版仍使用独立入口。": "Negative and reversal stocks share this editor, while retaining separate identities, native polarities, and curve previews. Other media use separate tools.",
    "H-D 曲线 / 染料密度": "H-D Curve / Dye Density",
    "D-min / 片基灰雾": "D-min / Base Fog",
    "D-max / 最大密度": "D-max / Maximum Density",
    "toe 位置 logE": "Toe Position logE",
    "shoulder 位置 logE": "Shoulder Position logE",
    "toe 宽度": "Toe Width",
    "shoulder 宽度": "Shoulder Width",
    "反转片原生正像曲线（负片材料中保留但不参与原生负片曲线）": "Native Reversal Positive Curve",
    "正片密度反差": "Positive Density Contrast",
    "正片密度偏移": "Positive Density Bias",
    "正片宽容度压缩": "Positive Latitude Compression",
    "正片染料饱和度": "Positive Dye Saturation",
    "正片中间调密度": "Positive Midtone Density",
    "正片暗部 toe": "Positive Shadow Toe",
    "正片暗部 toe 宽度": "Positive Shadow Toe Width",
    "正片高光 shoulder": "Positive Highlight Shoulder",
    "正片高光 shoulder 宽度": "Positive Highlight Shoulder Width",
    "正片高光端色度保留": "Positive Highlight Chroma Retention",
    "正片暗部端色度保留": "Positive Shadow Chroma Retention",
    "非原生程序兼容性（仅逆冲/跨材料程序启用）": "Non-Native Process Compatibility",
    "逆冲银显影效率": "Cross-Process Silver Development",
    "逆冲染料偶合效率": "Cross-Process Dye Coupling",
    "逆冲剩余卤化银激活": "Cross-Process Halide Activation",
    "逆冲银漂白能力": "Cross-Process Silver Bleach",
    "逆冲定影能力": "Cross-Process Fixing",
    "逆冲直接去银能力": "Cross-Process Direct Silver Removal",
    "逆冲染料稳定性": "Cross-Process Dye Stability",
    "逆冲附加层去除能力": "Cross-Process Auxiliary Removal",
    "逆冲层间反应平衡": "Cross-Process Layer Balance",
    "片基 / 颗粒 / 解析力": "Base / Grain / Resolution",
    "综合色罩 / 片基 RGB 光学密度（可自定义颜色）": "Combined Mask / Base RGB Optical Density (Custom Color)",
    "透明片基 RGB 密度下限": "Clear Support RGB Density Floor",
    "片基—染料光谱耦合强度": "Base–Dye Spectral Interaction Strength",
    "实验性色罩漂白敏感度": "Experimental Mask-Bleach Susceptibility",
    "实验性色罩漂白染料损伤": "Experimental Mask-Bleach Dye Damage",
    "定影不足残留银盐 RGB 密度权重": "Residual Halide RGB Density",
    "附加层初始量": "Initial Auxiliary Layer Amount",
    "附加层 RGB 光学密度": "Auxiliary Layer RGB Density",
    "材料退化强度": "Material Degradation",
    "满强度感光度损失 stop": "Full Degradation Speed Loss (stops)",
    "满强度退化底雾 RGB 密度": "Full Degradation Fog RGB Density",
    "满强度退化层间感度平衡": "Full Degradation Layer Balance",
    "颗粒密度 sigma RGB": "Granularity Sigma RGB",
    "颗粒相关半径": "Grain Correlation Radius",
    "乳剂 MTF 强度": "Emulsion MTF Strength",
    "乳剂模糊半径": "Emulsion Blur Radius",
    "高频响应阈值": "High-Frequency Response Threshold",
    "数字锐化抑制": "Digital Sharpening Suppression",
    "Halation 基准": "Halation Baseline",
    "halation 强度": "Halation Strength",
    "halation 阈值": "Halation Threshold",
    "阈值软化": "Threshold Softness",
    "短程散射半径": "Short-Range Scatter Radius",
    "长程散射半径": "Long-Range Scatter Radius",
    "短程/长程混合": "Short/Long-Range Mix",
    "光晕颜色 RGB": "Halation Color RGB",
    "回流模型": "Return Model",
    "材料层回流相对权重": "Material-Layer Return Weights",
    "传播尺度权重（紧邻 / 主回流 / 宽域）": "Spread Scale Weights (Compact / Main / Wide)",
    "光源预模糊半径": "Source Pre-Blur Radius",
    "边缘梯度抑制": "Edge Gradient Suppression",
    "局部峰值半径": "Local Peak Radius",
    "局部峰值阈值": "Local Peak Threshold",
    "局部峰值软化": "Local Peak Softness",
    "大面积亮区半径": "Broad Highlight Radius",
    "大面积亮区阈值": "Broad Highlight Threshold",
    "大面积亮区抑制": "Broad Highlight Suppression",
    "高斯散射幅度": "Gaussian Scatter Amplitude",
    "指数散射幅度": "Exponential Scatter Amplitude",
    "指数尾半径": "Exponential Tail Radius",
    "三层感光 / 染料吸收 / 片基光谱耦合矩阵": "Sensitivity / Dye / Base Spectral Matrices",
    "曲线预览": "Curve Preview",
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


def film_preset_names() -> list[str]:
    names = {path.stem for path in FILM_PRESET_DIR.glob("*.json")}
    names.update(path.stem for path in USER_FILM_PRESET_DIR.glob("*.json"))
    supported: list[str] = []
    for name in sorted(names):
        try:
            config = DarkroomConfig.from_json(film_preset_path(name))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if is_supported_silver_halide_material(config):
            supported.append(name)
    return supported


def film_preset_path(name: str) -> Path:
    user_path = USER_FILM_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return FILM_PRESET_DIR / f"{name}.json"


def film_preset_choices() -> tuple[list[str], dict[str, str]]:
    items: list[tuple[str, str]] = []
    for key in film_preset_names():
        path = film_preset_path(key)
        try:
            fallback = str(DarkroomConfig.from_json(path).film.name).strip() or key
        except Exception:
            fallback = key
        items.append((key, localized_preset_name("film", key, fallback, selected_path=path, builtin_dir=FILM_PRESET_DIR)))
    return unique_choice_map(items)


def save_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def is_supported_silver_halide_material(config: DarkroomConfig) -> bool:
    return (
        str(config.film.medium_family).lower() in {"", SUPPORTED_MEDIUM_FAMILY}
        and str(config.film.medium_process).lower() in {"", "negative", "slide", "reversal", "positive"}
        and str(config.film.image_polarity).lower() in {"", "negative", "positive"}
    )


def material_class_for_config(config: DarkroomConfig) -> str:
    process = str(config.film.medium_process).strip().lower().replace("-", "_")
    polarity = str(config.film.image_polarity).strip().lower()
    color = str(config.film.color_process).strip().lower().replace("-", "_")
    monochrome = color in {"bw", "black_white", "monochrome"}
    reversal = polarity == "positive" or process in {"slide", "reversal", "positive"}
    if reversal:
        return "bw_reversal" if monochrome else "color_reversal"
    return "bw_negative" if monochrome else "color_negative"


def material_identity_for_class(material_class: str) -> dict[str, str]:
    return dict(MATERIAL_CLASS_SPECS.get(str(material_class), MATERIAL_CLASS_SPECS["color_negative"]))


def material_preview_density(exposure: np.ndarray, config: DarkroomConfig) -> np.ndarray:
    """Return the native material curve shown by the shared editor.

    Negative stocks show their exposure-selective density curve. Reversal
    stocks show the positive density formed from remaining material, including
    the material-side positive curve controls.
    """
    negative_density = hd_density_curve(exposure, config.film, config.chemistry)
    if material_class_for_config(config) not in {"color_reversal", "bw_reversal"}:
        return negative_density.astype(np.float32)
    d_min = np.asarray(config.film.density_min, dtype=np.float32).reshape(1, 3)
    d_max = np.asarray(config.film.density_max, dtype=np.float32).reshape(1, 3)
    normalized_negative = np.clip(
        (negative_density - d_min) / np.maximum(d_max - d_min, 1e-6),
        0.0,
        1.0,
    )
    positive_fraction = shape_positive_density_fraction(1.0 - normalized_negative, config.film)
    return (d_min + positive_fraction * (d_max - d_min)).astype(np.float32)


# Compatibility aliases for older callers.
is_supported_negative_material = is_supported_silver_halide_material
negative_mode_for_config = lambda config: material_identity_for_class(material_class_for_config(config))["mode"]
color_process_for_mode = lambda mode: "monochrome" if str(mode) == "bw_negative" else "color"


class FilmMaterialEditor:
    def __init__(self) -> None:
        USER_FILM_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title(ui("Film Foundry - 银盐胶片材料编辑器", APP_TITLE))
        self.root.geometry("1080x780")
        self.root.minsize(860, 620)

        self.preset_name = tk.StringVar(value="clear_modern_negative")
        self.preset_display = tk.StringVar(value="")
        self.preset_display_to_key: dict[str, str] = {}
        self.material_name = tk.StringVar(value="")
        self.material_class = tk.StringVar(value="color_negative")
        self.mode = tk.StringVar(value="color_negative")
        self.medium_family = tk.StringVar(value="film")
        self.medium_process = tk.StringVar(value="negative")
        self.image_polarity = tk.StringVar(value="negative")
        self.color_process = tk.StringVar(value="color")
        self.identity_summary = tk.StringVar(value="film / negative / negative polarity / color")
        self.halation_return_display = tk.StringVar(value="")
        self.light_piping_edge_display = tk.StringVar(value="")
        self.status = tk.StringVar(value=ui("加载银盐胶片材料预设，调整后保存到 user_presets/film。", "Load a silver-halide material preset, adjust it, then save to user_presets/film."))
        self.curve_photo = None

        self.scalar_vars: dict[str, tk.DoubleVar] = {}
        self.vector_vars: dict[str, tuple[tk.DoubleVar, tk.DoubleVar, tk.DoubleVar]] = {}
        self.matrix_vars: dict[str, list[list[tk.DoubleVar]]] = {}
        self.field_widgets: dict[str, ttk.Widget] = {}

        self._build()
        self._refresh_preset_choices()
        self.material_class.trace_add("write", lambda *_: self._apply_material_class())
        self._load_selected_preset()

    def _build(self) -> None:
        shell = ttk.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.LabelFrame(shell, text="银盐胶片材料预设（分类共享外壳）", padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="基准材料").grid(row=0, column=0, sticky="w", pady=3)
        self.preset_combo = ttk.Combobox(header, textvariable=self.preset_display, state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", pady=3, padx=(8, 8))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(header, text="加载", command=self._load_selected_preset).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(header, text="保存为用户胶片材料", command=self._save_user_preset).grid(row=0, column=3)
        ttk.Label(header, text="材料名称").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(header, textvariable=self.material_name).grid(row=1, column=1, columnspan=2, sticky="ew", pady=3, padx=(8, 8))
        ttk.Combobox(header, textvariable=self.material_class, values=tuple(MATERIAL_CLASS_SPECS), state="readonly", width=16).grid(
            row=1, column=3, sticky="ew", pady=3
        )
        ttk.Label(header, text="当前介质").grid(row=2, column=0, sticky="w", pady=3)
        medium_row = ttk.Frame(header)
        medium_row.grid(row=2, column=1, columnspan=3, sticky="ew", pady=3, padx=(8, 0))
        ttk.Label(medium_row, textvariable=self.identity_summary).pack(side="left", padx=(0, 10))
        ttk.Label(
            medium_row,
            text="负片与反转片共享编辑器外壳，但调用身份、原生极性和曲线预览分开；即时成像、撕拉片和银版仍使用独立入口。",
            wraplength=560,
        ).pack(side="left")
        header.columnconfigure(1, weight=1)

        controls_shell = ttk.Frame(shell)
        controls_shell.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        controls_shell.rowconfigure(0, weight=1)
        controls_shell.columnconfigure(0, weight=1)

        self.controls_scroller = VerticalScrolledFrame(controls_shell, canvas_width=560)
        self.controls_scroller.grid(row=0, column=0, sticky="nsew")
        controls = self.controls_scroller.content

        row = 0
        row = self._section(controls, "H-D 曲线 / 染料密度", row)
        row = self._vector(controls, "hd_gamma", "H-D gamma RGB", 0.20, 1.20, row)
        row = self._vector(controls, "density_min", "D-min / 片基灰雾", 0.00, 0.35, row)
        row = self._vector(controls, "density_max", "D-max / 最大密度", 0.80, 2.80, row)
        row = self._vector(controls, "log_exposure_toe", "toe 位置 logE", -3.40, -1.20, row)
        row = self._vector(controls, "log_exposure_shoulder", "shoulder 位置 logE", -0.20, 1.20, row)
        row = self._scalar(controls, "hd_toe_width", "toe 宽度", 0.04, 0.60, row)
        row = self._scalar(controls, "hd_shoulder_width", "shoulder 宽度", 0.04, 0.70, row)

        row = self._section(controls, "研究性极端曝光潜影尾部（默认关闭）", row)
        row = self._scalar(
            controls,
            "extreme_exposure_reversal_strength",
            "极端曝光潜影回落强度",
            0.00,
            1.00,
            row,
        )
        row = self._vector(
            controls,
            "extreme_exposure_reversal_start_loge",
            "回落起点 logE RGB",
            0.30,
            1.50,
            row,
        )
        row = self._scalar(
            controls,
            "extreme_exposure_reversal_width",
            "回落过渡宽度",
            0.03,
            0.80,
            row,
        )

        row = self._section(controls, "反转片原生正像曲线（负片材料中保留但不参与原生负片曲线）", row)
        row = self._scalar(controls, "positive_density_contrast", "正片密度反差", 0.50, 2.00, row)
        row = self._scalar(controls, "positive_density_bias", "正片密度偏移", -0.25, 0.25, row)
        row = self._scalar(controls, "positive_latitude_compression", "正片宽容度压缩", 0.00, 1.00, row)
        row = self._scalar(controls, "positive_dye_saturation", "正片染料饱和度", 0.00, 2.00, row)
        row = self._scalar(controls, "positive_midtone_density", "正片中间调密度", 0.00, 1.00, row)
        row = self._scalar(controls, "positive_shadow_toe", "正片暗部 toe", 0.00, 1.00, row)
        row = self._scalar(controls, "positive_shadow_toe_width", "正片暗部 toe 宽度", 0.02, 0.80, row)
        row = self._scalar(controls, "positive_highlight_shoulder", "正片高光 shoulder", 0.00, 1.00, row)
        row = self._scalar(controls, "positive_highlight_shoulder_width", "正片高光 shoulder 宽度", 0.02, 0.80, row)
        row = self._scalar(controls, "positive_highlight_chroma_retention", "正片高光端色度保留", 0.00, 1.00, row)
        row = self._scalar(controls, "positive_shadow_chroma_retention", "正片暗部端色度保留", 0.00, 1.00, row)

        row = self._section(controls, "非原生程序兼容性（仅逆冲/跨材料程序启用）", row)
        row = self._scalar(controls, "cross_process_silver_development", "逆冲银显影兼容效率", 0.00, 1.00, row)
        row = self._scalar(controls, "cross_process_dye_coupling", "逆冲染料偶合效率", 0.00, 1.50, row)
        row = self._scalar(controls, "cross_process_activation", "逆冲激活兼容效率", 0.00, 1.00, row)
        row = self._scalar(controls, "cross_process_silver_bleach", "逆冲银漂白兼容效率", 0.00, 1.00, row)
        row = self._scalar(controls, "cross_process_halide_fixing", "逆冲定影兼容效率", 0.00, 1.00, row)
        row = self._scalar(controls, "cross_process_silver_removal", "逆冲直接去银兼容效率", 0.00, 1.00, row)
        row = self._scalar(controls, "cross_process_dye_stability", "逆冲染料稳定性", 0.00, 1.00, row)
        row = self._scalar(controls, "cross_process_auxiliary_removal", "逆冲附加层去除兼容效率", 0.00, 1.00, row)
        row = self._vector(controls, "cross_process_layer_balance", "非原生材料额外层兼容倍率（与程序倍率相乘）", 0.00, 2.00, row)

        row = self._section(controls, "片基 / 颗粒 / 解析力", row)
        row = self._vector(controls, "film_base_density_rgb", "综合色罩 / 片基 RGB 光学密度（可自定义颜色）", 0.00, 1.30, row)
        row = self._vector(controls, "clear_support_density_rgb", "透明片基 RGB 密度下限", 0.00, 0.50, row)
        row = self._scalar(controls, "base_dye_interaction_strength", "片基—染料光谱耦合强度", 0.00, 1.00, row)
        row = self._scalar(controls, "experimental_mask_bleach_susceptibility", "实验性色罩漂白敏感度", 0.00, 1.00, row)
        row = self._scalar(controls, "experimental_mask_bleach_dye_damage", "实验性色罩漂白染料损伤", 0.00, 1.00, row)
        row = self._vector(controls, "retained_halide_density_rgb", "定影不足残留银盐 RGB 密度权重", 0.00, 1.50, row)
        row = self._scalar(controls, "auxiliary_layer_amount", "附加层初始量", 0.00, 1.00, row)
        row = self._vector(controls, "auxiliary_layer_density_rgb", "附加层 RGB 光学密度", 0.00, 0.50, row)
        row = self._scalar(controls, "material_degradation", "材料退化强度", 0.00, 1.00, row)
        row = self._scalar(controls, "degradation_speed_loss_stops", "满强度感光度损失 stop", 0.00, 2.00, row)
        row = self._vector(controls, "degradation_fog_density_rgb", "满强度退化底雾 RGB 密度", 0.00, 0.50, row)
        row = self._vector(controls, "degradation_layer_balance", "满强度退化层间感度平衡", 0.00, 1.50, row)
        row = self._vector(controls, "granularity_sigma", "颗粒密度 sigma RGB", 0.00, 0.08, row)
        row = self._scalar(controls, "grain_density_correlation_radius", "颗粒相关半径", 0.0003, 0.0040, row)
        row = self._scalar(controls, "silver_grain_strength", "实验性金属银颗粒强度", 0.00, 0.20, row)
        row = self._scalar(controls, "silver_grain_radius", "金属银颗粒相关半径", 0.0001, 0.0040, row)
        row = self._scalar(controls, "silver_grain_clump_mix", "金属银粗团聚混合", 0.00, 1.00, row)
        row = self._scalar(controls, "emulsion_mtf_strength", "乳剂 MTF 强度", 0.00, 0.70, row)
        row = self._scalar(controls, "emulsion_blur_radius", "乳剂模糊半径", 0.0002, 0.0050, row)
        row = self._scalar(controls, "high_frequency_threshold", "高频响应阈值", 0.00, 0.20, row)
        row = self._scalar(controls, "digital_artifact_suppression", "数字锐化抑制", 0.00, 0.60, row)

        row = self._section(controls, "片基边缘光导（实验）", row)
        row = self._scalar(controls, "light_piping_strength", "片基光导强度", 0.00, 0.50, row)
        row = self._scalar(controls, "light_piping_depth", "向内传播深度", 0.002, 0.25, row)
        ttk.Label(controls, text=ui("入光边缘", "Entry Edges")).grid(
            row=row,
            column=0,
            sticky="w",
            pady=2,
        )
        ttk.Combobox(
            controls,
            textvariable=self.light_piping_edge_display,
            values=tuple(LIGHT_PIPING_EDGE_LABELS),
            state="readonly",
            width=30,
        ).grid(row=row, column=1, columnspan=3, sticky="ew", pady=2)
        row += 1
        row = self._vector(
            controls,
            "light_piping_layer_weights",
            "光导材料层权重",
            0.00,
            2.00,
            row,
        )

        row = self._section(controls, "Halation 基准", row)
        row = self._scalar(controls, "halation_strength", "halation 强度", 0.00, 0.40, row)
        row = self._scalar(controls, "halation_threshold", "halation 阈值", 0.40, 1.20, row)
        row = self._scalar(controls, "halation_softness", "阈值软化", 0.02, 0.45, row)
        row = self._scalar(controls, "halation_core_radius", "短程散射半径", 0.0005, 0.0120, row)
        row = self._scalar(controls, "halation_outer_radius", "长程散射半径", 0.0030, 0.0500, row)
        row = self._scalar(controls, "halation_core_mix", "短程/长程混合", 0.00, 1.00, row)
        row = self._vector(controls, "halation_color", "光晕颜色 RGB", 0.00, 1.50, row)
        ttk.Label(controls, text=ui("回流模型", "Return Model")).grid(
            row=row,
            column=0,
            sticky="w",
            pady=2,
        )
        ttk.Combobox(
            controls,
            textvariable=self.halation_return_display,
            values=tuple(HALATION_RETURN_MODE_LABELS),
            state="readonly",
            width=30,
        ).grid(row=row, column=1, columnspan=3, sticky="ew", pady=2)
        row += 1
        row = self._vector(
            controls,
            "halation_layer_return_weights",
            "材料层回流相对权重",
            0.00,
            2.00,
            row,
        )
        row = self._vector(
            controls,
            "halation_spread_scale_weights",
            "传播尺度权重（紧邻 / 主回流 / 宽域）",
            0.00,
            1.00,
            row,
        )
        row = self._scalar(controls, "halation_source_blur_radius", "光源预模糊半径", 0.0001, 0.0060, row)
        row = self._scalar(controls, "halation_gradient_suppression", "边缘梯度抑制", 0.00, 1.00, row)
        row = self._scalar(controls, "halation_peak_radius", "局部峰值半径", 0.0010, 0.0300, row)
        row = self._scalar(controls, "halation_peak_threshold", "局部峰值阈值", 0.00, 0.60, row)
        row = self._scalar(controls, "halation_peak_softness", "局部峰值软化", 0.01, 0.40, row)
        row = self._scalar(controls, "halation_area_radius", "大面积亮区半径", 0.0050, 0.0800, row)
        row = self._scalar(controls, "halation_area_threshold", "大面积亮区阈值", 0.00, 0.80, row)
        row = self._scalar(controls, "halation_area_suppression", "大面积亮区抑制", 0.00, 1.00, row)
        row = self._scalar(controls, "halation_gaussian_amplitude", "高斯散射幅度", 0.00, 1.50, row)
        row = self._scalar(controls, "halation_exponential_amplitude", "指数散射幅度", 0.00, 1.50, row)
        row = self._scalar(controls, "halation_exponential_radius", "指数尾半径", 0.0030, 0.0600, row)

        row = self._section(controls, "三层感光 / 染料吸收 / 片基光谱耦合矩阵", row)
        row = self._matrix(controls, "layer_sensitivity_matrix", "layer sensitivity", 0.0, 1.4, row)
        row = self._matrix(controls, "dye_absorption_matrix", "dye absorption", 0.0, 1.6, row)
        row = self._matrix(controls, "base_dye_interaction_matrix", "base / dye spectral overlap", 0.0, 1.0, row)

        preview = ttk.LabelFrame(shell, text="曲线预览", padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.curve_label = ttk.Label(preview)
        self.curve_label.grid(row=0, column=0, sticky="nsew")
        ttk.Button(preview, text="刷新曲线", command=self._refresh_curve).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        ttk.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=980).pack(fill="x", side="bottom")
        self.controls_scroller.bind_mousewheel()
        localize_widget_tree(self.root, MATERIAL_EDITOR_EN)

    def _refresh_preset_choices(self) -> None:
        labels, mapping = film_preset_choices()
        self.preset_display_to_key = mapping
        self.preset_combo.configure(values=labels)
        key = self.preset_name.get()
        self.preset_display.set(next((label for label, value in mapping.items() if value == key), key))

    def _on_preset_selected(self, _event=None) -> None:
        self.preset_name.set(self.preset_display_to_key.get(self.preset_display.get(), self.preset_display.get()))

    def _section(self, parent: ttk.Frame, text: str, row: int) -> int:
        ttk.Label(parent, text=text, font=("", 10, "bold")).grid(row=row, column=0, columnspan=5, sticky="w", pady=(12, 4))
        return row + 1

    def _scalar(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        var = tk.DoubleVar(value=0.0)
        self.scalar_vars[key] = var
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        scale = ttk.Scale(
            parent,
            from_=min_value,
            to=max_value,
            variable=var,
            command=lambda _value: self._refresh_curve(),
        )
        scale.grid(row=row, column=1, columnspan=2, sticky="ew", pady=3)
        self.field_widgets[key] = scale
        ttk.Spinbox(
            parent,
            from_=min_value,
            to=max_value,
            increment=max((max_value - min_value) / 200.0, 0.0001),
            textvariable=var,
            width=10,
            justify="right",
            command=self._refresh_curve,
        ).grid(row=row, column=3, sticky="e", pady=3)
        var.trace_add("write", lambda *_: self._refresh_curve())
        parent.columnconfigure(1, weight=1)
        return row + 1

    def _vector(self, parent: ttk.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        vars_ = (tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0), tk.DoubleVar(value=0.0))
        self.vector_vars[key] = vars_
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

    def _apply_material_class(self) -> None:
        identity = material_identity_for_class(self.material_class.get())
        self.mode.set(identity["mode"])
        self.medium_family.set(SUPPORTED_MEDIUM_FAMILY)
        self.medium_process.set(identity["medium_process"])
        self.image_polarity.set(identity["image_polarity"])
        self.color_process.set(identity["color_process"])
        self.identity_summary.set(
            f"film / {identity['medium_process']} / {identity['image_polarity']} polarity / "
            f"{identity['color_process']}"
        )
        reversal_material = self.material_class.get() in {"color_reversal", "bw_reversal"}
        for key in POSITIVE_MATERIAL_FIELDS:
            widget = self.field_widgets.get(key)
            if widget is not None:
                widget.state(["!disabled"] if reversal_material else ["disabled"])
        self._refresh_curve()

    def _load_selected_preset(self) -> None:
        path = film_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(ui(f"找不到材料预设：{path}", f"Material preset not found: {path}"))
            return
        config = DarkroomConfig.from_json(path)
        supported = self._set_controls_from_config(config)
        source = "用户" if path.parent == USER_FILM_PRESET_DIR else "内置"
        if supported:
            self.status.set(ui(
                f"已加载{source}银盐材料预设：{path.name} · {self.material_class.get()}",
                f"Loaded {'user' if path.parent == USER_FILM_PRESET_DIR else 'built-in'} silver-halide material preset: {path.name} · {self.material_class.get()}",
            ))
        else:
            self.status.set(ui(
                f"已加载{source}材料预设：{path.name}；共享编辑器只支持银盐负片/反转片，不会改写其他介质。",
                f"Loaded material preset {path.name}; this shared editor supports silver-halide negative and reversal materials only.",
            ))
        self._refresh_curve()

    def _set_controls_from_config(self, config: DarkroomConfig) -> bool:
        supported = is_supported_silver_halide_material(config)
        self.material_name.set(str(config.film.name))
        self.material_class.set(material_class_for_config(config))
        self._apply_material_class()
        return_model = str(config.film.halation_return_model).strip().lower()
        self.halation_return_display.set(
            next(
                (
                    label
                    for label, value in HALATION_RETURN_MODE_LABELS.items()
                    if value == return_model
                ),
                next(iter(HALATION_RETURN_MODE_LABELS)),
            )
        )
        light_piping_mode = str(config.film.light_piping_edge_mode).strip().lower()
        self.light_piping_edge_display.set(
            next(
                (
                    label
                    for label, value in LIGHT_PIPING_EDGE_LABELS.items()
                    if value == light_piping_mode
                ),
                next(iter(LIGHT_PIPING_EDGE_LABELS)),
            )
        )
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
        return supported

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = film_preset_path(self.preset_name.get())
        config = DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig()
        config.film.name = self.material_name.get().strip() or "Custom Film Material"
        identity = material_identity_for_class(self.material_class.get())
        config.mode = identity["mode"]
        config.medium = f"{SUPPORTED_MEDIUM_FAMILY}_{identity['medium_process']}"
        config.film.medium_family = SUPPORTED_MEDIUM_FAMILY
        config.film.medium_process = identity["medium_process"]
        config.film.image_polarity = identity["image_polarity"]
        config.film.color_process = identity["color_process"]
        config.film.halation_return_model = HALATION_RETURN_MODE_LABELS.get(
            self.halation_return_display.get(),
            "compatibility_rgb",
        )
        config.film.light_piping_edge_mode = LIGHT_PIPING_EDGE_LABELS.get(
            self.light_piping_edge_display.get(),
            "none",
        )
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
            "medium": str(config.medium),
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
        density = material_preview_density(exposure, config)
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
        curve_kind = "positive density" if self.material_class.get().endswith("reversal") else "negative density"
        draw.text((12, y0 - 4), curve_kind, fill=(70, 66, 58))
        return image

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(config.film.name) or safe_preset_stem(self.preset_name.get()) or "custom_film_material"
        name = simpledialog.askstring(
            ui("保存胶片材料", "Save Film Material"),
            ui("请输入材料预设文件名（建议英文、数字、下划线）：", "Enter a material preset filename (letters, numbers, and underscores recommended):"),
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            messagebox.showerror(ui("无法保存", "Cannot Save"), ui("预设文件名不能为空，也不能只包含特殊字符。", "The preset filename cannot be empty or contain only special characters."), parent=self.root)
            return
        path = USER_FILM_PRESET_DIR / f"{stem}.json"
        existing = film_preset_path(stem)
        if existing.exists():
            if existing.parent == FILM_PRESET_DIR and not path.exists():
                detail = ui(
                    f"{stem}.json 是内置材料。保存后会创建同名用户材料，并在 GUI/CLI 中优先使用。",
                    f"{stem}.json is built in. Saving creates a same-name user material that takes precedence in the GUI and CLI.",
                )
            else:
                detail = ui(f"{path.name} 已存在。", f"{path.name} already exists.")
            if not messagebox.askyesno(ui("覆盖材料预设", "Overwrite Material Preset"), f"{detail}\n{ui('是否继续？', 'Continue?')}", parent=self.root):
                return
        save_json(path, self._film_payload(config))
        self.preset_name.set(stem)
        self._refresh_preset_choices()
        self.status.set(ui(f"已保存用户胶片材料：{path}", f"Saved user film material: {path}"))

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    FilmMaterialEditor().run()
