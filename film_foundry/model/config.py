"""Film Foundry 配置对象。

配置分为七层：
- FilmStockConfig：胶片/介质材料本体
- DevelopRecipeConfig：冲洗流程配方；ChemistryConfig 只是兼容旧名称的别名
- ScannerConfig：扫描/打印解释
- LookAdjustConfig：GUI/脚本中的一次性微调
- OutputConfig：文件输出
- ProcessingConfig：处理质量与内部工作尺寸
- FilmBorderConfig：齿孔/片边布局及厂印配置；相机打印进入曝光，桌面片边仍仅解析布局

from_dict() 会迁移旧 preset 中混放在 film/output/top-level 的字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import copy
import math
import math

from film_foundry.core.atomic_io import strict_json_load
from film_foundry.core.file_cache import FileRevisionCache

_config_file_cache = FileRevisionCache(128)
from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile


Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Vector3 = tuple[float, float, float]
SCANNER_COLOR_RESPONSE_MODELS = ("simple_channel_adjust_v1",)


def normalize_scanner_tone_curve(value: object, *, field_name: str = "scanner.tone_curve") -> tuple[tuple[float, float], ...] | None:
    """Output brightness proxy curve, independent of RGB channel curves."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError(f"{field_name} must contain at least two control points.")
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError(f"{field_name} points must be [x, y] pairs.")
        x, y = map(float, point)
        if not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 1 or points and x <= points[-1][0]:
            raise ValueError(f"{field_name} needs finite values and strictly increasing x in [0, 1].")
        points.append((x, y))
    return tuple(points)


def normalize_scanner_color_curve_rgb(
    value: object,
    *,
    field_name: str = "scanner.color_curve_rgb",
) -> tuple[
    tuple[tuple[float, float], ...],
    tuple[tuple[float, float], ...],
    tuple[tuple[float, float], ...],
] | None:
    """Normalize optional per-channel piecewise RGB curves."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{field_name} must contain three channel curves.")
    channels: list[tuple[tuple[float, float], ...]] = []
    for index, channel in enumerate(value):
        if not isinstance(channel, (list, tuple)) or len(channel) < 2:
            raise ValueError(
                f"{field_name}[{index}] must contain at least two control points."
            )
        points: list[tuple[float, float]] = []
        previous_x: float | None = None
        for point in channel:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(
                    f"{field_name}[{index}] points must be [x, y] pairs."
                )
            x_value = float(point[0])
            y_value = float(point[1])
            if not math.isfinite(x_value) or not math.isfinite(y_value):
                raise ValueError(f"{field_name}[{index}] points must be finite.")
            if not (0.0 <= x_value <= 1.0) or previous_x is not None and x_value <= previous_x:
                raise ValueError(
                    f"{field_name}[{index}] x values must be strictly increasing in [0, 1]."
                )
            previous_x = x_value
            points.append((x_value, y_value))
        channels.append(tuple(points))
    return (channels[0], channels[1], channels[2])


def normalize_color_mixer(value):
    """Eight H/S/L rows: red, orange, yellow, green, aqua, blue, purple, magenta."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 8:
        raise ValueError("scanner.color_mixer requires eight H/S/L rows")
    rows = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError("scanner.color_mixer requires three values per row")
        values = tuple(float(v) for v in row)
        if any(not math.isfinite(v) or abs(v) > limit for v, limit in zip(values, (30, 100, 100))):
            raise ValueError("color_mixer: hue must be ±30 degrees; saturation/lightness ±100")
        rows.append(values)
    return tuple(rows) if any(v != 0 for row in rows for v in row) else None


