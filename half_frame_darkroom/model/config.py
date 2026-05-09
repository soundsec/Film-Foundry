"""Film Foundry 配置对象。

配置分为五层：
- FilmStockConfig：胶片本体
- ChemistryConfig：显影条件
- ScannerConfig：扫描/打印解释
- LookAdjustConfig：GUI/脚本滑块微调
- OutputConfig：文件输出

from_dict() 会迁移旧 preset 中混放在 film/output/top-level 的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import copy
import json


Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Vector3 = tuple[float, float, float]


@dataclass(slots=True)
class FilmStockConfig:
    """胶片本体属性。"""

    name: str = "Generic Color Negative"

    halation_strength: float = 0.12
    halation_threshold: float = 0.72
    halation_softness: float = 0.18
    halation_core_radius: float = 0.004
    halation_outer_radius: float = 0.018
    halation_core_mix: float = 0.62
    halation_color: Vector3 = (1.0, 0.42, 0.16)

    color_matrix: Matrix3 = (
        (1.05, -0.03, -0.02),
        (-0.04, 1.02, 0.02),
        (0.01, -0.06, 1.05),
    )
    base_fog: float = 0.018
    contrast: float = 1.08
    toe_strength: float = 0.18
    shoulder_strength: float = 0.30
    shoulder_point: float = 0.72
    saturation: float = 0.96

    grain_strength: float = 0.035
    grain_scales: tuple[float, ...] = (0.0012, 0.0035, 0.010)
    grain_scale_weights: tuple[float, ...] = (0.58, 0.30, 0.12)
    grain_midtone_mu: float = 0.48
    grain_midtone_sigma: float = 0.22

    emulsion_mtf_strength: float = 0.25
    emulsion_blur_radius: float = 0.0012
    high_frequency_threshold: float = 0.030
    digital_artifact_suppression: float = 0.15

    halation_source_blur_radius: float = 0.0010
    halation_gradient_suppression: float = 0.35

    hd_gamma: Vector3 = (0.62, 0.66, 0.60)
    density_min: Vector3 = (0.10, 0.10, 0.10)
    density_max: Vector3 = (1.85, 1.95, 1.75)
    log_exposure_toe: Vector3 = (-2.20, -2.15, -2.25)
    log_exposure_shoulder: Vector3 = (0.45, 0.48, 0.42)
    hd_toe_width: float = 0.18
    hd_shoulder_width: float = 0.22

    layer_sensitivity_matrix: Matrix3 = (
        (0.82, 0.14, 0.04),
        (0.10, 0.78, 0.12),
        (0.05, 0.20, 0.75),
    )
    dye_absorption_matrix: Matrix3 = (
        (1.00, 0.10, 0.04),
        (0.08, 1.00, 0.12),
        (0.03, 0.16, 1.00),
    )

    # 彩色负片片基/橙色 mask 的 RGB 光学密度。
    film_base_density_rgb: Vector3 = (0.18, 0.55, 0.85)

    granularity_sigma: Vector3 = (0.030, 0.028, 0.032)
    grain_density_correlation_radius: float = 0.0014

    halation_gaussian_amplitude: float = 0.62
    halation_exponential_amplitude: float = 0.38
    halation_exponential_radius: float = 0.022


@dataclass(slots=True)
class ChemistryConfig:
    """显影条件。"""

    push_stops: float = 0.0
    temperature_c: float = 20.0
    developer_exhaustion: float = 0.0


@dataclass(slots=True)
class ScannerConfig:
    """扫描/打印解释。"""

    scanner_light_color: Vector3 = (1.0, 1.0, 1.0)
    scanner_response_matrix: Matrix3 = (
        (1.00, 0.00, 0.00),
        (0.00, 1.00, 0.00),
        (0.00, 0.00, 1.00),
    )
    scan_method: str = "negative_inversion"
    scan_base_percentile: float = 99.5

    print_reference_density: Vector3 = (1.58, 1.61, 1.53)
    print_gamma: float = 0.95
    print_mapping_mode: str = "printlike"
    print_color_bias: Vector3 = (1.0, 1.0, 1.0)
    print_color_shift: Vector3 = (0.0, 0.0, 0.0)
    highlight_color_bias: Vector3 = (1.0, 1.0, 1.0)
    highlight_bias_threshold: float = 0.72
    highlight_bias_softness: float = 0.18
    # 扫描/输出阶段的色彩浓度。它不改变底片染料密度，只改变正像解释。
    scan_saturation: float = 1.0

    scan_normalize: bool = True
    scan_normalize_strength: float = 0.15
    scan_normalize_mode: str = "luma"
    scan_black_percentile: float = 0.3
    scan_white_percentile: float = 99.7


@dataclass(slots=True)
class LookAdjustConfig:
    """GUI/脚本滑块微调，不属于胶片或扫描器本体。"""

    exposure_ev: float = 0.0
    negative_contrast: float = 1.0
    print_contrast: float = 1.0
    print_exposure_ev: float = 0.0
    saturation_multiplier: float = 1.0
    halation_multiplier: float = 1.0
    # 颗粒强度控制密度扰动幅度；颗粒尺寸控制相关噪声半径，仍按画幅比例换算像素。
    grain_multiplier: float = 1.0
    grain_size_multiplier: float = 1.0
    look_strength: float = 1.0

    emulsion_mtf_strength: float | None = None
    digital_artifact_suppression: float | None = None
    halation_edge_compensation: float | None = None


@dataclass(slots=True)
class OutputConfig:
    """文件输出。"""

    format: str = "png"
    quality: int = 95
    bit_depth: int = 8
    render_long_edge: int | None = None
    preview_long_edge: int | None = 1600
    save_scanner_raw: bool = True
    scanner_raw_border_percent: float = 0.04
    scanner_raw_border_min_px: int = 32
    export_layer_pack: bool = False
    export_transparent_plate: bool = True
    export_plate_set: bool = True


@dataclass(slots=True)
class DarkroomConfig:
    """单张图像处理总配置。"""

    film: FilmStockConfig = field(default_factory=FilmStockConfig)
    chemistry: ChemistryConfig = field(default_factory=ChemistryConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    look: LookAdjustConfig = field(default_factory=LookAdjustConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    random_seed: int | None = None
    seed_strategy: str = "random"
    fast_mode: bool = False
    enable_mtf: bool = True
    enable_halation: bool = True
    enable_grain: bool = True
    enable_subtractive: bool = True
    mode: str = "color_negative"
    debug_output: bool = False
    save_sidecar: bool = True
    comparison_grid: bool = False

    @property
    def exposure_ev(self) -> float:
        return self.look.exposure_ev

    @exposure_ev.setter
    def exposure_ev(self, value: float) -> None:
        self.look.exposure_ev = float(value)

    @property
    def print_contrast(self) -> float:
        return self.look.print_contrast

    @print_contrast.setter
    def print_contrast(self, value: float) -> None:
        self.look.print_contrast = float(value)

    @property
    def print_exposure_ev(self) -> float:
        return self.look.print_exposure_ev

    @print_exposure_ev.setter
    def print_exposure_ev(self, value: float) -> None:
        self.look.print_exposure_ev = float(value)

    @property
    def look_strength(self) -> float:
        return self.look.look_strength

    @look_strength.setter
    def look_strength(self, value: float) -> None:
        self.look.look_strength = float(value)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DarkroomConfig":
        film_data = dict(data.get("film", {}))
        scanner_data = dict(data.get("scanner", {}))
        output_data = dict(data.get("output", {}))
        look_data = dict(data.get("look", {}))

        # 旧 preset 迁移：这些字段过去混在 film 中。
        scanner_keys = {
            "scanner_light_color",
            "scanner_response_matrix",
            "print_reference_density",
            "print_gamma",
            "print_mapping_mode",
            "print_color_bias",
            "print_color_shift",
            "highlight_color_bias",
            "highlight_bias_threshold",
            "highlight_bias_softness",
            "scan_saturation",
        }
        for key in list(film_data):
            if key in scanner_keys:
                scanner_data.setdefault(key, film_data.pop(key))

        # 旧 preset 迁移：扫描归一化曾经在 output 中。
        scanner_output_keys = {
            "scan_method",
            "scan_base_percentile",
            "scan_normalize",
            "scan_normalize_strength",
            "scan_normalize_mode",
            "scan_black_percentile",
            "scan_white_percentile",
        }
        for key in list(output_data):
            if key in scanner_output_keys:
                scanner_data.setdefault(key, output_data.pop(key))

        if "preview_size" in output_data:
            output_data.setdefault("render_long_edge", output_data.pop("preview_size"))

        legacy_print_contrast = data.get("print_contrast", data.get("print_density", 1.0))
        legacy_look = {
            "exposure_ev": data.get("exposure_ev", 0.0),
            "print_contrast": legacy_print_contrast,
            "print_exposure_ev": data.get("print_exposure_ev", 0.0),
            "grain_multiplier": data.get("grain_multiplier", data.get("grain_strength_multiplier", 1.0)),
            "grain_size_multiplier": data.get("grain_size_multiplier", 1.0),
            "look_strength": data.get("look_strength", 1.0),
        }
        for key, value in legacy_look.items():
            look_data.setdefault(key, value)

        return cls(
            film=FilmStockConfig(**film_data),
            chemistry=ChemistryConfig(**data.get("chemistry", {})),
            scanner=ScannerConfig(**scanner_data),
            look=LookAdjustConfig(**look_data),
            output=OutputConfig(**output_data),
            random_seed=data.get("random_seed"),
            seed_strategy=str(data.get("seed_strategy", "random")),
            fast_mode=bool(data.get("fast_mode", False)),
            enable_mtf=bool(data.get("enable_mtf", True)),
            enable_halation=bool(data.get("enable_halation", True)),
            enable_grain=bool(data.get("enable_grain", True)),
            enable_subtractive=bool(data.get("enable_subtractive", True)),
            mode=str(data.get("mode", "color_negative")),
            debug_output=bool(data.get("debug_output", False)),
            save_sidecar=bool(data.get("save_sidecar", True)),
            comparison_grid=bool(data.get("comparison_grid", False)),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "DarkroomConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))


DEVELOP_LOOK_FIELDS = (
    "exposure_ev",
    "negative_contrast",
    "saturation_multiplier",
    "halation_multiplier",
    "grain_multiplier",
    "grain_size_multiplier",
    "look_strength",
    "emulsion_mtf_strength",
    "digital_artifact_suppression",
    "halation_edge_compensation",
)

SCAN_LOOK_FIELDS = (
    "print_contrast",
    "print_exposure_ev",
)


def merge_config_presets(
    film_config: DarkroomConfig | None = None,
    scanner_config: DarkroomConfig | None = None,
) -> DarkroomConfig:
    """Compose one runtime config from separated film/develop and scanner presets.

    The film preset owns negative formation. The scanner preset owns positive
    interpretation. Output size/format and runtime flags can still be adjusted by
    entry scripts after this merge.
    """
    merged = DarkroomConfig()

    if film_config is not None:
        merged.film = copy.deepcopy(film_config.film)
        merged.chemistry = copy.deepcopy(film_config.chemistry)
        merged.mode = str(film_config.mode)
        merged.enable_mtf = bool(film_config.enable_mtf)
        merged.enable_halation = bool(film_config.enable_halation)
        merged.enable_grain = bool(film_config.enable_grain)
        for field_name in DEVELOP_LOOK_FIELDS:
            setattr(merged.look, field_name, copy.deepcopy(getattr(film_config.look, field_name)))

    if scanner_config is not None:
        merged.scanner = copy.deepcopy(scanner_config.scanner)
        merged.enable_subtractive = bool(scanner_config.enable_subtractive)
        for field_name in SCAN_LOOK_FIELDS:
            setattr(merged.look, field_name, copy.deepcopy(getattr(scanner_config.look, field_name)))

    return merged
