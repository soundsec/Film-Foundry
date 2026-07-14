"""扫描 / 数字化阶段。"""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.core.derived_cache import bounded_inverse_3x3
from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


def negative_total_density_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """CMY 染料密度 + 片基/橙色 mask -> RGB 总光学密度。"""
    density = np.asarray(density_cmy, dtype=np.float32)
    if density.ndim != 3 or density.shape[-1] != 3:
        raise ValueError(f"density_cmy must have HxWx3 shape, got {density.shape}")
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
    total = np.einsum("...l,rl->...r", density, absorption)
    total += np.asarray(film.film_base_density_rgb, dtype=np.float32).reshape(1, 1, 3)
    return np.clip(total, 0.0, None).astype(np.float32)


def negative_transmittance_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """底片总密度 -> 透过率 T = 10^-D。"""
    return np.power(10.0, -negative_total_density_rgb(density_cmy, film)).astype(np.float32)


def capture_transmitted_medium(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
    *,
    illuminant_rgb: np.ndarray | tuple[float, float, float] | None = None,
) -> np.ndarray:
    """Capture a transmissive medium before positive/negative interpretation.

    This shared physical stage observes immutable developed-medium density
    through an illuminant and sensor response. It does not remove a mask,
    invert a negative, or write back to the developed medium.
    """
    total_density_rgb = negative_total_density_rgb(density_cmy, film)
    return capture_optical_density(
        total_density_rgb,
        scanner,
        illuminant_rgb=illuminant_rgb,
    )


def capture_optical_density(
    total_density_rgb: np.ndarray,
    scanner: ScannerConfig | None = None,
    *,
    illuminant_rgb: np.ndarray | tuple[float, float, float] | None = None,
) -> np.ndarray:
    """Capture an already formed RGB optical-density field.

    This is the shared physical sampling stage used when a caller also needs
    to retain the exact density field.  It avoids reconstructing the final
    medium twice and does not perform mask removal or polarity interpretation.
    """
    scanner = scanner or ScannerConfig()
    total_density_rgb = np.asarray(total_density_rgb, dtype=np.float32)
    transmittance = np.power(10.0, -total_density_rgb).astype(np.float32)
    if illuminant_rgb is None:
        illuminant_rgb = scanner.scanner_light_color
    light = np.asarray(illuminant_rgb, dtype=np.float32).reshape(1, 1, 3)
    response = np.asarray(scanner.scanner_response_matrix, dtype=np.float32).reshape(3, 3)
    illuminated = transmittance * light
    raw = np.einsum("...c,rc->...r", illuminated, response)
    # scanner_raw is a normalized sensor signal. Saturation belongs here so
    # the image and its clear-base reference clip under the same exposure.
    return np.clip(raw, 1e-6, 1.0).astype(np.float32)


