"""Shared silver-halide film process-program editor for Film Foundry.

The editor covers reduced operators shared by negative and reversal film.  It
does not claim to edit instant, peel-apart, direct-positive, or plate chemistry.
Material-specific editors and scan/view editors remain separate.
"""

from __future__ import annotations

from dataclasses import asdict
import copy
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from film_foundry.core.accidents import (
    LIGHT_LEAK_CONFIG_TEMPLATE_KEYS,
    LIGHT_LEAK_ENTRY_SIDE_KEYS,
    LIGHT_LEAK_MIX_KEYS,
    LIGHT_LEAK_PLACEMENT_KEYS,
    LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS,
)
from film_foundry.core.atomic_io import atomic_write_json
from film_foundry.core.development import build_effective_development
from film_foundry.core.film_process.recipe import (
    FILM_PROCESS_PROGRAM_KEYS,
    program_from_develop_recipe,
)
from film_foundry.model.config import DEVELOP_LOOK_FIELDS, DarkroomConfig
from film_foundry.ui.components import add_choice_control, add_numeric_control
from film_foundry.ui.i18n import tr
from film_foundry.ui.qt import file_dialog, message_box, text_dialog, ui
from film_foundry.ui.widgets import VerticalScrolledFrame
from film_foundry.ui.config_fields import OptionalNumberField, add_apply_button
from film_foundry.tools.editor_ui import local_text, localize_widget_tree, localized_preset_name, unique_choice_map
from film_foundry.tools.paths import app_root, resource_root


PROJECT_ROOT = app_root()
RESOURCE_ROOT = resource_root()
PRESET_DIR = RESOURCE_ROOT / "film_foundry" / "presets"
DEVELOP_PRESET_DIR = PRESET_DIR / "develop"
USER_DEVELOP_PRESET_DIR = PROJECT_ROOT / "user_presets" / "develop"
APP_TITLE = "Film Foundry - Silver-Halide Film Process Program Editor"
SUPPORTED_MEDIUM_PROCESSES = {"", "film", "negative", "reversal", "slide", "positive"}
DEVELOP_RECIPE_SCALAR_FIELDS = (
    "time_min",
    "temperature_c",
    "concentration",
    "agitation",
    "push_stops",
    "developer_exhaustion",
    "fixer_exhaustion",
    "compensation",
    "silver_retention",
    "silver_plating",
    "light_leak_strength",
    "light_leak_center_x",
    "light_leak_center_y",
    "light_leak_rotation_degrees",
    "chemical_stain",
    "uneven_development",
    "reticulation_strength",
    "dye_damage_strength",
    "exposure_occlusion_dust",
    "exposure_occlusion_hair",
    "exposure_occlusion_insect",
    "exposure_occlusion_debris",
    "development_adjacency_strength",
    "development_adjacency_radius",
    "process_variation",
    "first_development_completion",
    "second_development_completion",
    "reversal_activation",
    "first_silver_removal",
    "silver_bleach_completion",
    "mask_bleach_completion",
    "halide_fixing_completion",
    "dye_coupling_efficiency",
    "auxiliary_removal",
)

DEVELOP_LOOK_CONTROL_DEFAULTS = {
    "exposure_ev": -0.05,
    "negative_contrast": 1.0,
    "saturation_multiplier": 1.0,
    "halation_multiplier": 1.0,
    "halation_sensitivity": 0.0,
    "grain_multiplier": 1.0,
    "grain_size_multiplier": 1.0,
    "look_strength": 1.0,
    "emulsion_mtf_strength": 0.25,
    "digital_artifact_suppression": 0.15,
    "halation_edge_compensation": 0.35,
}


def light_leak_template_labels() -> dict[str, str]:
    """Return localized display labels without leaking them into preset data."""

    return {
        key: tr(f"light_leak.template.{key}")
        for key in LIGHT_LEAK_CONFIG_TEMPLATE_KEYS
    }


def light_leak_template_label(key: str) -> str:
    labels = light_leak_template_labels()
    return labels.get(str(key), labels["auto"])


def light_leak_template_key_from_label(label: str) -> str:
    for key, localized in light_leak_template_labels().items():
        if label == localized:
            return key
    return "auto"


def _labeled_choice_map(prefix: str, keys: tuple[str, ...]) -> dict[str, str]:
    return {key: tr(f"{prefix}.{key}") for key in keys}


def light_leak_secondary_template_labels() -> dict[str, str]:
    return _labeled_choice_map("light_leak.secondary", LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS)


def light_leak_secondary_template_label(key: str) -> str:
    labels = light_leak_secondary_template_labels()
    return labels.get(str(key), labels["none"])


def light_leak_secondary_template_key_from_label(label: str) -> str:
    for key, localized in light_leak_secondary_template_labels().items():
        if label == localized:
            return key
    return "none"


def light_leak_mix_labels() -> dict[str, str]:
    return _labeled_choice_map("light_leak.mix", LIGHT_LEAK_MIX_KEYS)


def light_leak_mix_label(key: str) -> str:
    labels = light_leak_mix_labels()
    return labels.get(str(key), labels["max"])


def light_leak_mix_key_from_label(label: str) -> str:
    for key, localized in light_leak_mix_labels().items():
        if label == localized:
            return key
    return "max"


def light_leak_placement_labels() -> dict[str, str]:
    return _labeled_choice_map("light_leak.placement", LIGHT_LEAK_PLACEMENT_KEYS)


def light_leak_placement_label(key: str) -> str:
    labels = light_leak_placement_labels()
    return labels.get(str(key), labels["sampled"])


def light_leak_placement_key_from_label(label: str) -> str:
    for key, localized in light_leak_placement_labels().items():
        if label == localized:
            return key
    return "sampled"


def light_leak_entry_side_labels() -> dict[str, str]:
    return _labeled_choice_map("light_leak.entry", LIGHT_LEAK_ENTRY_SIDE_KEYS)


def light_leak_entry_side_label(key: str) -> str:
    labels = light_leak_entry_side_labels()
    return labels.get(str(key), labels["auto"])


