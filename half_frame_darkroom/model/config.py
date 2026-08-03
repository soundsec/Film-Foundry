"""Film Foundry 配置对象。

配置分为六层：
- FilmStockConfig：胶片/介质材料本体
- DevelopRecipeConfig：冲洗流程配方；ChemistryConfig 只是兼容旧名称的别名
- ScannerConfig：扫描/打印解释
- LookAdjustConfig：GUI/脚本中的一次性微调
- OutputConfig：文件输出
- ProcessingConfig：处理质量与内部工作尺寸

from_dict() 会迁移旧 preset 中混放在 film/output/top-level 的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import copy

from half_frame_darkroom.core.atomic_io import strict_json_load


Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Vector3 = tuple[float, float, float]


@dataclass(slots=True)
class FilmStockConfig:
    """胶片本体属性。"""

    name: str = "Generic Color Negative"
    medium_family: str = "film"
    medium_process: str = "negative"
    image_polarity: str = "negative"
    color_process: str = "color"

    halation_strength: float = 0.12
    halation_threshold: float = 0.72
    halation_softness: float = 0.18
    halation_core_radius: float = 0.004
    halation_outer_radius: float = 0.018
    halation_core_mix: float = 0.62
    halation_color: Vector3 = (1.0, 0.42, 0.16)
    # Compatibility presets inject a warm RGB exposure before layer mapping.
    # Experimental layer-selective materials instead couple one scalar return
    # field directly into material-layer exposure using relative weights.
    halation_return_model: str = "compatibility_rgb"
    halation_layer_return_weights: Vector3 = (1.0, 0.42, 0.16)
    # Experimental layer-selective spread mixture: compact emulsion, existing
    # main base-return PSF, and wide low-frequency veil. Relative weights are
    # normalized during execution; compatibility RGB ignores this field.
    halation_spread_scale_weights: Vector3 = (0.0, 1.0, 0.0)

    # Default-off reduced support light piping. Unlike the random light-leak
    # accident, this material response reads only explicitly declared frame
    # edges and couples its geometric exposure directly to emulsion layers.
    light_piping_strength: float = 0.0
    light_piping_depth: float = 0.035
    light_piping_edge_mode: str = "none"
    light_piping_layer_weights: Vector3 = (1.0, 0.45, 0.18)

    # Legacy RGB-response fields.
    # The current electronic-negative pipeline uses sensitometry.py
    # (H-D density) instead of core/response.py. These fields are kept for
    # older helpers/tests and should not be used for new film presets.
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

    # Legacy image-space grain fields.
    # The current electronic-negative pipeline uses density_grain.py with
    # granularity_sigma and grain_density_correlation_radius.
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
    # Halation source gating: prefer local specular peaks over broad bright matte areas.
    halation_peak_radius: float = 0.006
    halation_peak_threshold: float = 0.12
    halation_peak_softness: float = 0.08
    halation_area_radius: float = 0.028
    halation_area_threshold: float = 0.32
    halation_area_suppression: float = 0.75

    hd_gamma: Vector3 = (0.62, 0.66, 0.60)
    density_min: Vector3 = (0.10, 0.10, 0.10)
    density_max: Vector3 = (1.85, 1.95, 1.75)
    log_exposure_toe: Vector3 = (-2.20, -2.15, -2.25)
    log_exposure_shoulder: Vector3 = (0.45, 0.48, 0.42)
    hd_toe_width: float = 0.18
    hd_shoulder_width: float = 0.22

    # Research-only latent-response tail for exceptional emulsions. Ordinary
    # stocks keep strength zero: normal gross overexposure is a shoulder/D-max
    # condition, not automatic solarization. The optional tail begins
    # continuously above the declared material log-exposure threshold.
    extreme_exposure_reversal_strength: float = 0.0
    extreme_exposure_reversal_start_loge: Vector3 = (0.56, 0.56, 0.56)
    extreme_exposure_reversal_width: float = 0.18

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

    # Three-band reduction of the otherwise spectral interaction between a
    # coloured support/mask and broad dye/scanner response bands.  The matrix
    # is row-normalized at observation time. A neutral base or zero strength
    # is exactly equivalent to the legacy additive-density model.
    base_dye_interaction_strength: float = 0.42
    base_dye_interaction_matrix: Matrix3 = (
        (0.90, 0.08, 0.02),
        (0.06, 0.88, 0.06),
        (0.02, 0.10, 0.88),
    )

    # Processed support + any integral coloured-coupler mask, expressed as
    # RGB optical density.  ``clear_support_density_rgb`` separates the
    # non-bleachable support floor from the mask for the experimental
    # mask/dye-bleach operator; old presets remain valid because their total
    # base density is still authoritative.
    film_base_density_rgb: Vector3 = (0.18, 0.55, 0.85)
    clear_support_density_rgb: Vector3 = (0.03, 0.03, 0.035)
    experimental_mask_bleach_susceptibility: float = 1.0
    experimental_mask_bleach_dye_damage: float = 0.16
    # Reduced RGB optical-density tendency of silver halide left by incomplete
    # fixing.  This belongs to the material because color stocks, sensitizing
    # dyes, and monochrome emulsions do not produce the same retained-salt veil.
    retained_halide_density_rgb: Vector3 = (0.62, 0.82, 1.0)
    # Rem-jet, anti-halation backing, protective/contamination layers, reduced
    # to one bounded auxiliary pool. Normal programs remove it completely;
    # incomplete or incompatible removal leaves this material-side density.
    auxiliary_layer_amount: float = 1.0
    auxiliary_layer_density_rgb: Vector3 = (0.08, 0.07, 0.06)

    # Reduced material-ageing model. ``material_degradation`` is the common
    # user-facing severity; the remaining fields describe how this stock ages.
    # Ageing is part of the material presented to exposure/development, not a
    # scanner look or a chemistry accident.
    material_degradation: float = 0.0
    degradation_speed_loss_stops: float = 0.65
    degradation_fog_density_rgb: Vector3 = (0.10, 0.12, 0.14)
    degradation_layer_balance: Vector3 = (0.90, 0.95, 1.00)

    granularity_sigma: Vector3 = (0.030, 0.028, 0.032)
    grain_density_correlation_radius: float = 0.0014
    # Experimental component-specific metallic-silver grain. Zero keeps the
    # established layer-grain path bit-for-bit unchanged. When enabled, this
    # neutral density field is derived from actual retained silver and added
    # directly to the RGB optical master, never through the dye matrix.
    silver_grain_strength: float = 0.0
    silver_grain_radius: float = 0.0008
    silver_grain_clump_mix: float = 0.22

    # Positive transparency prototype controls. These are material-side,
    # reduced-order equivalents for slide/reversal characteristics.
    positive_density_contrast: float = 1.0
    positive_density_bias: float = 0.0
    positive_latitude_compression: float = 0.0
    positive_dye_saturation: float = 1.0
    positive_midtone_density: float = 0.0
    positive_shadow_toe: float = 0.0
    positive_shadow_toe_width: float = 0.22
    positive_highlight_shoulder: float = 0.0
    positive_highlight_shoulder_width: float = 0.18
    # Retained chroma at the thin/highlight and dense/shadow endpoints of a
    # formed dye positive.  These are material controls, not scanner looks.
    positive_highlight_chroma_retention: float = 0.22
    positive_shadow_chroma_retention: float = 0.28

    # Material-side response when a non-native silver-halide program is used.
    cross_process_silver_development: float = 1.0
    cross_process_dye_coupling: float = 1.0
    cross_process_activation: float = 1.0
    cross_process_silver_bleach: float = 1.0
    cross_process_halide_fixing: float = 1.0
    cross_process_silver_removal: float = 1.0
    cross_process_dye_stability: float = 1.0
    cross_process_auxiliary_removal: float = 1.0
    cross_process_layer_balance: Vector3 = (1.0, 1.0, 1.0)

    halation_gaussian_amplitude: float = 0.62
    halation_exponential_amplitude: float = 0.38
    halation_exponential_radius: float = 0.022


@dataclass(slots=True)
class DevelopRecipeConfig:
    """银盐胶片工艺条件与降阶程序参数。

    时间、温度、浓度等字段描述药水/环境条件；``program_key`` 和步骤
    completion 字段描述银盐胶片共享算子程序。即时成像、银版等非银盐胶片
    工艺不应复用本配置。
    """

    developer_name: str = "Standard Developer"
    developer_type: str = "standard"
    fixer_name: str = "Standard Fixer"
    fixer_type: str = "standard"
    medium_process: str = "negative"
    process_mode: str = "normal_negative"
    program_key: str = "auto"
    frame_size: str = "35mm"
    time_min: float = 8.0
    temperature_c: float = 20.0
    concentration: float = 1.0
    agitation: float = 1.0
    push_stops: float = 0.0
    developer_exhaustion: float = 0.0
    fixer_exhaustion: float = 0.0
    compensation: float = 0.0
    silver_retention: float = 0.0
    # Surface metallic-silver deposition caused by mishandled/exhausted rapid
    # processing or monobath chemistry. This is distinct from bleach bypass:
    # it adds a deposit instead of retaining the developed image silver pool.
    silver_plating: float = 0.0
    light_leak_strength: float = 0.0
    chemical_stain: float = 0.0
    uneven_development: float = 0.0
    # Default-off reduced adjacency kinetics. A single provisional first-
    # development estimate generates one bounded, near-zero-mean local rate
    # correction; formal material pools are still consumed only once.
    development_adjacency_strength: float = 0.0
    development_adjacency_radius: float = 0.0025
    process_variation: float = 0.0
    first_development_completion: float = 1.0
    second_development_completion: float = 1.0
    reversal_activation: float = 1.0
    first_silver_removal: float = 1.0
    silver_bleach_completion: float = 1.0
    # Experimental, non-standard coloured-mask/dye bleach.  This must never be
    # conflated with silver bleach, which only prepares metallic silver for
    # fixing.  A value of zero preserves all standard process topologies.
    mask_bleach_completion: float = 0.0
    halide_fixing_completion: float = 1.0
    dye_coupling_efficiency: float = 1.0
    auxiliary_removal: float = 1.0
    process_layer_balance: Vector3 = (1.0, 1.0, 1.0)


# Legacy alias for older presets/tests. New code should use DevelopRecipeConfig;
# ChemistryConfig remains here as a compatibility layer while public naming
# moves from "chemistry" toward "develop recipe".
ChemistryConfig = DevelopRecipeConfig


@dataclass(slots=True)
class ScannerConfig:
    """扫描/打印解释。"""

    interpretation_mode: str = "auto"
    # New scanner controls are orthogonal to the developed-medium identity.
    # ``auto`` remains a legacy/session compatibility mode; the main GUI writes
    # ``manual`` and treats the medium contract as a recommendation only.
    remove_base_mask: bool = True
    invert_transmission: bool = True
    include_clear_base_border: bool = False
    interpreter_key: str = "negative_scan"
    target_medium_process: str = "negative"
    input_polarity: str = "negative"
    output_polarity: str = "positive"
    scanner_light_color: Vector3 = (1.0, 1.0, 1.0)
    # Canonical shared light source. ``None`` preserves old presets by falling
    # back to their negative-backlight or positive-light-table fields.
    transmission_light_ev: float | None = None
    transmission_light_temperature_k: float | None = None
    negative_backlight_ev: float = 0.0
    negative_backlight_temperature_k: float = 5500.0
    light_table_ev: float = 0.0
    light_table_temperature_k: float = 5500.0
    positive_scan_color_control_strength: float = 0.35
    projection_white_softness: float = 0.0
    projection_black_adaptation: float = 0.0
    scanner_response_matrix: Matrix3 = (
        (1.00, 0.00, 0.00),
        (0.00, 1.00, 0.00),
        (0.00, 0.00, 1.00),
    )
    scan_method: str = "negative_inversion"
    scan_base_percentile: float = 99.5
    negative_channel_matrix: Matrix3 = (
        (1.00, 0.00, 0.00),
        (0.00, 1.00, 0.00),
        (0.00, 0.00, 1.00),
    )
    negative_channel_gamma: Vector3 = (1.0, 1.0, 1.0)
    # Optional reduced dye-channel decoupling derived from the immutable
    # material absorption matrix. Off by default to preserve existing presets.
    negative_channel_compensation_enabled: bool = False
    negative_channel_compensation_strength: float = 0.35

    print_reference_density: Vector3 = (1.58, 1.61, 1.53)
    print_gamma: float = 0.95
    print_mapping_mode: str = "printlike"
    print_color_bias: Vector3 = (1.0, 1.0, 1.0)
    print_color_shift: Vector3 = (0.0, 0.0, 0.0)
    highlight_color_bias: Vector3 = (1.0, 1.0, 1.0)
    highlight_bias_threshold: float = 0.72
    highlight_bias_softness: float = 0.18
    # 扫描/输出阶段的色彩浓度。它不改变底片染料密度，只改变正像解释。
    scan_saturation: float = 1.05

    scan_normalize: bool = True
    # A normal negative scan should establish a useful display black/white
    # range after inversion.  Low values are still available for flat/log-like
    # diagnostics, but they leave the print-like mapping visibly grey.
    scan_normalize_strength: float = 0.45
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
    # Runtime-only control: positive values make halation easier to trigger by
    # lowering the effective film threshold; negative values raise it.
    halation_sensitivity: float = 0.0
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
    # Bound float quantization temporaries during final image/raw encoding.
    # Zero disables row tiling for diagnostics.
    encode_tile_rows: int = 512
    encode_tile_threshold_megapixels: float = 8.0
    # compressed minimizes archive size; store trades disk space for faster
    # saves without changing any stored float values.
    medium_npz_compression: str = "compressed"
    save_scanner_raw: bool = True
    scanner_raw_border_percent: float = 0.04
    scanner_raw_border_min_px: int = 32
    # Complete archive bundle: NPZ, preview/raw when available, transparency,
    # transmission, density/effect plates, and a manifest.
    export_layer_pack: bool = False
    # Standalone transparent/transmission rendition. A layer pack includes it.
    export_transparent_plate: bool = True
    # Standalone derived density/effect plates. A layer pack includes them.
    export_plate_set: bool = False
    anti_banding_strength: float = 0.18
    watermark_metadata: bool = True
    watermark_negative_material: bool = True
    watermark_scanner_raw_border: bool = True


@dataclass(slots=True)
class ProcessingConfig:
    """处理质量与内部工作尺寸。

    这些设置不改变胶片材料、冲洗流程或扫描解释的物理语义，只决定某些低频/
    随机场模块是否先在较小尺寸计算再回贴到目标尺寸。
    """

    # User-visible execution policy. quality keeps the requested frame size;
    # scaled_fast uses output.preview_long_edge; reduced_fast keeps the frame
    # size but permits documented lower-order internal approximations.
    execution_mode: str = "quality"
    # Internal detail tier inside the selected execution policy.
    quality_mode: str = "standard"
    # Larger working frames remain enabled as best-effort support.
    comfort_zone_megapixels: float = 30.0
    # None means use the quality-mode default (draft=1200, standard=1800,
    # high=native). A positive value is an explicit per-module override.
    halation_work_long_edge: int | None = None
    grain_work_long_edge: int | None = None
    # Adjacency is an explicitly reduced global chemistry field even in high
    # quality mode; None selects draft=1200, standard=1800, high=3200.
    adjacency_work_long_edge: int | None = None
    # Exact pointwise material-pool processing switches to bounded row tiles
    # above this frame size. Zero rows disables tiling for diagnostics.
    material_tile_rows: int = 256
    material_tile_threshold_megapixels: float = 8.0
    scan_tile_rows: int = 512
    scan_tile_threshold_megapixels: float = 8.0
    # Process-wide native-library worker budget. Four avoids the severe
    # oversubscription commonly observed when OpenCV defaults to every logical
    # CPU while unrelated background work is active. Zero restores the native
    # library's startup default. This changes scheduling only.
    native_thread_limit: int = 4
    # Runtime ownership policy. Public develop/diagnostic paths keep full;
    # persisted production media may use cold_fp16. The private output-only
    # array path may temporarily select discard; public validation rejects it.
    history_storage_policy: str = "full"
    # Optional explicit capacity boundary. In allow/warn mode it may activate
    # the existing exact material-row schedule earlier; it never changes
    # resolution, process operators, precision, or scan meaning. Error policy
    # remains a conservative pre-decode rejection boundary.
    memory_budget_mb: float | None = None
    memory_budget_policy: str = "warn"


@dataclass(slots=True)
class DarkroomConfig:
    """单张图像处理总配置。"""

    film: FilmStockConfig = field(default_factory=FilmStockConfig)
    chemistry: ChemistryConfig = field(default_factory=ChemistryConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    look: LookAdjustConfig = field(default_factory=LookAdjustConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)

    random_seed: int | None = None
    seed_strategy: str = "random"
    fast_mode: bool = False
    enable_mtf: bool = True
    enable_halation: bool = True
    enable_grain: bool = True
    enable_subtractive: bool = True
    mode: str = "color_negative"
    medium: str = "film_negative"
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

    @property
    def develop(self) -> DevelopRecipeConfig:
        return self.chemistry

    @develop.setter
    def develop(self, value: DevelopRecipeConfig) -> None:
        self.chemistry = value

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DarkroomConfig":
        if not isinstance(data, dict):
            raise ValueError("Darkroom configuration root must be a mapping object.")

        def section(name: str) -> dict[str, Any]:
            value = data.get(name, {})
            if value is None:
                return {}
            if not isinstance(value, dict):
                raise ValueError(f"Configuration section '{name}' must be an object.")
            return dict(value)

        film_data = section("film")
        chemistry_data = section("chemistry")
        chemistry_data.update(section("develop"))
        scanner_data = section("scanner")
        output_data = section("output")
        processing_data = section("processing")
        processing_data.setdefault(
            "execution_mode",
            "reduced_fast" if bool(data.get("fast_mode", False)) else "quality",
        )
        look_data = section("look")

        # JSON has no tuple type. Normalize typed vectors/matrices at the one
        # config boundary so presets, sessions, GUI values and defaults expose
        # the same immutable shape to both formation and scan code.
        film_vector_fields = {
            "halation_color",
            "halation_layer_return_weights",
            "halation_spread_scale_weights",
            "light_piping_layer_weights",
            "hd_gamma",
            "density_min",
            "density_max",
            "log_exposure_toe",
            "log_exposure_shoulder",
            "extreme_exposure_reversal_start_loge",
            "film_base_density_rgb",
            "clear_support_density_rgb",
            "retained_halide_density_rgb",
            "auxiliary_layer_density_rgb",
            "degradation_fog_density_rgb",
            "degradation_layer_balance",
            "granularity_sigma",
            "cross_process_layer_balance",
        }
        film_matrix_fields = {
            "color_matrix",
            "layer_sensitivity_matrix",
            "dye_absorption_matrix",
            "base_dye_interaction_matrix",
        }
        for key in film_vector_fields:
            if key in film_data:
                film_data[key] = tuple(float(value) for value in film_data[key])
        for key in film_matrix_fields:
            if key in film_data:
                film_data[key] = tuple(
                    tuple(float(value) for value in row) for row in film_data[key]
                )
        for key in {"grain_scales", "grain_scale_weights"}:
            if key in film_data:
                film_data[key] = tuple(float(value) for value in film_data[key])

        if "process_layer_balance" in chemistry_data:
            chemistry_data["process_layer_balance"] = tuple(
                float(value) for value in chemistry_data["process_layer_balance"]
            )

        scanner_vector_fields = {
            "scanner_light_color",
            "negative_channel_gamma",
            "print_reference_density",
            "print_color_bias",
            "print_color_shift",
            "highlight_color_bias",
        }
        for key in scanner_vector_fields:
            if key in scanner_data:
                scanner_data[key] = tuple(float(value) for value in scanner_data[key])
        if "scanner_response_matrix" in scanner_data:
            scanner_data["scanner_response_matrix"] = tuple(
                tuple(float(value) for value in row)
                for row in scanner_data["scanner_response_matrix"]
            )
        if "negative_channel_matrix" in scanner_data:
            scanner_data["negative_channel_matrix"] = tuple(
                tuple(float(value) for value in row)
                for row in scanner_data["negative_channel_matrix"]
            )

        # Scanner interpretation used to be a three-way pipeline selector.
        # Keep those presets readable while making mask removal and inversion
        # explicit, independent user controls in new sessions.
        legacy_interpretation = str(
            scanner_data.get("interpretation_mode", "")
        ).strip().lower()
        legacy_interpreter = str(
            scanner_data.get("interpreter_key", "")
        ).strip().lower()
        legacy_positive = (
            legacy_interpretation == "positive"
            or legacy_interpreter == "positive_transparency_scan"
        )
        scanner_data.setdefault("remove_base_mask", not legacy_positive)
        scanner_data.setdefault("invert_transmission", not legacy_positive)
        if "transmission_light_ev" not in scanner_data:
            legacy_ev_key = "light_table_ev" if legacy_positive else "negative_backlight_ev"
            if legacy_ev_key in scanner_data:
                scanner_data["transmission_light_ev"] = float(scanner_data[legacy_ev_key])
        if "transmission_light_temperature_k" not in scanner_data:
            legacy_temp_key = (
                "light_table_temperature_k"
                if legacy_positive
                else "negative_backlight_temperature_k"
            )
            if legacy_temp_key in scanner_data:
                scanner_data["transmission_light_temperature_k"] = float(
                    scanner_data[legacy_temp_key]
                )

        # 旧 preset 迁移：这些字段过去混在 film 中。
        scanner_keys = {
            "scanner_light_color",
            "scanner_response_matrix",
            "negative_channel_matrix",
            "negative_channel_gamma",
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

        # Legacy migrations above may have moved scanner vectors out of old
        # film/output sections after the first normalization pass.
        for key in scanner_vector_fields:
            if key in scanner_data:
                scanner_data[key] = tuple(float(value) for value in scanner_data[key])
        if "scanner_response_matrix" in scanner_data:
            scanner_data["scanner_response_matrix"] = tuple(
                tuple(float(value) for value in row)
                for row in scanner_data["scanner_response_matrix"]
            )
        if "negative_channel_matrix" in scanner_data:
            scanner_data["negative_channel_matrix"] = tuple(
                tuple(float(value) for value in row)
                for row in scanner_data["negative_channel_matrix"]
            )

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
            chemistry=ChemistryConfig(**chemistry_data),
            scanner=ScannerConfig(**scanner_data),
            look=LookAdjustConfig(**look_data),
            output=OutputConfig(**output_data),
            processing=ProcessingConfig(**processing_data),
            random_seed=data.get("random_seed"),
            seed_strategy=str(data.get("seed_strategy", "random")),
            fast_mode=bool(data.get("fast_mode", False)),
            enable_mtf=bool(data.get("enable_mtf", True)),
            enable_halation=bool(data.get("enable_halation", True)),
            enable_grain=bool(data.get("enable_grain", True)),
            enable_subtractive=bool(data.get("enable_subtractive", True)),
            mode=str(data.get("mode", "color_negative")),
            medium=str(data.get("medium", data.get("medium_type", "film_negative"))),
            debug_output=bool(data.get("debug_output", False)),
            save_sidecar=bool(data.get("save_sidecar", True)),
            comparison_grid=bool(data.get("comparison_grid", False)),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "DarkroomConfig":
        payload = strict_json_load(path)
        if not isinstance(payload, dict):
            raise ValueError(f"Configuration JSON root must be an object: {path}")
        return cls.from_dict(payload)


DEVELOP_LOOK_FIELDS = (
    "exposure_ev",
    "negative_contrast",
    "saturation_multiplier",
    "halation_multiplier",
    "halation_sensitivity",
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
    develop_config: DarkroomConfig | None = None,
) -> DarkroomConfig:
    """Compose one runtime config from separated film/develop and scanner presets.

    The film preset owns only material identity/parameters, the develop preset
    owns chemistry and formation-side look controls, and the scanner preset
    owns observation. Output and runtime flags remain entry-point concerns.
    """
    merged = DarkroomConfig()

    if film_config is not None:
        merged.film = copy.deepcopy(film_config.film)
        merged.mode = str(film_config.mode)
        merged.medium = str(film_config.medium)

    if develop_config is not None:
        merged.chemistry = copy.deepcopy(develop_config.chemistry)
        if str(develop_config.mode).lower() != "color_negative":
            merged.mode = str(develop_config.mode)
        if str(develop_config.medium).lower() != "film_negative":
            merged.medium = str(develop_config.medium)
        for field_name in DEVELOP_LOOK_FIELDS:
            setattr(merged.look, field_name, copy.deepcopy(getattr(develop_config.look, field_name)))

    if scanner_config is not None:
        merged.scanner = copy.deepcopy(scanner_config.scanner)
        merged.enable_subtractive = bool(scanner_config.enable_subtractive)
        for field_name in SCAN_LOOK_FIELDS:
            setattr(merged.look, field_name, copy.deepcopy(getattr(scanner_config.look, field_name)))

    return merged