def render_negative_image(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """Capture a negative transparency through the calibrated backlight."""
    scanner = scanner or ScannerConfig()
    return capture_transmitted_medium(
        density_cmy,
        film,
        scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(scanner),
    )


def render_transparency_image(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """Capture a positive transparency through its light-table illuminant."""
    scanner = scanner or ScannerConfig()
    return capture_transmitted_medium(
        density_cmy,
        film,
        scanner,
        illuminant_rgb=light_table_illuminant_rgb(scanner),
    )


def color_temperature_rgb(temperature_k: float) -> np.ndarray:
    """Approximate normalized RGB illuminant for a Kelvin color temperature."""
    temp = float(np.clip(float(temperature_k), 1000.0, 40000.0)) / 100.0
    if temp <= 66.0:
        red = 255.0
        green = 99.4708025861 * np.log(max(temp, 1e-6)) - 161.1195681661
        blue = 0.0 if temp <= 19.0 else 138.5177312231 * np.log(max(temp - 10.0, 1e-6)) - 305.0447927307
    else:
        red = 329.698727446 * ((temp - 60.0) ** -0.1332047592)
        green = 288.1221695283 * ((temp - 60.0) ** -0.0755148492)
        blue = 255.0
    rgb = np.asarray((red, green, blue), dtype=np.float32)
    rgb = np.clip(rgb, 0.0, 255.0) / 255.0
    return (rgb / max(float(np.max(rgb)), 1e-6)).astype(np.float32)


def light_table_illuminant_rgb(scanner: ScannerConfig | None = None) -> np.ndarray:
    """Return positive-transparency light-table RGB including EV and color temperature."""
    scanner = scanner or ScannerConfig()
    kelvin_rgb = color_temperature_rgb(float(scanner.light_table_temperature_k))
    residual_rgb = np.asarray(scanner.scanner_light_color, dtype=np.float32)
    brightness = 2.0 ** float(scanner.light_table_ev)
    return np.clip(kelvin_rgb * residual_rgb * brightness, 1e-6, None).astype(np.float32)


def negative_backlight_illuminant_rgb(scanner: ScannerConfig | None = None) -> np.ndarray:
    """Return the calibrated transmission illuminant used to capture negatives."""
    scanner = scanner or ScannerConfig()
    kelvin_rgb = color_temperature_rgb(float(scanner.negative_backlight_temperature_k))
    residual_rgb = np.asarray(scanner.scanner_light_color, dtype=np.float32)
    brightness = 2.0 ** float(scanner.negative_backlight_ev)
    return np.clip(kelvin_rgb * residual_rgb * brightness, 1e-6, None).astype(np.float32)


def scan_negative_raw(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """兼容旧命名：返回 scanner raw / negative linear。"""
    return render_negative_image(density_cmy, film, scanner)


def balance_negative_base(
    negative_linear: np.ndarray,
    base_percentile: float = 99.5,
    base_samples: np.ndarray | None = None,
) -> np.ndarray:
    """去除片基/橙罩颜色；有边框样本时优先使用边框。"""
    negative_linear = np.asarray(negative_linear, dtype=np.float32)
    if base_samples is None:
        base = np.percentile(negative_linear, float(base_percentile), axis=(0, 1)).astype(np.float32)
    else:
        samples = np.clip(np.asarray(base_samples, dtype=np.float32), 1e-6, 1.0).reshape(-1, 3)
        base = np.percentile(samples, 50.0, axis=0).astype(np.float32)
    balanced = negative_linear / np.maximum(base, 1e-6).reshape(1, 1, 3)
    return np.clip(balanced, 1e-6, 1.0).astype(np.float32)


def invert_negative_image(base_balanced_negative: np.ndarray) -> np.ndarray:
    """base-balanced negative -> raw positive density。"""
    return (-np.log10(np.clip(base_balanced_negative, 1e-6, 1.0))).astype(np.float32)


def reconstruct_negative_channels(
    scene_density_rgb: np.ndarray,
    scanner: ScannerConfig | None = None,
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, float, float], ...] | None = None,
) -> np.ndarray:
    """Apply reduced-order dye/sensor channel reconstruction in density space.

    Base-mask removal happens first. The matrix approximates channel crosstalk;
    the per-channel exponents approximate remaining nonlinear response,
    including the extra green/blue treatment masked color negatives can need.
    Identity defaults preserve legacy scans and monochrome neutrality.
    """
    scanner = scanner or ScannerConfig()
    density = np.asarray(scene_density_rgb, dtype=np.float32)
    if bool(scanner.negative_channel_compensation_enabled):
        material_matrix = negative_material_compensation_matrix(
            dye_absorption_matrix,
            strength=scanner.negative_channel_compensation_strength,
        )
        density = np.einsum("...c,rc->...r", density, material_matrix)
        density = np.clip(density, 0.0, None)
    matrix = np.asarray(scanner.negative_channel_matrix, dtype=np.float32).reshape(3, 3)
    reconstructed = np.einsum("...c,rc->...r", density, matrix)
    reconstructed = np.clip(reconstructed, 0.0, None)
    gamma = np.asarray(scanner.negative_channel_gamma, dtype=np.float32).reshape(1, 1, 3)
    return np.power(
        np.maximum(reconstructed, 1e-8),
        np.maximum(gamma, 1e-4),
    ).astype(np.float32)


def negative_material_compensation_matrix(
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, float, float], ...] | None,
    *,
    strength: float = 0.35,
) -> np.ndarray:
    """Return the bounded material-aware density reconstruction matrix."""
    absorption = np.asarray(
        dye_absorption_matrix
        if dye_absorption_matrix is not None
        else (
            (1.00, 0.10, 0.04),
            (0.08, 1.00, 0.12),
            (0.03, 0.16, 1.00),
        ),
        dtype=np.float32,
    ).reshape(3, 3)
    # The full inverse is too aggressive for a reduced RGB model that also
    # contains silver/fog density. Blend and bound it instead of pretending to
    # perform spectral dye separation.
    inverse_absorption = bounded_inverse_3x3(absorption, -0.30, 1.35)
    mix = float(np.clip(strength, 0.0, 1.0))
    identity = np.eye(3, dtype=np.float32)
    return (identity + mix * (inverse_absorption - identity)).astype(np.float32)