def light_leak_entry_side_key_from_label(label: str) -> str:
    for key, localized in light_leak_entry_side_labels().items():
        if label == localized:
            return key
    return "auto"

DEVELOP_EDITOR_EN = {
    "银盐胶片工艺程序预设": "Silver-Halide Film Process Presets",
    "基准流程": "Base process",
    "加载": "Load",
    "保存为用户流程": "Save user process",
    "显影液名称": "Developer name",
    "定影/清除名称": "Fixer / clearing name",
    "程序色彩模式（不改材料）": "Program color mode (does not change material)",
    "工艺算子程序": "Process operator program",
    "共享范围：银盐负片 / 反转片 / 漂白旁路 / 交叉冲洗。auto 使用统一材料池；legacy_density 仅用于旧结果对照。黑白程序不会把彩色材料改写成黑白胶片；即时成像、撕拉片和银版使用独立工具。": "Scope: silver-halide negative, reversal, bleach bypass, and cross processing. Auto uses the unified material pools; legacy_density is for comparison only. Other media use separate tools.",
    "暗房流程与算子参数": "Darkroom Process and Operator Parameters",
    "显影液 / 药水类型": "Developer / Chemistry Type",
    "定影 / 清除类型": "Fixer / Clearing Type",
    "药水执行模式": "Chemistry Execution Mode",
    "画幅": "Frame Size",
    "冲洗时间 min": "Process Time (min)",
    "药水/环境温度 C": "Chemistry / Ambient Temperature (C)",
    "药水浓度 x": "Chemistry Concentration",
    "搅拌强度": "Agitation",
    "迫冲 / 欠冲 stop": "Push / Pull (stops)",
    "补偿显影": "Compensating Development",
    "显影液疲劳": "Developer Exhaustion",
    "定影疲劳 / 清除失败": "Fixer Exhaustion / Clearing Failure",
    "留银（降低漂白去银）": "Silver Retention",
    "表面镀银 / 银沉积事故": "Surface Silvering / Deposition Accident",
    "漏光形态": "Light-Leak Pattern",
    "漏光事故强度": "Light-Leak Accident Strength",
    "次级漏光形态": "Secondary Light-Leak Pattern",
    "漏光覆盖混合": "Light-Leak Coverage Mix",
    "漏光外部 mask": "External Light-Leak Mask",
    "漏光位置": "Light-Leak Placement",
    "漏光入口边": "Light-Leak Entry Side",
    "漏光中心 X": "Light-Leak Center X",
    "漏光中心 Y": "Light-Leak Center Y",
    "漏光旋转角度": "Light-Leak Rotation (degrees)",
    "漏光身份种子": "Light-Leak Identity Seed",
    "选择": "Browse",
    "灰尘（冲洗前遮挡）": "Dust (Pre-Develop Occlusion)",
    "毛发（冲洗前遮挡）": "Hair (Pre-Develop Occlusion)",
    "飞虫（冲洗前遮挡）": "Insects (Pre-Develop Occlusion)",
    "碎屑（冲洗前遮挡）": "Debris (Pre-Develop Occlusion)",
    "毛发/飞虫身份种子": "Hair / Insect Identity Seed",
    "海带 / 药染浑浊": "Chemical Stain / Turbidity",
    "显影不均 / 药痕": "Uneven Development / Chemical Marks",
    "乳剂网裂 / 温度冲击损伤": "Reticulation / Temperature-Shock Damage",
    "染料损伤（显式事故）": "Dye Damage (Explicit Accident)",
    "显影邻接强度（降阶）": "Development Adjacency Strength (Reduced)",
    "显影邻接半径": "Development Adjacency Radius",
    "批次 / 单张过程差异": "Batch / Frame Process Variation",
    "首次显影阶段完成度": "First Development Completion",
    "二次显影阶段完成度": "Second Development Completion",
    "反转激活完成度": "Reversal Activation Completion",
    "首次银像移除（黑白反转）": "First Silver Image Removal (B&W reversal)",
    "金属银漂白完成度": "Silver Bleach Completion",
    "实验性色罩 / 染料漂白": "Experimental Mask / Dye Bleach",
    "卤化银定影完成度": "Halide Fixing Completion",
    "染料偶合效率": "Dye Coupling Efficiency",
    "附加层去除能力": "Auxiliary Layer Removal",
    "感光层 1 反应平衡": "Layer 1 Process Balance",
    "感光层 2 反应平衡": "Layer 2 Process Balance",
    "感光层 3 反应平衡": "Layer 3 Process Balance",
    "曝光校准 EV": "Exposure Calibration EV",
    "负片反差校准": "Negative Contrast Calibration",
    "染料选择性 / 成色饱和": "Dye Selectivity / Coupling Saturation",
    "光晕校准": "Halation Calibration",
    "光晕感光倾向": "Halation Sensitivity Bias",
    "光晕边缘抑制": "Halation Edge Suppression",
    "颗粒强度校准": "Grain Strength Calibration",
    "颗粒尺寸校准": "Grain Size Calibration",
    "乳剂 MTF 强度": "Emulsion MTF Strength",
    "数字高频伪影抑制": "Digital High-Frequency Artifact Suppression",
    "整体形成效果强度": "Overall Formation Effect Strength",
    "有效冲洗状态预览": "Effective Process-State Preview",
    "刷新预览": "Refresh Preview",
}


def is_supported_film_process(config: DarkroomConfig) -> bool:
    recipe = config.chemistry
    medium_process = str(recipe.medium_process).strip().lower().replace("-", "_")
    program_key = str(getattr(recipe, "program_key", "auto")).strip().lower().replace("-", "_")
    return medium_process in SUPPORTED_MEDIUM_PROCESSES and program_key in FILM_PROCESS_PROGRAM_KEYS


def mode_for_program(program_key: str, current_mode: str) -> str:
    """Keep the material-response selector consistent with explicit programs."""
    key = str(program_key).strip().lower().replace("-", "_")
    if key.startswith("bw_"):
        return "bw_negative"
    if key.startswith("color_"):
        return "color_negative"
    return str(current_mode)


