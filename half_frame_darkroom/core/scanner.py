"""扫描 / 数字化阶段。"""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


def negative_total_density_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """CMY 染料密度 + 片基/橙色 mask -> RGB 总光学密度。"""
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
    dye_density_rgb = np.einsum("...l,rl->...r", density_cmy, absorption)
    base_density = np.asarray(film.film_base_density_rgb, dtype=np.float32).reshape(1, 1, 3)
    return np.clip(dye_density_rgb + base_density, 0.0, None).astype(np.float32)


def negative_transmittance_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """底片总密度 -> 透过率 T = 10^-D。"""
    return np.power(10.0, -negative_total_density_rgb(density_cmy, film)).astype(np.float32)


def render_negative_image(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """底片密度 -> 扫描器看到的线性负片图像。"""
    scanner = scanner or ScannerConfig()
    transmittance = negative_transmittance_rgb(density_cmy, film)
    light = np.asarray(scanner.scanner_light_color, dtype=np.float32).reshape(1, 1, 3)
    response = np.asarray(scanner.scanner_response_matrix, dtype=np.float32).reshape(3, 3)
    illuminated = transmittance * light
    raw = np.einsum("...c,rc->...r", illuminated, response)
    return np.clip(raw, 1e-6, None).astype(np.float32)


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
        samples = np.asarray(base_samples, dtype=np.float32).reshape(-1, 3)
        base = np.percentile(samples, 50.0, axis=0).astype(np.float32)
    balanced = negative_linear / np.maximum(base, 1e-6).reshape(1, 1, 3)
    return np.clip(balanced, 1e-6, 1.0).astype(np.float32)


def invert_negative_image(base_balanced_negative: np.ndarray) -> np.ndarray:
    """base-balanced negative -> raw positive density。"""
    return (-np.log10(np.clip(base_balanced_negative, 1e-6, 1.0))).astype(np.float32)


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
) -> np.ndarray:
    """scanner raw -> 去片基 -> 反相 -> 正像渲染。"""
    scanner = scanner or ScannerConfig()
    balanced = balance_negative_base(scanner_raw, base_percentile=base_percentile, base_samples=base_samples)
    raw_positive = invert_negative_image(balanced)
    return render_positive_scan(
        raw_positive,
        scanner,
        print_contrast=print_contrast,
        print_exposure_ev=print_exposure_ev,
        paper_black=paper_black,
        paper_white=paper_white,
    )


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

    if str(mode).lower() == "rgb":
        black = np.percentile(image, float(black_percentile), axis=(0, 1)).astype(np.float32)
        white = np.percentile(image, float(white_percentile), axis=(0, 1)).astype(np.float32)
    else:
        luma = image[..., 0] * 0.2126 + image[..., 1] * 0.7152 + image[..., 2] * 0.0722
        black_value = float(np.percentile(luma, float(black_percentile)))
        white_value = float(np.percentile(luma, float(white_percentile)))
        black = np.asarray((black_value, black_value, black_value), dtype=np.float32)
        white = np.asarray((white_value, white_value, white_value), dtype=np.float32)

    scale = white - black
    if float(np.max(scale)) <= 1e-6:
        return np.clip(image, 0.0, 1.0).astype(np.float32)
    normalized = np.clip((image - black) / np.maximum(scale, 1e-6), 0.0, 1.0)
    blended = image * (1.0 - strength) + normalized * strength
    return np.clip(blended, 0.0, 1.0).astype(np.float32)