def _smoothstep(values: np.ndarray, edge0: float, edge1: float) -> np.ndarray:
    x = np.clip((values - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _luma_preserving_saturation(image: np.ndarray, saturation: float) -> np.ndarray:
    """扫描输出阶段的色彩浓度调节，保留亮度关系，不回写底片材料。"""
    saturation = float(max(saturation, 0.0))
    if abs(saturation - 1.0) < 1e-6:
        return image.astype(np.float32)
    luma = (
        image[..., 0:1] * 0.2126
        + image[..., 1:2] * 0.7152
        + image[..., 2:3] * 0.0722
    )
    return np.clip(luma + (image - luma) * saturation, 0.0, 1.0).astype(np.float32)


def _soft_white_rolloff(image: np.ndarray, softness: float) -> np.ndarray:
    softness = float(np.clip(softness, 0.0, 0.95))
    if softness <= 0.0:
        return image.astype(np.float32)
    image = np.asarray(image, dtype=np.float32)
    knee = 1.0 - softness
    luma = (
        image[..., 0:1] * 0.2126
        + image[..., 1:2] * 0.7152
        + image[..., 2:3] * 0.0722
    )
    rolled = np.where(
        luma <= knee,
        luma,
        knee + (1.0 - knee) * (1.0 - np.exp(-(luma - knee) / max(1.0 - knee, 1e-6))),
    )
    scale = rolled / np.maximum(luma, 1e-6)
    return np.clip(image * scale, 0.0, 1.0).astype(np.float32)


def _black_adaptation_lift(image: np.ndarray, strength: float) -> np.ndarray:
    strength = float(np.clip(strength, 0.0, 0.95))
    if strength <= 0.0:
        return image.astype(np.float32)
    image = np.asarray(image, dtype=np.float32)
    luma = (
        image[..., 0:1] * 0.2126
        + image[..., 1:2] * 0.7152
        + image[..., 2:3] * 0.0722
    )
    lift = strength * np.square(np.clip(1.0 - luma, 0.0, 1.0))
    return np.clip(image + (1.0 - image) * lift * 0.22, 0.0, 1.0).astype(np.float32)


def render_positive_scan(
    positive_raw_density: np.ndarray,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
    paper_black: float = 0.0,
    paper_white: float = 1.0,
) -> np.ndarray:
    """raw positive density -> 渲染后的正像线性 RGB。"""
    scanner = scanner or ScannerConfig()
    positive_raw_density = np.asarray(positive_raw_density, dtype=np.float32)
    reference = np.asarray(scanner.print_reference_density, dtype=np.float32)
    color_shift = np.asarray(scanner.print_color_shift, dtype=np.float32)
    gamma = float(scanner.print_gamma) * max(float(print_contrast), 0.01)

    log_positive = (positive_raw_density - reference) * gamma + color_shift
    positive = np.power(10.0, log_positive) * (2.0 ** float(print_exposure_ev))

    if str(scanner.print_mapping_mode).lower() == "sigmoid":
        mapped = positive / (1.0 + positive)
    else:
        mapped = 1.0 - np.exp(-positive)
    mapped = mapped * np.asarray(scanner.print_color_bias, dtype=np.float32)[None, None, :]

    luma = mapped[..., 0] * 0.2126 + mapped[..., 1] * 0.7152 + mapped[..., 2] * 0.0722
    highlight_weight = _smoothstep(
        luma,
        scanner.highlight_bias_threshold,
        scanner.highlight_bias_threshold + scanner.highlight_bias_softness,
    )[..., None]
    highlight_bias = np.asarray(scanner.highlight_color_bias, dtype=np.float32)[None, None, :]
    mapped = mapped * (1.0 + highlight_weight * (highlight_bias - 1.0))
    mapped = _luma_preserving_saturation(mapped, scanner.scan_saturation)

    return (
        float(paper_black) + np.clip(mapped, 0.0, 1.0) * (float(paper_white) - float(paper_black))
    ).astype(np.float32)


def scanner_raw_to_positive_rgb(
    scanner_raw: np.ndarray,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
    paper_black: float = 0.0,
    paper_white: float = 1.0,
    base_percentile: float = 99.5,
    base_samples: np.ndarray | None = None,
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, float, float], ...] | None = None,
) -> np.ndarray:
    """scanner raw -> 去片基 -> 反相 -> 正像渲染。"""
    scanner = scanner or ScannerConfig()
    balanced = balance_negative_base(scanner_raw, base_percentile=base_percentile, base_samples=base_samples)
    raw_positive = invert_negative_image(balanced)
    reconstructed = reconstruct_negative_channels(
        raw_positive,
        scanner,
        dye_absorption_matrix=dye_absorption_matrix,
    )
    return render_positive_scan(
        reconstructed,
        scanner,
        print_contrast=print_contrast,
        print_exposure_ev=print_exposure_ev,
        paper_black=paper_black,
        paper_white=paper_white,
    )