@dataclass(slots=True)
class FilmStockConfig:
    """胶片本体属性。"""

    name: str = "Generic Color Negative"
    process_response: dict[str, object] | None = None
    carbon_backing: dict[str, object] | None = field(default=None, kw_only=True)
    emulsion_integrity: dict[str, object] | None = field(default=None, kw_only=True)
    chemical_fog_susceptibility: float = field(default=1.0, kw_only=True)
    time_response: dict[str, object] | None = field(default=None, kw_only=True)
    storage_history: dict[str, object] | None = field(default=None, kw_only=True)
    reciprocity_response: dict[str, object] | None = field(default=None, kw_only=True)
    medium_family: str = "film"
    medium_process: str = "negative"
    image_polarity: str = "negative"
    color_process: str = "color"

    # None preserves historical radii relative to the digital short edge.
    # A positive reference length opts into material-space projection at
    # formation; old radius values then describe fractions of this mm extent.
    spatial_reference_short_edge_mm: float | None = None

    # Optional exposure-only spectra. Dye optics and scan remain independent.
    spectral_exposure_profile: SpectralExposureProfile | None = None
    spectral_material_profile: SpectralMaterialProfile | None = None

    def __post_init__(self) -> None:
        if self.spectral_material_profile is not None:
            self.spectral_material_profile = SpectralMaterialProfile.from_value(self.spectral_material_profile)
        if self.spectral_exposure_profile is not None:
            self.spectral_exposure_profile = SpectralExposureProfile.from_value(self.spectral_exposure_profile)

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

    # Default-off compact lateral exposure inside emulsion/interlayer
    # material. Unlike halation this happens before support/backing return,
    # remains close to the highlight, and preserves layer/source colour.
    emulsion_highlight_spread_strength: float = 0.0
    emulsion_highlight_spread_radius: float = 0.0014
    emulsion_highlight_spread_threshold: float = 0.72
    emulsion_highlight_spread_softness: float = 0.14
    emulsion_highlight_spread_layer_radius_scales: Vector3 = (0.85, 1.0, 1.15)
    emulsion_highlight_spread_layer_strengths: Vector3 = (0.90, 1.0, 1.08)

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

    # grain_strength/midtone fields serve legacy image-space grain helpers.
    # grain_scales and grain_scale_weights also describe the active multiscale
    # density_grain model, with granularity_sigma/correlation_radius.
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

    # Lightweight material-side DIR response. Rows are dye-forming source
    # layers and columns are inhibited target layers. Values already include
    # the material's effective release and sensitivity; keeping that observable
    # product avoids an unsupported strength/release/coupling factorization.
    # An all-zero matrix is a strict legacy identity. This is material
    # formation, never scanner colour correction.
    dir_material_response_matrix: Matrix3 = (
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    )
    dir_diffusion_radius: float = 0.0030

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

    formation_history_model: str = field(default='bath_products_local_v2', kw_only=True)
    latent_wait: dict[str, object] | None = field(default=None, kw_only=True)
    post_process_light: dict[str, object] | None = field(default=None, kw_only=True)
    developer_name: str = "Standard Developer"
    developer_type: str = "standard"
    fixer_name: str = "Standard Fixer"
    fixer_type: str = "standard"
    medium_process: str = "negative"
    process_mode: str = "normal_negative"
    program_key: str = "auto"
    process_steps: tuple[dict[str, object], ...] | None = None
    bath_overrides: tuple[dict[str, object], ...] | None = None
    process_events: dict[str, object] | None = None
    effect_intent: dict[str, object] | None = None
    frame_size: str = "35mm"
    # Optional custom exposed-frame long edge. Requires the material's
    # spatial reference above; None resolves frame_size only in physical mode.
    frame_long_edge_mm: float | None = None
    time_min: float = 8.0
    temperature_c: float = 20.0
    concentration: float = 1.0
    agitation: float = 1.0
    # Optional total active agitation seconds per bath; None preserves historical intensity-only kinetics.
    agitation_time_s: float | None = None
    bath_transfer_s: float = 10.0
    push_stops: float = 0.0
    developer_exhaustion: float = 0.0
    fixer_exhaustion: float = 0.0
    compensation: float = 0.0
    silver_retention: float = 0.0
    # Surface metallic-silver deposition caused by mishandled/exhausted rapid
    # processing or monobath chemistry. This is distinct from bleach bypass:
    # it adds a deposit instead of retaining the developed image silver pool.
    silver_plating: float = 0.0
    # ``auto`` preserves the historical local-edge sampler. Explicit built-in
    # templates still create additional exposure before latent formation; they
    # are not image overlays or scanner effects.
    light_leak_template: str = "auto"
    light_leak_strength: float = 0.0
    # Optional second built-in shape mixed with the primary template. ``none``
    # keeps the historical single-template path.
    light_leak_secondary_template: str = "none"
    # Coverage mix for primary/secondary templates and an optional mask.
    # ``replace`` uses only the external mask.
    light_leak_mix: str = "max"
    light_leak_mask_path: str = ""
    # ``sampled`` keeps seed-driven placement. ``manual`` uses the entry side,
    # centre and rotation below without resampling texture identity.
    light_leak_placement: str = "sampled"
    light_leak_entry_side: str = "auto"
    light_leak_center_x: float = 0.5
    light_leak_center_y: float = 0.5
    light_leak_rotation_degrees: float = 0.0
    # When set, pattern sampling uses this seed instead of the frame RNG so a
    # batch can share one leak identity.  It is not a sidecar group contract.
    light_leak_identity_seed: int | None = None
    # Shared roll/batch identity for leak, fixed gate obstructions and bath
    # defects. None preserves legacy array-level random streams; application
    # batches freeze this once. Grain/sensor noise remain frame-specific.
    incident_identity_seed: int | None = None
    chemical_stain: float = 0.0
    uneven_development: float = 0.0
    # Explicit gelatin/emulsion damage severity caused by abrupt bath
    # temperature changes or mishandling. A single ``temperature_c`` value
    # cannot encode a temperature shock, so ordinary hot development must not
    # silently enable this accident.
    reticulation_strength: float = 0.0
    # Explicit post-formation dye loss caused by non-standard chemistry or a
    # declared processing accident. This is consumed by a material-pool
    # operator; a single hot bath or exhausted chemistry must not invent it.
    dye_damage_strength: float = 0.0
    # Optical occlusions of linearized scene light after inverse gamma and
    # before development. These multiply exposure; they are not scan dirt,
    # RGB overlays, or post-process texture. All-zero keeps a strict identity.
    exposure_occlusion_dust: float = 0.0
    exposure_occlusion_hair: float = 0.0
    exposure_occlusion_insect: float = 0.0
    exposure_occlusion_debris: float = 0.0
    # Overrides the shared gate identity. With an identified batch all four
    # obstructions use it; legacy None batches keep per-frame dust/debris and
    # fixed hair/insects. None inherits the batch or historical built-in seed.
    exposure_occlusion_identity_seed: int | None = None
    # Default-off reduced adjacency kinetics. A single provisional first-
    # development estimate generates one bounded, near-zero-mean local rate
    # correction; formal material pools are still consumed only once.
    development_adjacency_strength: float = 0.0
    development_adjacency_radius: float = 0.0025
    # Process-side survival/effectiveness of a material's DIR inhibitor.
    # None selects the topology-aware conservative default: ordinary colour
    # negative development permits DIR, while exhaustive standard reversal
    # colour development suppresses it. Special reversal chemistry or strong
    # inhibitors must opt in explicitly. This is not a scanner control.
    dir_process_effectiveness: float | None = None
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
    spectral_observation_profile: SpectralObservationProfile | None = None

    def __post_init__(self) -> None:
        if self.spectral_observation_profile is not None:
            self.spectral_observation_profile = SpectralObservationProfile.from_value(self.spectral_observation_profile)
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
    # Current observation color is parametric shift/bias/saturation/highlight
    # gain, not a 3-D scanner LUT. Optional piecewise RGB curves may stack in
    # two places: device_response_rgb on recorded scanner_raw after the
    # response matrix, and color_curve_rgb after print-like mapping. None or
    # identity keeps the historical path exactly.
    color_response_model: str = "simple_channel_adjust_v1"
    color_mixer: tuple[tuple[float, float, float], ...] | None = None
    tone_curve: tuple[tuple[float, float], ...] | None = None
    color_curve_rgb: tuple[
        tuple[tuple[float, float], ...],
        tuple[tuple[float, float], ...],
        tuple[tuple[float, float], ...],
    ] | None = None
    device_response_rgb: tuple[
        tuple[tuple[float, float], ...],
        tuple[tuple[float, float], ...],
        tuple[tuple[float, float], ...],
    ] | None = None

    scan_normalize: bool = True
    # A normal negative scan should establish a useful display black/white
    # range after inversion.  Low values are still available for flat/log-like
    # diagnostics, but they leave the print-like mapping visibly grey.
    scan_normalize_strength: float = 0.45
    scan_normalize_mode: str = "luma"
    scan_black_percentile: float = 0.3
    scan_white_percentile: float = 99.7