def process_mode_for_program(program_key: str, current_mode: str) -> str:
    key = str(program_key).strip().lower().replace("-", "_")
    if key in {"bw_reversal", "color_reversal"}:
        return "reversal"
    if key in {"bw_negative", "color_negative", "color_negative_bleach_bypass"}:
        return "normal_negative" if str(current_mode) == "reversal" else str(current_mode)
    return str(current_mode)


def bleach_completion_for_program(
    program_key: str,
    current_value: float,
    previous_program_key: str,
) -> float:
    """Provide useful defaults when the user changes topology interactively."""
    key = str(program_key).strip().lower()
    previous = str(previous_program_key).strip().lower()
    value = float(current_value)
    if key == "color_negative_bleach_bypass" and previous != key and value >= 0.99:
        return 0.20
    if key == "color_negative" and previous == "color_negative_bleach_bypass" and value <= 0.21:
        return 1.0
    return value


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
    supported: list[str] = []
    for name in sorted(names):
        path = develop_preset_path(name)
        try:
            if is_supported_film_process(DarkroomConfig.from_json(path)):
                supported.append(name)
        except Exception:
            continue
    return supported


def develop_preset_path(name: str) -> Path:
    user_path = USER_DEVELOP_PRESET_DIR / f"{name}.json"
    if user_path.exists():
        return user_path
    return DEVELOP_PRESET_DIR / f"{name}.json"


def develop_preset_choices() -> tuple[list[str], dict[str, str]]:
    items: list[tuple[str, str]] = []
    for key in develop_preset_names():
        path = develop_preset_path(key)
        try:
            fallback = str(DarkroomConfig.from_json(path).chemistry.developer_name).strip() or key
        except Exception:
            fallback = key
        items.append((key, localized_preset_name("develop", key, fallback, selected_path=path, builtin_dir=DEVELOP_PRESET_DIR)))
    return unique_choice_map(items)