def render_positive_transparency_scan(
    transparency_rgb: np.ndarray,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
) -> np.ndarray:
    """Render a positive transparency/light-table scan without negative inversion."""
    scanner = scanner or ScannerConfig()
    image = np.asarray(transparency_rgb, dtype=np.float32)
    image = np.clip(image * (2.0 ** float(print_exposure_ev)), 0.0, None)
    color_strength = float(np.clip(scanner.positive_scan_color_control_strength, 0.0, 1.0))

    contrast = max(float(scanner.print_gamma) * max(float(print_contrast), 0.01), 0.01)
    image = np.clip((image - 0.5) * contrast + 0.5, 0.0, None)
    image = image * np.power(10.0, np.asarray(scanner.print_color_shift, dtype=np.float32) * color_strength)[None, None, :]
    color_bias = 1.0 + (np.asarray(scanner.print_color_bias, dtype=np.float32) - 1.0) * color_strength
    image = image * color_bias[None, None, :]

    luma = image[..., 0] * 0.2126 + image[..., 1] * 0.7152 + image[..., 2] * 0.0722
    highlight_weight = _smoothstep(
        luma,
        scanner.highlight_bias_threshold,
        scanner.highlight_bias_threshold + scanner.highlight_bias_softness,
    )[..., None]
    highlight_bias = 1.0 + (np.asarray(scanner.highlight_color_bias, dtype=np.float32) - 1.0) * color_strength
    highlight_bias = highlight_bias[None, None, :]
    image = image * (1.0 + highlight_weight * (highlight_bias - 1.0))
    image = _black_adaptation_lift(image, scanner.projection_black_adaptation)
    image = _soft_white_rolloff(image, scanner.projection_white_softness)
    return _luma_preserving_saturation(image, scanner.scan_saturation)


def normalize_scan_rgb(
    image: np.ndarray,
    black_percentile: float = 0.3,
    white_percentile: float = 99.7,
    strength: float = 1.0,
    mode: str = "luma",
) -> np.ndarray:
    """扫描软件后期定黑白点。luma 模式保留色偏，rgb 模式更像自动白平衡。"""
    image = np.asarray(image, dtype=np.float32)
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        return np.clip(image, 0.0, 1.0).astype(np.float32)

    black, white = scan_normalization_range(
        image,
        black_percentile=black_percentile,
        white_percentile=white_percentile,
        mode=mode,
    )
    return apply_scan_normalization_range(image, black, white, strength=strength)


def scan_normalization_range(
    image: np.ndarray,
    black_percentile: float = 0.3,
    white_percentile: float = 99.7,
    mode: str = "luma",
) -> tuple[np.ndarray, np.ndarray]:
    """Measure one global black/white range without changing scan pixels."""
    image = np.asarray(image, dtype=np.float32)

    if str(mode).lower() == "rgb":
        # Process channels independently so percentile selection never needs a
        # second full HxWx3 work array on large scans.
        black = np.asarray(
            [np.percentile(image[..., channel], float(black_percentile)) for channel in range(3)],
            dtype=np.float32,
        )
        white = np.asarray(
            [np.percentile(image[..., channel], float(white_percentile)) for channel in range(3)],
            dtype=np.float32,
        )
    else:
        luma = image[..., 0] * 0.2126 + image[..., 1] * 0.7152 + image[..., 2] * 0.0722
        black_value = float(np.percentile(luma, float(black_percentile)))
        white_value = float(np.percentile(luma, float(white_percentile)))
        black = np.asarray((black_value, black_value, black_value), dtype=np.float32)
        white = np.asarray((white_value, white_value, white_value), dtype=np.float32)
    return black, white


def apply_scan_normalization_range(
    image: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
    *,
    strength: float = 1.0,
) -> np.ndarray:
    """Apply a previously measured global range to an image or row tile."""
    image = np.asarray(image, dtype=np.float32)
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        return np.clip(image, 0.0, 1.0).astype(np.float32)
    black = np.asarray(black, dtype=np.float32).reshape(3)
    white = np.asarray(white, dtype=np.float32).reshape(3)

    scale = white - black
    if float(np.max(scale)) <= 1e-6:
        return np.clip(image, 0.0, 1.0).astype(np.float32)
    normalized = np.clip((image - black) / np.maximum(scale, 1e-6), 0.0, 1.0)
    blended = image * (1.0 - strength) + normalized * strength
    return np.clip(blended, 0.0, 1.0).astype(np.float32)