@dataclass(slots=True)
class ExposureConfig:
    """Explicit capture conditions; source RGB is already integrated dose."""

    duration_seconds: float | None = None


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
    # Auto follows quality for cold density layers (7/10/15 fraction bits).
    # Independent appearance textures keep their field-specific FP16 policy.
    # Authoritative optics/chemistry stay FP32; full preserves all current bits.
    material_storage_precision: str = "auto"
    # Final image orientation only; material arrays and optical relay stay fixed.
    rotation_quarters: int = 0
    # Browsing companion only. The authoritative medium and scan stay native.
    # A complete layer pack keeps its full-size rendition; zero opts out.
    medium_preview_long_edge: int = 1600
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

    边长缩放、质量档位与低阶算子许可是可以叠加的三个独立控制轴。三者都不得
    改变材料身份、工艺拓扑、最终介质契约或扫描只读边界。
    """

    # Compatibility shortcut for historical sessions/CLI. Runtime authority is
    # resolved as independent axes: scaled_fast enables edge scaling;
    # reduced_fast enables the lower-order family; fast_mode may combine the
    # latter with scaled_fast. quality_mode is always an independent third axis.
    execution_mode: str = "quality"
    # Independent internal detail/sampling tier.
    quality_mode: str = "standard"
    # Native FP32 wet-front arithmetic in draft/standard; high keeps FP64.
    # Independent of archive precision. 'float64' explicitly opts out.
    contact_precision: str = "auto"
    # A formation-only model choice, independent of scanner interpretation.
    reduced_profile: str = "builtin_reduced_fast_v4"
    # Larger working frames remain enabled as best-effort support.
    comfort_zone_megapixels: float = 30.0
    # None means use the quality-mode default (draft=768, standard=1800,
    # high=native). A positive value is an explicit per-module override.
    halation_work_long_edge: int | None = None
    # Compact emulsion spread is evaluated on an explicitly bounded optical
    # propagation grid even at high quality; full-resolution highlight
    # evidence is extracted before this reduction.
    emulsion_spread_work_long_edge: int | None = None
    grain_work_long_edge: int | None = None
    # Adjacency is an explicitly reduced global chemistry field even in high
    # quality mode; None selects draft=768, standard=1800, high=3200.
    adjacency_work_long_edge: int | None = None
    # DIR uses a separate layer-specific colour-development work field. None
    # selects draft=640, standard=1400, high=1800. The tighter grid accounts
    # for its private three-layer provisional material state.
    dir_work_long_edge: int | None = None
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
class FilmBorderConfig:
    """Film-edge layout and independently configured in-picture imprint.

    Rebates become stored optical material at formation, with absent support in holes.
    Imprint styles other than ``none`` add an in-picture film-plane flash into
    formation. Do not reuse scanner ``include_clear_base_border`` as sprocket
    geometry, and do not treat imprint as an RGB watermark.
    """

    enabled: bool = False
    format_key: str = "135"
    sprocket_rows: str = "both"
    rebate_height_ratio: float = 0.23
    sprocket_count: int | None = None
    factory_marks: bool = True
    stock_label: str = ""
    factory_layout: str = "legacy"
    factory_batch: int = 0
    factory_barcode: str = "legacy"
    factory_barcode_seed: int = 0
    loading_offset: float = 0.0
    factory_offset_top: tuple[float, float] = (0.0, 0.0)
    factory_offset_bottom: tuple[float, float] = (0.0, 0.0)
    advance_marker_offset: tuple[float, float] = (0.0, 0.0)
    advance_marker_style: str = "legacy"
    factory_exposure_ev: float = 0.0
    factory_exposure_variation: float = 0.0
    frame_number: int | None = None
    advance_direction: str = "right"
    # Empty stencils retain the existing stock text; patterns replace the stock
    # name area. Frame labels occupy a separate lower-edge band. Flip is 180 degrees.
    factory_pattern_top: str = ""
    factory_pattern_bottom: str = ""
    factory_pattern_linked: bool = True
    factory_flip_top: bool = False
    factory_flip_bottom: bool = False
    factory_mark_type: str = "hard"
    factory_mark_spread: float = 1.0
    imprint_style: str = "none"
    imprint_side: str = "bottom"
    imprint_text: str = ""
    imprint_time: str = ""
    imprint_font: str = "legacy"
    imprint_position_xy: tuple[float, float] | None = None
    imprint_scale: float = 1.0


@dataclass(slots=True)
class DarkroomConfig:
    """单张图像处理总配置。"""

    film: FilmStockConfig = field(default_factory=FilmStockConfig)
    exposure: ExposureConfig = field(default_factory=ExposureConfig, kw_only=True)
    chemistry: ChemistryConfig = field(default_factory=ChemistryConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    look: LookAdjustConfig = field(default_factory=LookAdjustConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    film_border: FilmBorderConfig = field(default_factory=FilmBorderConfig)

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
    # apply: convert a valid embedded RGB ICC into working sRGB.
    # ignore: keep encoded values as sRGB (historical decode).
    input_icc_policy: str = "apply"
    # Independent of workflow (photo / sealed roll) and execution quality.
    process_control_mode: str = "normal"

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
        # An absent version in a saved recipe means the historical global
        # approximation. Fresh dataclass recipes use the current bath model.
        from film_foundry.contracts.formation_history import validate_model, LEGACY, validate_fog_susceptibility
        chemistry_data['formation_history_model'] = validate_model(chemistry_data.get('formation_history_model', LEGACY))
        from film_foundry.contracts.film_time import validate_history, validate_response as validate_time_response
        for key,kind in (('latent_wait','latent'),('post_process_light','light')):
            if key in chemistry_data:chemistry_data[key]=validate_history(chemistry_data[key],kind)
        from film_foundry.contracts.process_events import validate_process_events,validate_process_response
        if chemistry_data.get('process_events') is not None:chemistry_data['process_events']=validate_process_events(chemistry_data['process_events'])
        if chemistry_data.get('effect_intent') is not None:
            from film_foundry.contracts.effect_intent import validate_effect_intent
            chemistry_data['effect_intent']=validate_effect_intent(chemistry_data['effect_intent'])
        if film_data.get('process_response') is not None:film_data['process_response']=validate_process_response(film_data['process_response'])
        from film_foundry.contracts.carbon_backing import validate_carbon_backing
        if film_data.get('carbon_backing') is not None:film_data['carbon_backing']=validate_carbon_backing(film_data['carbon_backing'])
        from film_foundry.contracts.emulsion_integrity import validate_emulsion_integrity
        if 'time_response' in film_data:film_data['time_response']=validate_time_response(film_data['time_response'])
        if 'storage_history' in film_data:film_data['storage_history']=validate_history(film_data['storage_history'],'storage')
        from film_foundry.contracts.film_exposure import validate_response as validate_reciprocity, validate_duration
        if 'reciprocity_response' in film_data:
            film_data['reciprocity_response'] = validate_reciprocity(film_data['reciprocity_response'])
        exposure_data = section('exposure')
        if 'duration_seconds' in exposure_data:
            exposure_data['duration_seconds'] = validate_duration(exposure_data['duration_seconds'])
        if 'chemical_fog_susceptibility' in film_data:
            film_data['chemical_fog_susceptibility'] = validate_fog_susceptibility(film_data['chemical_fog_susceptibility'])
        if film_data.get('emulsion_integrity') is not None:film_data['emulsion_integrity']=validate_emulsion_integrity(film_data['emulsion_integrity'])
        scanner_data = section("scanner")
        output_data = section("output")
        processing_data = section("processing")
        film_border_data = section("film_border")
        processing_data.setdefault(
            "execution_mode",
            "reduced_fast" if bool(data.get("fast_mode", False)) else "quality",
        )
        if "reduced_profile" not in processing_data:
            from film_foundry.contracts.formation_profiles import LEGACY_REDUCED_PROFILE, CURRENT_REDUCED_PROFILE
            processing_data["reduced_profile"] = (LEGACY_REDUCED_PROFILE
                if data.get("fast_mode", False) or processing_data["execution_mode"] == "reduced_fast"
                else CURRENT_REDUCED_PROFILE)
        look_data = section("look")

        # JSON has no tuple type. Normalize typed vectors/matrices at the one
        # config boundary so presets, sessions, GUI values and defaults expose
        # the same immutable shape to both formation and scan code.
        film_vector_fields = {
            "halation_color",
            "halation_layer_return_weights",
            "halation_spread_scale_weights",
            "emulsion_highlight_spread_layer_radius_scales",
            "emulsion_highlight_spread_layer_strengths",
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
            "dir_material_response_matrix",
        }

        # Experimental DIR sessions briefly exposed an unidentifiable
        # strength * release * coupling factorization. Collapse those fields
        # into the one material response that the reduced model can actually
        # observe. A new explicit matrix always wins if both forms are present.
        legacy_dir_strength = film_data.pop("dir_inhibition_strength", None)
        legacy_dir_release = film_data.pop("dir_release_per_layer", None)
        legacy_dir_coupling = film_data.pop("dir_layer_coupling_matrix", None)
        if (
            "dir_material_response_matrix" not in film_data
            and any(
                value is not None
                for value in (
                    legacy_dir_strength,
                    legacy_dir_release,
                    legacy_dir_coupling,
                )
            )
        ):
            strength = float(0.0 if legacy_dir_strength is None else legacy_dir_strength)
            release = (
                (1.0, 1.0, 1.0)
                if legacy_dir_release is None
                else tuple(float(value) for value in legacy_dir_release)
            )
            coupling = (
                (
                    (1.0, 0.0, 0.0),
                    (0.0, 1.0, 0.0),
                    (0.0, 0.0, 1.0),
                )
                if legacy_dir_coupling is None
                else tuple(
                    tuple(float(value) for value in row)
                    for row in legacy_dir_coupling
                )
            )
            if len(release) != 3 or len(coupling) != 3 or any(
                len(row) != 3 for row in coupling
            ):
                raise ValueError("Legacy DIR material fields must describe three layers.")
            film_data["dir_material_response_matrix"] = tuple(
                tuple(strength * release[source] * coupling[source][target] for target in range(3))
                for source in range(3)
            )
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
        if "process_steps" in chemistry_data:
            from film_foundry.contracts.process_steps import validate_process_steps
            chemistry_data["process_steps"] = validate_process_steps(chemistry_data["process_steps"])
        if "bath_overrides" in chemistry_data:
            from film_foundry.contracts.bath_overrides import validate_bath_overrides
            chemistry_data["bath_overrides"] = validate_bath_overrides(chemistry_data["bath_overrides"])
        # Phone's historical shorthand selects a program; it is not another chemistry schema.
        phone_chemistry = chemistry_data.pop("chemistry", None)
        if phone_chemistry is not None:
            if phone_chemistry not in {"native", "cross"}:
                raise ValueError("develop.chemistry must be native or cross")
            if phone_chemistry == "cross" and not chemistry_data.get("process_steps") and chemistry_data.get("program_key", "auto") == "auto":
                monochrome = film_data.get("color_process") in {"bw", "black_white", "monochrome"}
                target_positive = film_data.get("image_polarity") != "positive"
                chemistry_data["program_key"] = ("bw_reversal" if target_positive else "bw_negative") if monochrome else ("color_reversal" if target_positive else "color_negative")
        if "light_leak_identity_seed" in chemistry_data:
            identity_seed = chemistry_data["light_leak_identity_seed"]
            if identity_seed is None or str(identity_seed).strip() == "":
                chemistry_data["light_leak_identity_seed"] = None
            else:
                chemistry_data["light_leak_identity_seed"] = int(identity_seed)
        if "exposure_occlusion_identity_seed" in chemistry_data:
            occlusion_seed = chemistry_data["exposure_occlusion_identity_seed"]
            if occlusion_seed is None or str(occlusion_seed).strip() == "":
                chemistry_data["exposure_occlusion_identity_seed"] = None
            else:
                chemistry_data["exposure_occlusion_identity_seed"] = int(occlusion_seed)
        if "incident_identity_seed" in chemistry_data:
            seed = chemistry_data["incident_identity_seed"]
            if seed is None or str(seed).strip() == "":
                chemistry_data["incident_identity_seed"] = None
            elif isinstance(seed, bool) or str(int(seed)) != str(seed).strip() or not 0 <= int(seed) <= 0xFFFFFFFF:
                raise ValueError("develop.incident_identity_seed must be an unsigned 32-bit integer or null")
            else:
                chemistry_data["incident_identity_seed"] = int(seed)
        if "sprocket_count" in film_border_data:
            sprocket_count = film_border_data["sprocket_count"]
            if sprocket_count is None or str(sprocket_count).strip() == "":
                film_border_data["sprocket_count"] = None
            else:
                film_border_data["sprocket_count"] = int(sprocket_count)

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
        if "color_mixer" in scanner_data:
            scanner_data["color_mixer"] = normalize_color_mixer(scanner_data["color_mixer"])
        if "tone_curve" in scanner_data:
            scanner_data["tone_curve"] = normalize_scanner_tone_curve(scanner_data["tone_curve"])
        if "color_curve_rgb" in scanner_data:
            scanner_data["color_curve_rgb"] = normalize_scanner_color_curve_rgb(
                scanner_data["color_curve_rgb"]
            )
        if "device_response_rgb" in scanner_data:
            scanner_data["device_response_rgb"] = normalize_scanner_color_curve_rgb(
                scanner_data["device_response_rgb"],
                field_name="scanner.device_response_rgb",
            )
        if "color_response_model" in scanner_data:
            scanner_data["color_response_model"] = str(
                scanner_data["color_response_model"]
            ).strip().lower() or "simple_channel_adjust_v1"

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

        icc_policy = str(data.get("input_icc_policy", "apply")).strip().lower() or "apply"
        if icc_policy not in {"apply", "ignore"}:
            raise ValueError(
                "input_icc_policy must be 'apply' or 'ignore'; "
                f"got {icc_policy!r}."
            )

        control_mode=str(data.get('process_control_mode','normal'))
        if control_mode not in ('normal','expected','principle'):
            raise ValueError('Unknown process_control_mode')
        return cls(
            film=FilmStockConfig(**film_data),
            exposure=ExposureConfig(**exposure_data),
            chemistry=ChemistryConfig(**chemistry_data),
            scanner=ScannerConfig(**scanner_data),
            look=LookAdjustConfig(**look_data),
            output=OutputConfig(**output_data),
            processing=ProcessingConfig(**processing_data),
            film_border=FilmBorderConfig(**film_border_data),
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
            input_icc_policy=icc_policy,
            process_control_mode=control_mode,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "DarkroomConfig":
        def load(target):
            payload = strict_json_load(target)
            if not isinstance(payload, dict):
                raise ValueError(f"Configuration JSON root must be an object: {path}")
            return cls.from_dict(payload)
        return _config_file_cache.get(path, load, namespace=cls)


FoundryConfig = DarkroomConfig


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
        merged.process_control_mode = develop_config.process_control_mode
        for key in ('enable_grain','enable_halation','enable_mtf'):
            setattr(merged,key,getattr(develop_config,key))
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