def save_json(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


class DevelopProcessEditor:
    def __init__(self, *, parent=None, config=None, on_apply=None, embedded=False) -> None:
        USER_DEVELOP_PRESET_DIR.mkdir(parents=True, exist_ok=True)

        self._base_config = None
        self._on_apply = on_apply
        self.root = ui.Application() if parent is None else ui.Window(parent,embedded=embedded)
        self.root.title(local_text("Film Foundry · 工艺编辑组件", "Film Foundry · Process editor component"))
        self.root.geometry("1100x860")
        self.root.minsize(900, 620)

        self.preset_name = ui.TextValue(value="standard_color_negative")
        self.preset_display = ui.TextValue(value="")
        self.preset_display_to_key: dict[str, str] = {}
        self.developer_name = ui.TextValue(value="")
        self.fixer_name = ui.TextValue(value="")
        self.mode = ui.TextValue(value="color_negative")
        self.program_key = ui.TextValue(value="auto")
        self.history_labels = {local_text('逐浴局部颗粒响应', 'Local retained bath response'): 'bath_products_local_v2',
                               local_text('逐浴保留产物', 'Retained bath products'): 'bath_products_v1',
                               local_text('旧版整体近似', 'Legacy global approximation'): 'legacy_global_v1'}
        self.history_display = ui.TextValue(value=next(iter(self.history_labels)))
        self._last_program_key = "auto"
        self.developer_type = ui.TextValue(value="standard")
        self.fixer_type = ui.TextValue(value="standard")
        self.process_mode = ui.TextValue(value="normal_negative")
        self.frame_size = ui.TextValue(value="35mm")
        self.light_leak_template = ui.TextValue(value="auto")
        self.light_leak_template_display = ui.TextValue(
            value=light_leak_template_label("auto")
        )
        self.light_leak_secondary_template = ui.TextValue(value="none")
        self.light_leak_secondary_template_display = ui.TextValue(
            value=light_leak_secondary_template_label("none")
        )
        self.light_leak_mix = ui.TextValue(value="max")
        self.light_leak_mix_display = ui.TextValue(value=light_leak_mix_label("max"))
        self.light_leak_placement = ui.TextValue(value="sampled")
        self.light_leak_placement_display = ui.TextValue(
            value=light_leak_placement_label("sampled")
        )
        self.light_leak_entry_side = ui.TextValue(value="auto")
        self.light_leak_entry_side_display = ui.TextValue(
            value=light_leak_entry_side_label("auto")
        )
        self.light_leak_mask_path = ui.TextValue(value="")
        self.light_leak_identity_seed = ui.TextValue(value="")
        self.exposure_occlusion_identity_seed = ui.TextValue(value="")
        self.incident_identity_seed = ui.TextValue(value="")
        self.status = ui.TextValue(value="")
        self.preview_photo = None

        self.vars: dict[str, ui.FloatValue] = {
            "time_min": ui.FloatValue(value=8.0),
            "temperature_c": ui.FloatValue(value=20.0),
            "concentration": ui.FloatValue(value=1.0),
            "agitation": ui.FloatValue(value=1.0),
            "push_stops": ui.FloatValue(value=0.0),
            "developer_exhaustion": ui.FloatValue(value=0.0),
            "fixer_exhaustion": ui.FloatValue(value=0.0),
            "compensation": ui.FloatValue(value=0.0),
            "silver_retention": ui.FloatValue(value=0.0),
            "silver_plating": ui.FloatValue(value=0.0),
            "light_leak_strength": ui.FloatValue(value=0.0),
            "light_leak_center_x": ui.FloatValue(value=0.5),
            "light_leak_center_y": ui.FloatValue(value=0.5),
            "light_leak_rotation_degrees": ui.FloatValue(value=0.0),
            "chemical_stain": ui.FloatValue(value=0.0),
            "uneven_development": ui.FloatValue(value=0.0),
            "reticulation_strength": ui.FloatValue(value=0.0),
            "dye_damage_strength": ui.FloatValue(value=0.0),
            "exposure_occlusion_dust": ui.FloatValue(value=0.0),
            "exposure_occlusion_hair": ui.FloatValue(value=0.0),
            "exposure_occlusion_insect": ui.FloatValue(value=0.0),
            "exposure_occlusion_debris": ui.FloatValue(value=0.0),
            "development_adjacency_strength": ui.FloatValue(value=0.0),
            "development_adjacency_radius": ui.FloatValue(value=0.0025),
            "process_variation": ui.FloatValue(value=0.0),
            "first_development_completion": ui.FloatValue(value=1.0),
            "second_development_completion": ui.FloatValue(value=1.0),
            "reversal_activation": ui.FloatValue(value=1.0),
            "first_silver_removal": ui.FloatValue(value=1.0),
            "silver_bleach_completion": ui.FloatValue(value=1.0),
            "mask_bleach_completion": ui.FloatValue(value=0.0),
            "halide_fixing_completion": ui.FloatValue(value=1.0),
            "dye_coupling_efficiency": ui.FloatValue(value=1.0),
            "auxiliary_removal": ui.FloatValue(value=1.0),
            "process_layer_0": ui.FloatValue(value=1.0),
            "process_layer_1": ui.FloatValue(value=1.0),
            "process_layer_2": ui.FloatValue(value=1.0),
        }
        self.vars.update(
            {key: ui.FloatValue(value=value) for key, value in DEVELOP_LOOK_CONTROL_DEFAULTS.items()}
        )

        self._build()
        self._refresh_preset_choices()
        if config is None:
            self._load_selected_preset()
        else:
            self._set_controls_from_config(config)
            self._refresh_preview()

    def _build(self) -> None:
        shell = ui.Frame(self.root, padding=12)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=0)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ui.Group(shell, text=local_text("药液与流程", "Chemistry and process"), padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ui.Label(header, text="基准流程").grid(row=0, column=0, sticky="w", pady=3)
        self.preset_combo = ui.Choice(header, textvariable=self.preset_display, state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=3)
        self.preset_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ui.Button(header, text="加载", command=self._load_selected_preset).grid(row=0, column=2, padx=(0, 8))
        ui.Button(header, text="保存为用户流程", command=self._save_user_preset).grid(row=0, column=3)
        ui.Label(header, text="显影液名称").grid(row=1, column=0, sticky="w", pady=3)
        ui.TextInput(header, textvariable=self.developer_name).grid(row=1, column=1, sticky="ew", padx=(8, 8), pady=3)
        ui.Label(header, text="定影/清除名称").grid(row=2, column=0, sticky="w", pady=3)
        ui.TextInput(header, textvariable=self.fixer_name).grid(row=2, column=1, sticky="ew", padx=(8, 8), pady=3)
        ui.Label(header, text="程序色彩模式（不改材料）").grid(row=1, column=2, sticky="w", padx=(8, 4), pady=3)
        ui.Choice(
            header,
            textvariable=self.mode,
            values=("color_negative", "bw_negative"),
            state="readonly",
            width=16,
        ).grid(row=1, column=3, sticky="ew", pady=3)
        ui.Label(header, text="工艺算子程序").grid(row=2, column=2, sticky="w", padx=(8, 4), pady=3)
        ui.Choice(
            header,
            textvariable=self.program_key,
            values=FILM_PROCESS_PROGRAM_KEYS,
            state="readonly",
            width=28,
        ).grid(row=2, column=3, sticky="ew", pady=3)
        header.set_tooltip(local_text("银盐负片、反转片、漂白旁路与交叉冲洗；黑白程序不会改写材料身份。拍立得、撕拉片和银版使用各自流程。", "Silver-halide negative, reversal, bleach bypass and cross processing. B&W processing preserves material identity; instant, peel-apart and plates have separate workflows."))
        header.columnconfigure(1, weight=1)
        add_apply_button(header, self, row=4)
        ui.Button(header,text=local_text('编辑流程…','Edit process…'),command=self._edit_baths).grid(row=3,column=0,columnspan=2,sticky='ew',pady=3)
        ui.Button(header,text=local_text('潜影与受光…','Latent image and light…'),command=self._edit_time_history).grid(row=3,column=2,columnspan=2,sticky='ew',pady=3)

        controls_panel = ui.Group(shell, text="暗房流程与算子参数", padding=6)
        controls_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        controls_panel.columnconfigure(0, weight=1)
        from film_foundry.ui.formation_execution import FormationExecutionBadge
        self.formation_badge = FormationExecutionBadge()
        controls_panel._ensure_grid_layout().addWidget(self.formation_badge, 0, 0)
        controls_panel.rowconfigure(1, weight=1)
        self.controls_scroller = VerticalScrolledFrame(controls_panel, canvas_width=520)
        self.controls_scroller.grid(row=1, column=0, sticky="nsew")
        controls = self.controls_scroller.content
        controls.columnconfigure(1, weight=1)
        row = 0
        row = self._section(controls,local_text('显影液与形成模型','Developer and formation model'),row)
        history_choice = add_choice_control(controls, label=local_text('颗粒与化学灰雾', 'Grain and chemical fog'),
            value=self.history_display, choices=tuple(self.history_labels), row=row, on_change=self._refresh_preview)
        history_choice.set_tooltip(local_text(
            '逐浴模型将化学灰雾形成在真实银盐池中，并按保留产物汇总颗粒响应。旧配方保持整体近似；切换会改变形成结果。',
            'The bath model forms chemical fog in real halide pools and weights grain by retained products. Existing recipes retain their global approximation; switching changes formation.'))
        row += 1
        row = self._combo(controls, "显影液类型", self.developer_type, ("standard", "fine_grain", "compensating", "high_acutance", "high_contrast", "push", "exhausted", "monobath", "experimental_reversal"), row)
        row = self._combo(controls, "定影 / 清除类型", self.fixer_type, ("standard", "rapid", "hardening", "monobath", "experimental_mask_bleach"), row)
        row = self._combo(controls, "药水执行模式", self.process_mode, ("normal_negative", "reversal", "monobath"), row)
        row = self._combo(controls, "画幅", self.frame_size, ("half_frame", "35mm", "6x6", "6x7", "4x5"), row)
        self.frame_long_edge_field = OptionalNumberField(controls, local_text("实际画幅长边 mm（空=按画幅）", "Frame long edge mm (blank: frame size)"), row=row)
        row += 1
        self.dir_effectiveness_field = OptionalNumberField(controls, local_text("DIR 工艺有效性 0–1（空=按程序）", "DIR process effectiveness 0–1 (blank: program default)"), row=row)
        row += 1
        row = self._section(controls,local_text('工艺条件','Process conditions'),row)
        from film_foundry.contracts.process_steps import CONDITION_RANGES
        for key,label in (('time_min','显影时间 min'),('temperature_c','药液温度 °C'),('concentration','药液浓度倍数'),('agitation','搅拌强度'),('push_stops','迫冲／减冲 EV')):
            row=self._slider(controls,label,key,*CONDITION_RANGES[key],row)
        row = self._slider(controls, "补偿显影", "compensation", 0.0, 1.0, row)
        row = self._slider(controls, "显影液疲劳", "developer_exhaustion", 0.0, 1.0, row)
        row = self._slider(controls, "定影疲劳 / 清除失败", "fixer_exhaustion", 0.0, 1.0, row)
        row = self._slider(controls, "留银（降低漂白去银）", "silver_retention", 0.0, 1.0, row)
        row = self._slider(controls, "表面镀银 / 银沉积事故", "silver_plating", 0.0, 1.0, row)
        row = self._section(controls,local_text('漏光与曝光遮挡','Light leaks and exposure obstruction'),row)
        row = self._combo(
            controls,
            "漏光形态",
            self.light_leak_template_display,
            tuple(light_leak_template_labels().values()),
            row,
        )
        row = self._slider(controls, "漏光事故强度", "light_leak_strength", 0.0, 1.0, row)
        row = self._combo(
            controls,
            "次级漏光形态",
            self.light_leak_secondary_template_display,
            tuple(light_leak_secondary_template_labels().values()),
            row,
        )
        row = self._combo(
            controls,
            "漏光覆盖混合",
            self.light_leak_mix_display,
            tuple(light_leak_mix_labels().values()),
            row,
        )
        row = self._path_row(controls, "漏光外部 mask", self.light_leak_mask_path, row)
        row = self._combo(
            controls,
            "漏光位置",
            self.light_leak_placement_display,
            tuple(light_leak_placement_labels().values()),
            row,
        )
        row = self._combo(
            controls,
            "漏光入口边",
            self.light_leak_entry_side_display,
            tuple(light_leak_entry_side_labels().values()),
            row,
        )
        row = self._slider(controls, "漏光中心 X", "light_leak_center_x", 0.0, 1.0, row)
        row = self._slider(controls, "漏光中心 Y", "light_leak_center_y", 0.0, 1.0, row)
        row = self._slider(controls, "漏光旋转角度", "light_leak_rotation_degrees", -180.0, 180.0, row)
        row = self._text_row(controls, "漏光身份种子", self.light_leak_identity_seed, row)
        for key,zh,en in [("exposure_occlusion_dust","灰尘遮挡","Dust obstruction"),("exposure_occlusion_hair","毛发遮挡","Hair obstruction"),("exposure_occlusion_insect","飞虫遮挡","Insect obstruction"),("exposure_occlusion_debris","碎屑遮挡","Debris obstruction")]:
            ui.Toggle(controls,text=local_text(zh,en),variable=self.vars[key]).grid(row=row,column=0,columnspan=3,sticky="w",pady=3)
            row+=1
        row = self._text_row(controls, "毛发/飞虫身份种子", self.exposure_occlusion_identity_seed, row)
        row = self._text_row(controls, local_text("本卷／批次瑕疵种子（留空自动）", "Roll/batch defect seed (blank: automatic)"), self.incident_identity_seed, row)
        row = self._section(controls,local_text('污染与乳剂损伤','Contamination and emulsion damage'),row)
        row = self._slider(controls, "药液污染", "chemical_stain", 0.0, 1.0, row)
        row = self._slider(controls, "显影不均 / 药痕", "uneven_development", 0.0, 1.0, row)
        row = self._slider(controls, "乳剂网裂 / 温度冲击损伤", "reticulation_strength", 0.0, 1.0, row)
        row = self._slider(controls, "染料损伤（显式事故）", "dye_damage_strength", 0.0, 1.0, row)
        row = self._slider(controls, "显影邻接强度（降阶）", "development_adjacency_strength", 0.0, 1.0, row)
        row = self._slider(controls, "显影邻接半径", "development_adjacency_radius", 0.0002, 0.02, row)
        row = self._slider(controls, "批次 / 单张过程差异", "process_variation", 0.0, 1.0, row)
        row = self._section(controls,local_text('反应完成度','Reaction completion'),row)
        ui.Separator(controls).grid(row=row, column=0, columnspan=4, sticky="ew", pady=10)
        row += 1
        row = self._slider(controls, "首次显影阶段完成度", "first_development_completion", 0.0, 1.0, row)
        row = self._slider(controls, "二次显影阶段完成度", "second_development_completion", 0.0, 1.0, row)
        row = self._slider(controls, "反转激活完成度", "reversal_activation", 0.0, 1.0, row)
        row = self._slider(controls, "首次银像移除（黑白反转）", "first_silver_removal", 0.0, 1.0, row)
        row = self._slider(controls, "金属银漂白完成度", "silver_bleach_completion", 0.0, 1.0, row)
        row = self._slider(controls, "实验性色罩 / 染料漂白", "mask_bleach_completion", 0.0, 1.0, row)
        row = self._slider(controls, "卤化银定影完成度", "halide_fixing_completion", 0.0, 1.0, row)
        row = self._slider(controls, "染料偶合效率", "dye_coupling_efficiency", 0.0, 1.5, row)
        row = self._slider(controls, "附加层去除能力", "auxiliary_removal", 0.0, 1.0, row)
        row = self._slider(controls, "程序自身感光层 1 倍率", "process_layer_0", 0.25, 1.75, row)
        row = self._slider(controls, "程序自身感光层 2 倍率", "process_layer_1", 0.25, 1.75, row)
        row = self._slider(controls, "程序自身感光层 3 倍率", "process_layer_2", 0.25, 1.75, row)
        ui.Separator(controls).grid(row=row, column=0, columnspan=4, sticky="ew", pady=10)
        row += 1
        row = self._section(controls,local_text('形成参数校准','Formation calibration'),row)
        row = self._slider(controls, "曝光校准 EV", "exposure_ev", -2.0, 2.0, row)
        row = self._slider(controls, "负片反差校准", "negative_contrast", 0.65, 1.45, row)
        row = self._slider(controls, "染料选择性 / 成色饱和", "saturation_multiplier", 0.40, 1.80, row)
        row = self._slider(controls, "光晕校准", "halation_multiplier", 0.0, 2.0, row)
        row = self._slider(controls, "光晕感光倾向", "halation_sensitivity", -1.0, 1.0, row)
        row = self._slider(controls, "光晕边缘抑制", "halation_edge_compensation", 0.0, 1.0, row)
        row = self._slider(controls, "颗粒强度校准", "grain_multiplier", 0.3, 2.5, row)
        row = self._slider(controls, "颗粒尺寸校准", "grain_size_multiplier", 0.5, 2.0, row)
        row = self._slider(controls, "乳剂 MTF 强度", "emulsion_mtf_strength", 0.0, 1.0, row)
        row = self._slider(controls, "数字高频伪影抑制", "digital_artifact_suppression", 0.0, 1.0, row)
        row = self._slider(controls, "整体形成效果强度", "look_strength", 0.0, 2.0, row)

        preview = ui.Group(shell, text="有效冲洗状态预览", padding=10)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        from film_foundry.ui.process_state_view import ProcessStateView
        self.preview_table=ProcessStateView(preview.qt)
        preview._ensure_grid_layout().addWidget(self.preview_table,0,0)
        ui.Button(preview, text="刷新预览", command=self._refresh_preview).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.program_key.trace_add("write", self._sync_mode_to_program)
        self.controls_scroller.bind_mousewheel()

        ui.Label(self.root, textvariable=self.status, padding=(12, 0, 12, 8), wraplength=940).pack(fill="x", side="bottom")
        localize_widget_tree(self.root, DEVELOP_EDITOR_EN)
        from film_foundry.ui.editor_navigator import install_editor_navigator, install_editor_splitter
        install_editor_navigator(self)
        install_editor_splitter(self, shell, controls_panel, preview)

    def set_execution_context(self, config):
        from film_foundry.ui.formation_execution import set_editor_execution_context
        set_editor_execution_context(self, config)
        self._refresh_preview()

    def _refresh_preset_choices(self) -> None:
        labels, mapping = develop_preset_choices()
        self.preset_display_to_key = mapping
        self.preset_combo.configure(values=labels)
        key = self.preset_name.get()
        self.preset_display.set(next((label for label, value in mapping.items() if value == key), key))

    def _on_preset_selected(self, _event=None) -> None:
        self.preset_name.set(self.preset_display_to_key.get(self.preset_display.get(), self.preset_display.get()))

    def _sync_mode_to_program(self, *_args) -> None:
        key = self.program_key.get()
        resolved = mode_for_program(key, self.mode.get())
        if resolved != self.mode.get():
            self.mode.set(resolved)
        process_mode = process_mode_for_program(key, self.process_mode.get())
        if process_mode != self.process_mode.get():
            self.process_mode.set(process_mode)
        bleach = bleach_completion_for_program(
            key,
            self.vars["silver_bleach_completion"].get(),
            self._last_program_key,
        )
        if abs(bleach - self.vars["silver_bleach_completion"].get()) > 1e-9:
            self.vars["silver_bleach_completion"].set(bleach)
        self._last_program_key = str(key)
        self._refresh_preview()

    def _combo(self, parent: ui.Frame, label: str, var: ui.TextValue, values: tuple[str, ...], row: int) -> int:
        add_choice_control(
            parent,
            label=label,
            value=var,
            choices=values,
            row=row,
            on_change=self._refresh_preview,
        )
        return row + 1

    def _section(self,parent,label,row):
        ui.Label(parent,text=label,font=('',10,'bold')).grid(row=row,column=0,columnspan=4,sticky='w',pady=(12,4))
        return row+1

    def _slider(self, parent: ui.Frame, label: str, key: str, min_value: float, max_value: float, row: int) -> int:
        from film_foundry.ui.film_parameter_help import field_label,describe_control
        var = self.vars[key]
        control = add_numeric_control(
            parent,
            label=field_label(key,label),
            value=var,
            minimum=min_value,
            maximum=max_value,
            row=row,
            increment=max((max_value - min_value) / 200.0, 0.000001),
            on_change=self._refresh_preview,
        )
        describe_control(key,control.label,control.slider,control.number_input)
        return row + 1

    def _text_row(self, parent: ui.Frame, label: str, var: ui.TextValue, row: int) -> int:
        ui.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ui.TextInput(parent, textvariable=var)
        entry.grid(row=row, column=1, columnspan=3, sticky="ew", pady=4)
        var.trace_add("write", lambda *_: self._refresh_preview())
        return row + 1

    def _path_row(self, parent: ui.Frame, label: str, var: ui.TextValue, row: int) -> int:
        ui.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ui.TextInput(parent, textvariable=var).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=4
        )
        ui.Button(parent, text=local_text("选择", "Browse"), command=self._choose_light_leak_mask).grid(
            row=row, column=3, sticky="e", pady=4
        )
        var.trace_add("write", lambda *_: self._refresh_preview())
        return row + 1

    def _choose_light_leak_mask(self) -> None:
        path = file_dialog.askopenfilename(
            parent=self.root,
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.light_leak_mask_path.set(path)

    def _load_selected_preset(self) -> None:
        path = develop_preset_path(self.preset_name.get())
        if not path.exists():
            self.status.set(local_text(f"找不到冲洗流程预设：{path}", f"Process preset not found: {path}"))
            return
        config = DarkroomConfig.from_json(path)
        if not is_supported_film_process(config):
            self.status.set(local_text(f"该预设不属于银盐胶片工艺程序：{path.name}", f"This preset is not a silver-halide film process program: {path.name}"))
            return
        self._set_controls_from_config(config)
        source = "用户" if path.parent == USER_DEVELOP_PRESET_DIR else "内置"
        self.status.set(local_text(
            f"已加载{source}冲洗流程：{path.name}",
            f"Loaded {'user' if path.parent == USER_DEVELOP_PRESET_DIR else 'built-in'} process preset: {path.name}",
        ))
        self.controls_scroller.scroll_to_top()
        self._refresh_preview()

    from film_foundry.ui.editor_navigator import batch_parameter_update
    @batch_parameter_update('_refresh_preview')
    def _set_controls_from_config(self, config: DarkroomConfig) -> None:
        self._base_config = copy.deepcopy(config)
        self.set_execution_context(getattr(self, '_execution_config', config))
        self.frame_long_edge_field.load(config.chemistry.frame_long_edge_mm)
        self.dir_effectiveness_field.load(config.chemistry.dir_process_effectiveness)
        recipe = config.chemistry
        self.history_display.set(next(label for label, key in self.history_labels.items() if key == recipe.formation_history_model))
        self.mode.set(str(config.mode))
        self.program_key.set(str(getattr(recipe, "program_key", "auto")))
        self.developer_name.set(str(recipe.developer_name))
        self.fixer_name.set(str(recipe.fixer_name))
        self.developer_type.set(str(recipe.developer_type))
        self.fixer_type.set(str(recipe.fixer_type))
        self.process_mode.set(str(recipe.process_mode))
        self.frame_size.set(str(recipe.frame_size))
        self.light_leak_template.set(str(recipe.light_leak_template))
        self.light_leak_template_display.set(
            light_leak_template_label(recipe.light_leak_template)
        )
        self.light_leak_secondary_template.set(str(recipe.light_leak_secondary_template))
        self.light_leak_secondary_template_display.set(
            light_leak_secondary_template_label(recipe.light_leak_secondary_template)
        )
        self.light_leak_mix.set(str(recipe.light_leak_mix))
        self.light_leak_mix_display.set(light_leak_mix_label(recipe.light_leak_mix))
        self.light_leak_placement.set(str(recipe.light_leak_placement))
        self.light_leak_placement_display.set(
            light_leak_placement_label(recipe.light_leak_placement)
        )
        self.light_leak_entry_side.set(str(recipe.light_leak_entry_side))
        self.light_leak_entry_side_display.set(
            light_leak_entry_side_label(recipe.light_leak_entry_side)
        )
        self.light_leak_mask_path.set(str(recipe.light_leak_mask_path))
        identity_seed = recipe.light_leak_identity_seed
        self.light_leak_identity_seed.set(
            "" if identity_seed is None else str(int(identity_seed))
        )
        occlusion_seed = recipe.exposure_occlusion_identity_seed
        self.exposure_occlusion_identity_seed.set(
            "" if occlusion_seed is None else str(int(occlusion_seed))
        )
        self.incident_identity_seed.set("" if recipe.incident_identity_seed is None else str(recipe.incident_identity_seed))
        for key in DEVELOP_RECIPE_SCALAR_FIELDS:
            self.vars[key].set(float(getattr(recipe, key)))
        for index, value in enumerate(recipe.process_layer_balance):
            self.vars[f"process_layer_{index}"].set(float(value))
        for key in DEVELOP_LOOK_FIELDS:
            if key in self.vars:
                value = getattr(config.look, key)
                if value is None:
                    fallback_fields = {
                        "emulsion_mtf_strength": "emulsion_mtf_strength",
                        "digital_artifact_suppression": "digital_artifact_suppression",
                        "halation_edge_compensation": "halation_gradient_suppression",
                    }
                    film_field = fallback_fields.get(key)
                    if film_field is not None:
                        value = getattr(config.film, film_field)
                if value is not None:
                    self.vars[key].set(float(value))

    def _config_from_controls(self) -> DarkroomConfig:
        base_path = develop_preset_path(self.preset_name.get())
        config = (copy.deepcopy(self._base_config) if self._base_config is not None
                  else DarkroomConfig.from_json(base_path) if base_path.exists() else DarkroomConfig())
        config.chemistry.frame_long_edge_mm = self.frame_long_edge_field.read()
        config.chemistry.dir_process_effectiveness = self.dir_effectiveness_field.read()
        recipe = config.chemistry
        recipe.formation_history_model = self.history_labels[self.history_display.get()]
        recipe.program_key = str(self.program_key.get())
        config.mode = mode_for_program(recipe.program_key, self.mode.get())
        self.mode.set(config.mode)
        reversal = recipe.program_key in {"bw_reversal", "color_reversal"}
        recipe.medium_process = "reversal" if reversal else "negative"
        config.medium = "film_reversal" if reversal else "film_negative"
        recipe.developer_name = self.developer_name.get().strip() or str(self.developer_type.get()).replace("_", " ").title()
        recipe.fixer_name = self.fixer_name.get().strip() or str(self.fixer_type.get()).replace("_", " ").title()
        recipe.developer_type = str(self.developer_type.get())
        recipe.fixer_type = str(self.fixer_type.get())
        recipe.process_mode = process_mode_for_program(
            recipe.program_key,
            self.process_mode.get(),
        )
        self.process_mode.set(recipe.process_mode)
        recipe.frame_size = str(self.frame_size.get())
        recipe.light_leak_template = light_leak_template_key_from_label(
            self.light_leak_template_display.get()
        )
        self.light_leak_template.set(recipe.light_leak_template)
        recipe.light_leak_secondary_template = light_leak_secondary_template_key_from_label(
            self.light_leak_secondary_template_display.get()
        )
        self.light_leak_secondary_template.set(recipe.light_leak_secondary_template)
        recipe.light_leak_mix = light_leak_mix_key_from_label(
            self.light_leak_mix_display.get()
        )
        self.light_leak_mix.set(recipe.light_leak_mix)
        recipe.light_leak_placement = light_leak_placement_key_from_label(
            self.light_leak_placement_display.get()
        )
        self.light_leak_placement.set(recipe.light_leak_placement)
        recipe.light_leak_entry_side = light_leak_entry_side_key_from_label(
            self.light_leak_entry_side_display.get()
        )
        self.light_leak_entry_side.set(recipe.light_leak_entry_side)
        recipe.light_leak_mask_path = str(self.light_leak_mask_path.get()).strip()
        identity_text = str(self.light_leak_identity_seed.get()).strip()
        recipe.light_leak_identity_seed = None if identity_text == "" else int(identity_text)
        occlusion_identity_text = str(self.exposure_occlusion_identity_seed.get()).strip()
        recipe.exposure_occlusion_identity_seed = (
            None if occlusion_identity_text == "" else int(occlusion_identity_text)
        )
        incident_text=self.incident_identity_seed.get().strip()
        recipe.incident_identity_seed=None if not incident_text else int(incident_text)
        for key in DEVELOP_RECIPE_SCALAR_FIELDS:
            setattr(recipe, key, float(self.vars[key].get()))
        recipe.process_layer_balance = tuple(
            float(self.vars[f"process_layer_{index}"].get()) for index in range(3)
        )
        for key in DEVELOP_LOOK_FIELDS:
            if key in self.vars:
                setattr(config.look, key, float(self.vars[key].get()))
        return config

    def _preset_payload(self, config: DarkroomConfig) -> dict:
        from film_foundry.app.preset_exchange import current_items
        item=next(item for item in current_items(config) if item['kind']=='develop')
        return {**item['payload'],'preset':{'kind':'develop','version':1,'name':item['name']}}

    def _refresh_preview(self) -> None:
        if getattr(self, '_loading_parameters', False):return
        if not hasattr(self, "preview_table"):
            return
        try:
            config = self._config_from_controls()
            from film_foundry.core.implementation_plan import resolve_implementation_plan
            plan = resolve_implementation_plan(config)
            if plan.basic_formation_only:
                from film_foundry.core.basic_formation import basic_formation_config
                config, _ = basic_formation_config(config)
            state = build_effective_development(config.chemistry)
            program = program_from_develop_recipe(
                config.chemistry,
                mode=config.mode,
                material_process=config.chemistry.medium_process,
                coupled_intervals=plan.coupled_intervals,
            )
            self.preview_table.update_state(state,program,config.chemistry,
                                            local_grain_response=plan.local_grain_response_enabled)
            self.preview_table.setToolTip(self.formation_badge.toolTip() + '\n' + local_text(
                '这里是实际执行配方的参考反应量，不是最终成片测量。',
                'Reference reaction amounts for the execution recipe, not measurements of the finished image.'))
        except (TypeError,ValueError) as exc:
            self.status.set(str(exc))

    def _edit_baths(self):
        from film_foundry.ui.roll_process_dialog import RollProcessDialog
        config=self._config_from_controls();dialog=RollProcessDialog(config,self.root.qt,principle_only=False)
        def apply():
            config.chemistry=copy.deepcopy(dialog.config.chemistry)
            self._set_controls_from_config(config);self._refresh_preview()
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.root.qt,dialog,apply)

    def _edit_time_history(self):
        from film_foundry.ui.film_time_editor import FilmTimeDialog
        config=self._config_from_controls();dialog=FilmTimeDialog(config,self.root.qt)
        def apply():
            config.chemistry.latent_wait=dialog.wait_value;config.chemistry.post_process_light=dialog.light_value
            self._set_controls_from_config(config);self._refresh_preview()
        from film_foundry.ui.inline_editor import present_inline_editor
        present_inline_editor(self.root.qt,dialog,apply)

    def _save_user_preset(self) -> None:
        config = self._config_from_controls()
        initial = safe_preset_stem(config.chemistry.developer_name) or safe_preset_stem(self.preset_name.get()) or "custom_develop"
        name = text_dialog.askstring(
            local_text("保存冲洗流程", "Save Process Preset"),
            local_text("请输入流程预设文件名（建议英文、数字、下划线）：", "Enter a process preset filename (letters, numbers, and underscores recommended):"),
            parent=self.root,
            initialvalue=initial,
        )
        if not name:
            return
        stem = safe_preset_stem(name)
        if not stem:
            message_box.showerror(local_text("无法保存", "Cannot Save"), local_text("预设文件名不能为空，也不能只包含特殊字符。", "The preset filename cannot be empty or contain only special characters."), parent=self.root)
            return
        path = USER_DEVELOP_PRESET_DIR / f"{stem}.json"
        existing = develop_preset_path(stem)
        if existing.exists():
            if existing.parent == DEVELOP_PRESET_DIR and not path.exists():
                detail = local_text(
                    f"{stem}.json 是内置流程。保存后会创建同名用户流程，并在 GUI/CLI 中优先使用。",
                    f"{stem}.json is built in. Saving creates a same-name user process that takes precedence in the GUI and CLI.",
                )
            else:
                detail = local_text(f"{path.name} 已存在。", f"{path.name} already exists.")
            if not message_box.askyesno(local_text("覆盖冲洗流程", "Overwrite Process Preset"), f"{detail}\n{local_text('是否继续？', 'Continue?')}", parent=self.root):
                return
        save_json(path, self._preset_payload(config))
        self.preset_name.set(stem)
        self._refresh_preset_choices()
        self.status.set(local_text(f"已保存用户冲洗流程：{path}", f"Saved user process preset: {path}"))

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    DevelopProcessEditor().run()


# Public semantic alias; keep the old class name for launcher and script compatibility.
FilmProcessProgramEditor = DevelopProcessEditor
