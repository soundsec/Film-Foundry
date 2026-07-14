"""高光触发的光学扩散与 halation。"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from half_frame_darkroom.core.color import luminance
from half_frame_darkroom.model.config import FilmStockConfig


def radius_to_sigma(radius_scale: float, image_shape: tuple[int, int, int]) -> float:
    """把相对图像尺寸的半径转换为 Gaussian sigma。"""
    height, width = image_shape[:2]
    reference = max(1, min(height, width))
    radius_px = max(0.5, float(radius_scale) * reference)
    return max(0.35, radius_px / 2.0)


def soft_threshold(values: np.ndarray, threshold: float, softness: float) -> np.ndarray:
    """生成软阈值遮罩，避免硬切边。"""
    softness = max(float(softness), 1e-6)
    edge0 = threshold - softness
    edge1 = threshold + softness
    x = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _resize_mask(mask: np.ndarray, long_edge: int) -> np.ndarray:
    height, width = mask.shape[:2]
    scale = min(1.0, float(long_edge) / float(max(height, width)))
    if scale >= 1.0:
        return mask
    size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(mask, size, interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=64)
def _cached_halation_psf_kernel(
    height: int,
    width: int,
    core_radius: float,
    exponential_radius: float,
    gaussian_amplitude: float,
    exponential_amplitude: float,
) -> np.ndarray:
    image_shape = (int(height), int(width), 3)
    sigma = radius_to_sigma(core_radius, image_shape)
    radius_r = max(1.0, float(exponential_radius) * min(image_shape[:2]))
    max_radius = int(np.ceil(max(sigma * 4.0, radius_r * 6.0)))
    max_radius = max(3, min(max_radius, 256))

    coords = np.arange(-max_radius, max_radius + 1, dtype=np.float32)
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    r = np.sqrt(xx * xx + yy * yy)

    gaussian = float(gaussian_amplitude) * np.exp(-(r * r) / (2.0 * sigma * sigma))
    exponential = float(exponential_amplitude) * np.exp(-r / max(radius_r, 1e-6))
    kernel = gaussian + exponential
    kernel /= max(float(kernel.sum()), 1e-6)
    return kernel.astype(np.float32)


def halation_psf_kernel(image_shape: tuple[int, int, int], film: FilmStockConfig) -> np.ndarray:
    """Generate and cache the immutable halation PSF for a material/work size."""
    return _cached_halation_psf_kernel(
        int(image_shape[0]),
        int(image_shape[1]),
        float(film.halation_core_radius),
        float(film.halation_exponential_radius),
        float(film.halation_gaussian_amplitude),
        float(film.halation_exponential_amplitude),
    )


def _gradient_mask(values: np.ndarray) -> np.ndarray:
    """估计极陡边缘区域，用于降低阶跃边缘对光晕的异常激发。"""
    grad_x = cv2.Sobel(values, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(values, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)
    scale = float(np.percentile(gradient, 96.0))
    if scale <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    normalized = np.clip(gradient / scale, 0.0, 1.0)
    x = np.clip((normalized - 0.18) / 0.42, 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _halation_source_luminance(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """在触发光晕前加入乳剂层微散射和陡梯度补偿。"""
    y = luminance(image)
    sigma = radius_to_sigma(film.halation_source_blur_radius, image.shape)
    scattered = cv2.GaussianBlur(y, (0, 0), sigmaX=sigma, sigmaY=sigma)
    edge_mask = _gradient_mask(y) * float(np.clip(film.halation_gradient_suppression, 0.0, 1.0))
    return (y * (1.0 - edge_mask) + scattered * edge_mask).astype(np.float32)


def _local_peak_mask(values: np.ndarray, film: FilmStockConfig, image_shape: tuple[int, int, int]) -> np.ndarray:
    """Estimate local highlight peaks and suppress broad bright matte surfaces."""
    sigma = radius_to_sigma(film.halation_peak_radius, image_shape)
    local_base = cv2.GaussianBlur(values, (0, 0), sigmaX=sigma, sigmaY=sigma)
    local_peak = np.clip(values - local_base, 0.0, None)
    relative_peak = local_peak / (local_base + 0.05)
    return soft_threshold(
        relative_peak,
        float(film.halation_peak_threshold),
        float(film.halation_peak_softness),
    )


def _large_bright_area_weight(highlight_mask: np.ndarray, film: FilmStockConfig, image_shape: tuple[int, int, int]) -> np.ndarray:
    """Down-weight large bright regions such as sky, white walls, or white cups."""
    suppression = float(np.clip(film.halation_area_suppression, 0.0, 1.0))
    if suppression <= 0.0:
        return np.ones_like(highlight_mask, dtype=np.float32)

    sigma = radius_to_sigma(film.halation_area_radius, image_shape)
    local_area = cv2.GaussianBlur(highlight_mask, (0, 0), sigmaX=sigma, sigmaY=sigma)
    area_mask = soft_threshold(
        local_area,
        float(film.halation_area_threshold),
        0.25,
    )
    return np.clip(1.0 - suppression * area_mask, 0.0, 1.0).astype(np.float32)


def halation_source_energy(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """Build the leaked highlight energy source for halation."""
    y = _halation_source_luminance(image, film)
    highlight_mask = soft_threshold(y, film.halation_threshold, film.halation_softness)
    peak_mask = _local_peak_mask(y, film, image.shape)
    area_weight = _large_bright_area_weight(highlight_mask, film, image.shape)
    excess_energy = np.clip(y - film.halation_threshold, 0.0, None)
    return (highlight_mask * peak_mask * area_weight * excess_energy).astype(np.float32)


def apply_halation(
    image: np.ndarray,
    film: FilmStockConfig,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> np.ndarray:
    """用 PSF 对高光泄漏能量做卷积，再以暖色散射能量加回线性图像。"""
    image = np.asarray(image, dtype=np.float32)
    if float(film.halation_strength) <= 0.0:
        return image
    source = halation_source_energy(image, film)

    # 低频光晕可以先在较小能量源上扩散，再放回原尺寸；视觉比例仍按图像尺寸计算。
    if work_long_edge is None and fast:
        work_long_edge = 1600
    work_source = _resize_mask(source, long_edge=int(work_long_edge)) if work_long_edge else source
    work_shape = (*work_source.shape, 3)
    kernel = halation_psf_kernel(work_shape, film)
    spread = cv2.filter2D(work_source, cv2.CV_32F, kernel, borderType=cv2.BORDER_CONSTANT)
    if spread.shape != source.shape:
        spread = cv2.resize(spread, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)

    halo_color = np.asarray(film.halation_color, dtype=np.float32)
    halo_color = halo_color / max(float(halo_color.max(initial=1.0)), 1e-6)
    halo = spread[..., None] * halo_color[None, None, :] * film.halation_strength
    return np.clip(image + halo, 0.0, 4.0).astype(np.float32)
