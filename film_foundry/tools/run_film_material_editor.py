"""Shared silver-halide film material preset editor for Film Foundry.

Negative and reversal stocks share the editor shell, but keep explicit native
material classes, identities, curve previews, and material-side controls.
"""

from __future__ import annotations

from dataclasses import asdict
import copy
import json
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import numpy as np
from PIL import Image, ImageDraw

from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.core.sensitometry import hd_density_curve
from film_foundry.core.film_process import shape_positive_density_fraction
from film_foundry.ui.components import add_numeric_control
from film_foundry.ui.qt import PhotoImage, message_box, text_dialog, ui
from film_foundry.ui.widgets import VerticalScrolledFrame
from film_foundry.ui.config_fields import OptionalNumberField, NumberListField, ProfileField, add_apply_button
from film_foundry.model.config import DarkroomConfig
from film_foundry.tools.editor_ui import local_text, localize_widget_tree, localized_preset_name, unique_choice_map
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "film_foundry" / "presets"
FILM_PRESET_DIR = PRESET_DIR / "film"
USER_FILM_PRESET_DIR = PROJECT_ROOT / "user_presets" / "film"
APP_TITLE = "Film Foundry - Silver-Halide Film Material Editor"
SUPPORTED_MEDIUM_FAMILY = "film"
MATERIAL_CLASS_SPECS: dict[str, dict[str, str]] = {
    'chromogenic_mono': {'medium_process':'negative','image_polarity':'negative','color_process':'chromogenic_monochrome','mode':'color_negative'},
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
DIR_MATERIAL_SCALAR_FIELDS = ("dir_diffusion_radius",)
DIR_MATERIAL_MATRIX_FIELDS = ("dir_material_response_matrix",)
HALATION_RETURN_MODE_LABELS = {
    local_text("兼容暖色 RGB 回注", "Compatible Warm RGB Return"): "compatibility_rgb",
    local_text("实验性材料层回流", "Experimental Material-Layer Return"): "layer_selective",
}
LIGHT_PIPING_EDGE_LABELS = {
    local_text("关闭", "Off"): "none",
    local_text("上边缘", "Top Edge"): "top",
    local_text("右边缘", "Right Edge"): "right",
    local_text("下边缘", "Bottom Edge"): "bottom",
    local_text("左边缘", "Left Edge"): "left",
    local_text("两条长边", "Both Long Edges"): "long_edges",
    local_text("两条短边", "Both Short Edges"): "short_edges",
    local_text("全部边缘", "All Edges"): "all_edges",
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
    "化学灰雾敏感度": "Chemical Fog Susceptibility",
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
    "乳剂内部高光扩散（实验，默认关闭）": "Emulsion-Internal Highlight Spread (Experimental, Off by Default)",
    "乳剂高光扩散强度": "Emulsion Highlight-Spread Strength",
    "乳剂高光扩散半径": "Emulsion Highlight-Spread Radius",
    "乳剂高光触发阈值": "Emulsion Highlight Threshold",
    "乳剂高光阈值软化": "Emulsion Highlight Threshold Softness",
    "逐层扩散半径倍率": "Per-Layer Spread Radius Scale",
    "逐层扩散强度倍率": "Per-Layer Spread Strength Scale",
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
    "DIR 材料响应（彩色材料，默认全零关闭）": "DIR Material Response (Color Materials, Off When Zero)",
    "DIR 扩散半径": "DIR Diffusion Radius",
    "源层→目标层有效响应": "Source→Target Effective Response",
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
    from film_foundry.app.seasonal_collection import visible_builtin_names
    names = visible_builtin_names(names, "film")
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
    from film_foundry.app.seasonal_collection import preset_path
    optional=preset_path("film",name)
    if optional is not None:
        return optional
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
    if color=='chromogenic_monochrome' and not reversal:return 'chromogenic_mono'
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
    def __init__(self, *, parent=None, config=None, on_apply=None, embedded=False) -> None:
        USER_FILM_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self._base_config = None
        self._on_apply = on_apply
        self.root = ui.Application() if parent is None else ui.Window(parent,embedded=embedded)
        self.root.title(local_text("Film Foundry · 材料编辑组件", "Film Foundry · Material editor component"))
        self.root.geometry("1080x780")
        self.root.minsize(860, 620)

        self.preset_name = ui.TextValue(value="clear_modern_negative")
        self.preset_display = ui.TextValue(value="")
        self.preset_display_to_key: dict[str, str] = {}
        self.material_name = ui.TextValue(value="")
        self.material_class = ui.TextValue(value="color_negative")
        self.mode = ui.TextValue(value="color_negative")
        self.medium_family = ui.TextValue(value="film")
        self.medium_process = ui.TextValue(value="negative")
        self.image_polarity = ui.TextValue(value="negative")
        self.color_process = ui.TextValue(value="color")
        self.identity_summary = ui.TextValue(value="film / negative / negative polarity / color")
        self.halation_return_display = ui.TextValue(value="")
        self.light_piping_edge_display = ui.TextValue(value="")
        self.status = ui.TextValue(value="")
        self.curve_photo = None

        self.scalar_vars: dict[str, ui.FloatValue] = {}
        self.vector_vars: dict[str, tuple[ui.FloatValue, ui.FloatValue, ui.FloatValue]] = {}
        self.matrix_vars: dict[str, list[list[ui.FloatValue]]] = {}
        self.matrix_inputs: dict[str, list] = {}
        self.field_widgets: dict[str, object] = {}
        self.field_controls: dict[str, tuple] = {}

        self._build()
        self._refresh_preset_choices()
        self.material_class.trace_add("write", lambda *_: self._apply_material_class())
        if config is None:
            self._load_selected_preset()
        else:
            self._set_controls_from_config(config)
            self._refresh_curve()

    def _build(self) -> None:
        shell = ui.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ui.Group(shell, text=local_text("胶片材料", "Film material"), padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ui.Label(header, text="基准材料").grid(row=0, column=0, sticky="w", pady=3)
        self.preset_combo = ui.Choice(header, textvariable=self.preset_display, state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", pady=3, padx=(8, 8))
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ui.Button(header, text="加载", command=self._load_selected_preset).grid(row=0, column=2, padx=(0, 8))
        ui.Button(header, text="保存为用户胶片材料", command=self._save_user_preset).grid(row=0, column=3)
        ui.Label(header, text="材料名称").grid(row=1, column=0, sticky="w", pady=3)
        ui.TextInput(header, textvariable=self.material_name).grid(row=1, column=1, columnspan=2, sticky="ew", pady=3, padx=(8, 8))
        from film_foundry.ui.qt import QtCore
        classes={'chromogenic_mono':('染料黑白负片','Chromogenic B&W'),
                 'color_negative':('彩色负片','Color negative'),'bw_negative':('银盐黑白负片','Silver B&W negative'),
                 'color_reversal':('彩色反转片','Color reversal'),'bw_reversal':('黑白反转片','B&W reversal')}
        keys=tuple(MATERIAL_CLASS_SPECS)
        choice=ui.Choice(header,values=tuple(local_text(*classes[k]) for k in keys),state='readonly',width=16)
        choice.grid(row=1,column=3,sticky='ew',pady=3)
        def display_class(value):
            with QtCore.QSignalBlocker(choice.qt):choice.qt.setCurrentIndex(keys.index(value) if value in keys else -1)
        self.material_class._bind_view(display_class,choice)
        choice.qt.currentIndexChanged.connect(lambda index:self.material_class.set(keys[index]) if index>=0 else None)
        ui.Label(header, text="当前介质").grid(row=2, column=0, sticky="w", pady=3)
        medium_row = ui.Frame(header)
        medium_row.grid(row=2, column=1, columnspan=3, sticky="ew", pady=3, padx=(8, 0))
        ui.Label(medium_row, textvariable=self.identity_summary).pack(side="left")
        header.columnconfigure(1, weight=1)
        header.set_tooltip(local_text("编辑银盐负片与反转片；拍立得、撕拉片和银版从图库选择介质后进入。", "Edit silver-halide negatives and reversal film; choose instant, peel-apart or plate from the library."))
        add_apply_button(header, self, row=4)

        controls_shell = ui.Frame(shell)
        controls_shell.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        controls_shell.rowconfigure(1, weight=1)
        controls_shell.columnconfigure(0, weight=1)

        from film_foundry.ui.qt import QtWidgets
        from film_foundry.ui.formation_execution import FormationExecutionBadge
        spectral_actions = ui.Frame(controls_shell)
        spectral_actions.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        toolbar = spectral_actions._ensure_pack_layout("left")
        self.formation_badge = FormationExecutionBadge()
        toolbar.addWidget(self.formation_badge)
        toolbar.addStretch(1)
        self.sensitivity_family = ui.TextValue(value=local_text('正色', 'Orthochromatic'))
        self.sensitivity_choices = {local_text('全色','Panchromatic'):'panchromatic', local_text('正色','Orthochromatic'):'orthochromatic', local_text('蓝敏','Blue-sensitive'):'blue_sensitive'}
        spectrum = QtWidgets.QToolButton()
        spectrum.setText(local_text('光谱', 'Spectra'))
        spectrum.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(spectrum)
        menu.addAction(local_text('开启七色升维', 'Enable seven-color lift'), self._enable_smits_exposure)
        menu.addAction(local_text('曝光感光谱…', 'Exposure sensitivity…'), lambda: self._edit_spectral_profile(False))
        menu.addAction(local_text('材料吸收谱…', 'Material absorbance…'), lambda: self._edit_spectral_profile(True))
        sensitivity = menu.addMenu(local_text('黑白感光类型', 'Monochrome sensitivity'))
        for label in self.sensitivity_choices:
            sensitivity.addAction(label, lambda label=label: (self.sensitivity_family.set(label), self._set_sensitivity_family()))
        spectrum.setMenu(menu)
        spectrum.setToolTip(local_text('感光谱与吸收谱独立配置；黑白感光类型是创作起点，不恢复源图未记录的紫外或红外。', 'Configure sensitivity and absorbance independently. Monochrome types are creative starting points, not recovered UV or IR.'))
        toolbar.addWidget(spectrum)
        tools = QtWidgets.QToolButton()
        tools.setText(local_text('材料', 'Material'))
        tools.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(tools)
        menu.addAction(local_text('工艺响应…', 'Process response…'), self._edit_process_response)
        menu.addAction(local_text('时间响应与储存…', 'Time response and storage…'), self._edit_time_history)
        menu.addAction(local_text('构建材料…', 'Compose material…'), self._compose_material)
        tools.setMenu(menu)
        toolbar.addWidget(tools)

        self.controls_scroller = VerticalScrolledFrame(controls_shell, canvas_width=560)
        self.controls_scroller.grid(row=1, column=0, sticky="nsew")
        controls = self.controls_scroller.content

        row = 0
        row = self._section(controls, local_text("材料尺度与谱定义", "Material geometry and spectral profiles"), row)
        self.spatial_reference_field = OptionalNumberField(controls, local_text("材料参考短边 mm（空=旧尺度）", "Material reference short edge mm (blank: legacy)"), row=row)
        row += 1
        self.exposure_profile_field = ProfileField(controls, "spectral_exposure_profile", row=row)
        row += 1
        self.material_profile_field = ProfileField(controls, "spectral_material_profile", row=row)
        row += 1
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
        self.grain_scales_field = NumberListField(controls, local_text("颗粒多尺度半径（分号分隔）", "Grain scale radii (semicolon-separated)"), row=row)
        row += 1
        self.grain_weights_field = NumberListField(controls, local_text("颗粒尺度权重（与半径对应）", "Grain scale weights (matching radii)"), row=row)
        row += 1
        row = self._scalar(controls, "silver_grain_strength", "实验性金属银颗粒强度", 0.00, 0.20, row)
        row = self._scalar(controls, "silver_grain_radius", "金属银颗粒相关半径", 0.0001, 0.0040, row)
        row = self._scalar(controls, "silver_grain_clump_mix", "金属银粗团聚混合", 0.00, 1.00, row)
        row = self._scalar(controls, "chemical_fog_susceptibility", "化学灰雾敏感度", 0.00, 10.00, row)
        self.field_widgets['chemical_fog_susceptibility'].set_tooltip(local_text(
            '逐浴模型下，未被曝光激活的银盐在显影液中的非成像转化敏感度。0 关闭化学灰雾，1 为基准；不改变片基本色或老化底雾。',
            'Non-image conversion of inactive halide in the bath-product model: 0 disables chemical fog, 1 is the reference. Support colour and ageing fog stay independent.'))
        row = self._scalar(controls, "emulsion_mtf_strength", "乳剂 MTF 强度", 0.00, 0.70, row)
        row = self._scalar(controls, "emulsion_blur_radius", "乳剂模糊半径", 0.0002, 0.0050, row)
        row = self._scalar(controls, "high_frequency_threshold", "高频响应阈值", 0.00, 0.20, row)
        row = self._scalar(controls, "digital_artifact_suppression", "数字锐化抑制", 0.00, 0.60, row)

        row = self._section(controls, "乳剂内部高光扩散（实验，默认关闭）", row)
        row = self._scalar(
            controls,
            "emulsion_highlight_spread_strength",
            "乳剂高光扩散强度",
            0.00,
            0.60,
            row,
        )
        row = self._scalar(
            controls,
            "emulsion_highlight_spread_radius",
            "乳剂高光扩散半径",
            0.0002,
            0.0080,
            row,
        )
        row = self._scalar(
            controls,
            "emulsion_highlight_spread_threshold",
            "乳剂高光触发阈值",
            0.20,
            1.40,
            row,
        )
        row = self._scalar(
            controls,
            "emulsion_highlight_spread_softness",
            "乳剂高光阈值软化",
            0.01,
            0.50,
            row,
        )
        row = self._vector(
            controls,
            "emulsion_highlight_spread_layer_radius_scales",
            "逐层扩散半径倍率",
            0.20,
            2.00,
            row,
        )
        row = self._vector(
            controls,
            "emulsion_highlight_spread_layer_strengths",
            "逐层扩散强度倍率",
            0.00,
            2.00,
            row,
        )

        row = self._section(controls, "片基边缘光导（实验）", row)
        row = self._scalar(controls, "light_piping_strength", "片基光导强度", 0.00, 0.50, row)
        row = self._scalar(controls, "light_piping_depth", "向内传播深度", 0.002, 0.25, row)
        ui.Label(controls, text=local_text("入光边缘", "Entry Edges")).grid(
            row=row,
            column=0,
            sticky="w",
            pady=2,
        )
        ui.Choice(
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
        ui.Label(controls, text=local_text("回流模型", "Return Model")).grid(
            row=row,
            column=0,
            sticky="w",
            pady=2,
        )
        ui.Choice(
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

        row = self._section(controls, "DIR 材料响应（彩色材料，默认全零关闭）", row)
        row = self._scalar(controls, "dir_diffusion_radius", "DIR 扩散半径", 0.0002, 0.0200, row)
        row = self._matrix(
            controls,
            "dir_material_response_matrix",
            "源层→目标层有效响应",
            0.0,
            0.95,
            row,
        )

        row = self._section(controls, "三层感光 / 染料吸收 / 片基光谱耦合矩阵", row)
        row = self._matrix(controls, "layer_sensitivity_matrix", "layer sensitivity", 0.0, 1.4, row)
        row = self._matrix(controls, "dye_absorption_matrix", "dye absorption", 0.0, 1.6, row)
        row = self._matrix(controls, "base_dye_interaction_matrix", "base / dye spectral overlap", 0.0, 1.0, row)

        preview = ui.Group(shell, text=local_text("材料预览", "Material preview"), padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        from film_foundry.ui.qt import QtWidgets
        from film_foundry.ui.material_preview import MaterialPreview
        curve_page = ui.Frame(preview)
        curve_page.columnconfigure(0, weight=1);curve_page.rowconfigure(0, weight=1)
        self.curve_label = ui.Label(curve_page)
        self.curve_label.grid(row=0, column=0, sticky="nsew")
        from film_foundry.ui.editor_navigator import FittedCurvePreview
        self.curve_fitter = FittedCurvePreview(self.curve_label.qt)
        ui.Button(curve_page, text="刷新曲线", command=self._refresh_curve).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.preview_tabs = QtWidgets.QTabWidget()
        self.preview_tabs.addTab(curve_page.qt,local_text("特性曲线","Characteristic curves"))
        self.quick_preview = MaterialPreview(self._preview_config)
        self.preview_tabs.addTab(self.quick_preview,local_text("效果预览","Image preview"))
        preview_layout = QtWidgets.QVBoxLayout(preview.qt)
        preview_layout.setContentsMargins(0,0,0,0);preview_layout.addWidget(self.preview_tabs)

        ui.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=980).pack(fill="x", side="bottom")
        self.controls_scroller.bind_mousewheel()
        localize_widget_tree(self.root, MATERIAL_EDITOR_EN)
        from film_foundry.ui.editor_navigator import install_editor_navigator, install_editor_splitter
        install_editor_navigator(self)
        install_editor_splitter(self, shell, controls_shell, preview)
        self.parameter_sections.setCurrentIndex(2)

    def set_execution_context(self, config):
        from film_foundry.ui.formation_execution import set_editor_execution_context
        set_editor_execution_context(self, config)
        if hasattr(self, 'quick_preview'):self.quick_preview.invalidate()

    def _preview_config(self):
        from film_foundry.app.config_assets import apply_recipe_bank
        draft = self._config_from_controls()
        return apply_recipe_bank(getattr(self, '_execution_config', draft), draft, 'film')

    def _refresh_preset_choices(self) -> None:
        labels, mapping = film_preset_choices()
        self.preset_display_to_key = mapping
        self.preset_combo.configure(values=labels)
        key = self.preset_name.get()
        self.preset_display.set(next((label for label, value in mapping.items() if value == key), key))

    def _on_preset_selected(self, _event=None) -> None:
        self.preset_name.set(self.preset_display_to_key.get(self.preset_display.get(), self.preset_display.get()))

    def _section(self, parent: ui.Frame, text: str, row: int) -> int:
        ui.Label(parent, text=text, font=("", 10, "bold")).grid(row=row, column=0, columnspan=5, sticky="w", pady=(12, 4))
        return row + 1

    def _scalar(self, parent: ui.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        from film_foundry.ui.film_parameter_help import field_label,describe_control
        var = ui.FloatValue(value=0.0)
        self.scalar_vars[key] = var
        control = add_numeric_control(
            parent,
            label=field_label(key,label),
            value=var,
            minimum=min_value,
            maximum=max_value,
            row=row,
            increment=max((max_value - min_value) / 200.0, 0.0001),
            input_width=10,
            on_change=self._refresh_curve,
            pady=3,
        )
        self.field_widgets[key] = control.slider
        self.field_controls[key] = (control.label, control.slider, control.number_input)
        describe_control(key,control.label,control.slider,control.number_input)
        return row + 1

    def _vector(self, parent: ui.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        from film_foundry.ui.film_parameter_help import field_label,describe_control
        vars_ = (ui.FloatValue(value=0.0), ui.FloatValue(value=0.0), ui.FloatValue(value=0.0))
        self.vector_vars[key] = vars_
        label_widget=ui.Label(parent,text=field_label(key,label))
        label_widget.grid(row=row,column=0,sticky='w',pady=3)
        describe_control(key,label_widget)

        vector_frame = ui.Frame(parent)
        vector_frame.grid(row=row, column=1, columnspan=3, sticky="ew", pady=3)
        for index, (channel, var) in enumerate(zip(("R", "G", "B"), vars_)):
            cell = ui.Frame(vector_frame)
            cell.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))
            cell.columnconfigure(1, weight=1)
            ui.Label(cell, text=channel, width=2, anchor="center").grid(row=0, column=0, sticky="w", padx=(0, 3))
            spin = ui.NumberInput(
                cell,
                from_=min_value,
                to=max_value,
                increment=(max_value - min_value) / 200.0,
                textvariable=var,
                width=9,
                justify="center",
            )
            spin.grid(row=0, column=1, sticky="ew")
            describe_control(key,spin)
            vector_frame.columnconfigure(index, weight=1, uniform="rgb_vector")
            var.trace_add("write", lambda *_: self._refresh_curve())
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(2, weight=1)
        parent.columnconfigure(3, weight=1)
        return row + 1

    def _matrix(self, parent: ui.Frame, key: str, label: str, min_value: float, max_value: float, row: int) -> int:
        from film_foundry.ui.film_parameter_help import describe_control
        title = ui.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        describe_control(key, title)
        matrix_frame=ui.Frame(parent)
        matrix_frame.grid(row=row,column=1,columnspan=3,rowspan=3,sticky="ew",pady=3)
        for column in range(3):matrix_frame.columnconfigure(column,weight=1)
        matrix: list[list[ui.FloatValue]] = []
        inputs: list = []
        for y in range(3):
            matrix_row = []
            for x in range(3):
                var = ui.FloatValue(value=0.0)
                spin = ui.NumberInput(matrix_frame, from_=min_value, to=max_value, increment=(max_value - min_value) / 200.0, textvariable=var, width=8)
                spin.grid(row=y, column=x, sticky="ew", padx=2, pady=2)
                describe_control(key, spin)
                var.trace_add("write", lambda *_: self._refresh_curve())
                matrix_row.append(var)
                inputs.append(spin)
            matrix.append(matrix_row)
        self.matrix_vars[key] = matrix
        self.matrix_inputs[key] = inputs
        return row + 3

    def _apply_material_class(self) -> None:
        identity = material_identity_for_class(self.material_class.get())
        self.mode.set(identity["mode"])
        self.medium_family.set(SUPPORTED_MEDIUM_FAMILY)
        self.medium_process.set(identity["medium_process"])
        self.image_polarity.set(identity["image_polarity"])
        self.color_process.set(identity["color_process"])
        color=local_text('彩色','Color') if identity['color_process']=='color' else local_text('染料黑白','Chromogenic B&W') if identity['color_process']=='chromogenic_monochrome' else local_text('银盐黑白','Silver B&W')
        polarity=local_text('正像','Positive') if identity['image_polarity']=='positive' else local_text('负像','Negative')
        self.identity_summary.set(color+' · '+polarity)
        reversal_material = self.material_class.get() in {"color_reversal", "bw_reversal"}
        color_material = self.material_class.get() in {"color_negative", "color_reversal", "chromogenic_mono"}
        for key in POSITIVE_MATERIAL_FIELDS:
            for widget in self.field_controls.get(key, ()):
                widget.state(["!disabled"] if reversal_material else ["disabled"])
        dir_state = ["!disabled"] if color_material else ["disabled"]
        for key in DIR_MATERIAL_SCALAR_FIELDS:
            for widget in self.field_controls.get(key, ()):
                widget.state(dir_state)
        for key in DIR_MATERIAL_MATRIX_FIELDS:
            for widget in self.matrix_inputs.get(key, []):
                widget.state(dir_state)
        self._refresh_curve()

    def _load_selected_preset(self) -> None:
        path = film_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(local_text(f"找不到材料预设：{path}", f"Material preset not found: {path}"))
            return
        config = DarkroomConfig.from_json(path)
        supported = self._set_controls_from_config(config)
        from film_foundry.app.seasonal_collection import localized_description
        from film_foundry.ui.i18n import current_language
        self.preset_combo.set_tooltip(localized_description('film',self.preset_name.get(),current_language()) if path.parent != USER_FILM_PRESET_DIR else '')
        source = "用户" if path.parent == USER_FILM_PRESET_DIR else "内置"
        if supported:
            self.status.set(local_text(
                f"已加载{source}银盐材料预设：{path.name} · {self.material_class.get()}",
                f"Loaded {'user' if path.parent == USER_FILM_PRESET_DIR else 'built-in'} silver-halide material preset: {path.name} · {self.material_class.get()}",
            ))
        else:
            self.status.set(local_text(
                f"已加载{source}材料预设：{path.name}；共享编辑器只支持银盐负片/反转片，不会改写其他介质。",
                f"Loaded material preset {path.name}; this shared editor supports silver-halide negative and reversal materials only.",
            ))
        self._refresh_curve()

    def _enable_smits_exposure(self):
        from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
        layers = 1 if self.material_class.get() in {"bw_negative", "bw_reversal"} else 3
        self.exposure_profile_field.load(make_smits_exposure_profile(layers))
        self.status.set(local_text("已开启七色升维。应用到当前任务后才会进入这次冲洗。", "Seven-color lift is on. Apply it to the current task before this development uses it."))

    def _set_sensitivity_family(self):
        if self.material_class.get() not in {'bw_negative','bw_reversal'}:
            self.status.set(local_text('请先选择黑白负片或黑白反转材料','Select a monochrome negative or reversal material first'));return
        from film_foundry.contracts.spectral_exposure import make_historical_exposure_profile
        self.exposure_profile_field.load(make_historical_exposure_profile(self.sensitivity_choices[self.sensitivity_family.get()]))
        self.status.set(local_text('感光类型已载入草稿，可编辑光谱后应用','Sensitivity loaded into the draft; edit its spectrum or apply'))

    def _edit_spectral_profile(self, material=False):
        from film_foundry.ui.spectral_editor import SpectralProfileDialog
        field = self.material_profile_field if material else self.exposure_profile_field
        layers = 1 if self.material_class.get() in {"bw_negative", "bw_reversal"} else 3
        dialog = SpectralProfileDialog(field.profile, material=material, layers=layers, parent=self.root.qt)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.root.qt,dialog,lambda:field.load(dialog.profile))

    from film_foundry.ui.editor_navigator import batch_parameter_update
    @batch_parameter_update('_refresh_curve')
    def _set_controls_from_config(self, config: DarkroomConfig) -> bool:
        self._base_config = copy.deepcopy(config)
        self.set_execution_context(getattr(self, '_execution_config', config))
        self._process_response=copy.deepcopy(config.film.process_response)
        self._carbon_backing=copy.deepcopy(config.film.carbon_backing)
        self._emulsion_integrity=copy.deepcopy(config.film.emulsion_integrity)
        self.spatial_reference_field.load(config.film.spatial_reference_short_edge_mm)
        self.grain_scales_field.load(config.film.grain_scales)
        self.grain_weights_field.load(config.film.grain_scale_weights)
        self.exposure_profile_field.load(config.film.spectral_exposure_profile)
        self.material_profile_field.load(config.film.spectral_material_profile)
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

    def _compose_material(self):
        from film_foundry.ui.material_design_dialog import MaterialDesignDialog
        dialog=MaterialDesignDialog(self._config_from_controls(),self.root.qt)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.root.qt,dialog,lambda:self._set_controls_from_config(dialog.config))

    def _edit_process_response(self):
        from film_foundry.ui.process_operations_editor import ProcessResponseDialog
        from film_foundry.ui.qt import QtWidgets
        film=self._config_from_controls().film
        dialog=ProcessResponseDialog(film.process_response,self.root.qt,carbon_backing=film.carbon_backing,emulsion_integrity=film.emulsion_integrity)
        def apply():
            self._process_response=copy.deepcopy(dialog.value)
            self._carbon_backing=copy.deepcopy(dialog.carbon_backing)
            self._emulsion_integrity=copy.deepcopy(dialog.emulsion_integrity)
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.root.qt,dialog,apply)

    def _edit_time_history(self):
        from film_foundry.ui.film_time_editor import FilmTimeDialog
        config=self._config_from_controls();dialog=FilmTimeDialog(config,self.root.qt,material=True)
        def apply():
            config.film.time_response=dialog.response;config.film.storage_history=dialog.storage_value
            config.film.reciprocity_response=dialog.reciprocity_value
            self._set_controls_from_config(config);self._refresh_curve()
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.root.qt,dialog,apply)

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = film_preset_path(self.preset_name.get())
        config = (copy.deepcopy(self._base_config) if self._base_config is not None
                  else DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig())
        config.film.spatial_reference_short_edge_mm = self.spatial_reference_field.read()
        config.film.grain_scales = self.grain_scales_field.read()
        config.film.grain_scale_weights = self.grain_weights_field.read()
        config.film.spectral_exposure_profile = self.exposure_profile_field.profile
        config.film.spectral_material_profile = self.material_profile_field.profile
        config.film.process_response = copy.deepcopy(getattr(self,'_process_response',config.film.process_response))
        config.film.carbon_backing = copy.deepcopy(getattr(self,'_carbon_backing',config.film.carbon_backing))
        config.film.emulsion_integrity = copy.deepcopy(getattr(self,'_emulsion_integrity',config.film.emulsion_integrity))
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
        if getattr(self, '_loading_parameters', False):return
        if hasattr(self, 'quick_preview'):self.quick_preview.invalidate()
        if not hasattr(self, "curve_label"):
            return
        try:
            config = self._config_from_controls()
            image = self._draw_hd_curve(config)
            self.curve_photo = PhotoImage(image)
            self.curve_fitter.set_pixmap(self.curve_photo.pixmap)
        except Exception:
            # Numeric typing can briefly create invalid floats; ignore until input stabilizes.
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
        for i in range(6):
            y = y1 - (y1-y0)*i/5
            draw.text((12, y-5), f'{y_max*i/5:.2f}', fill=(70,66,58))
        for i in range(5):
            x = x0 + (x1-x0)*i/4
            draw.text((x-7,y1+5), str(i-3), fill=(70,66,58))
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
        draw.text((12, y0 - 18), 'D', fill=(70, 66, 58))
        return image

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(config.film.name) or safe_preset_stem(self.preset_name.get()) or "custom_film_material"
        name = text_dialog.askstring(
            local_text("保存胶片材料", "Save Film Material"),
            local_text("请输入材料预设文件名（建议英文、数字、下划线）：", "Enter a material preset filename (letters, numbers, and underscores recommended):"),
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            message_box.showerror(local_text("无法保存", "Cannot Save"), local_text("预设文件名不能为空，也不能只包含特殊字符。", "The preset filename cannot be empty or contain only special characters."), parent=self.root)
            return
        path = USER_FILM_PRESET_DIR / f"{stem}.json"
        existing = film_preset_path(stem)
        if existing.exists():
            if existing.parent == FILM_PRESET_DIR and not path.exists():
                detail = local_text(
                    f"{stem}.json 是内置材料。保存后会创建同名用户材料，并在 GUI/CLI 中优先使用。",
                    f"{stem}.json is built in. Saving creates a same-name user material that takes precedence in the GUI and CLI.",
                )
            else:
                detail = local_text(f"{path.name} 已存在。", f"{path.name} already exists.")
            if not message_box.askyesno(local_text("覆盖材料预设", "Overwrite Material Preset"), f"{detail}\n{local_text('是否继续？', 'Continue?')}", parent=self.root):
                return
        save_json(path, self._film_payload(config))
        self.preset_name.set(stem)
        self._refresh_preset_choices()
        self.status.set(local_text(f"已保存用户胶片材料：{path}", f"Saved user film material: {path}"))

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    FilmMaterialEditor().run()
