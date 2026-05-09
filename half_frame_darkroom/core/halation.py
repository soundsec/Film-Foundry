"""高光触发的光学扩散与 halation。"""

from __future__ import annotations

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


def halation_psf_kernel(image_shape: tuple[int, int, int], film: FilmStockConfig) -> np.ndarray:
    """生成 PSF_halation(r) = A exp(-r^2/2sigma^2) + B exp(-r/R)。"""
    sigma = radius_to_sigma(film.halation_core_radius, image_shape)
    radius_r = max(1.0, float(film.halation_exponential_radius) * min(image_shape[:2]))
    max_radius = int(np.ceil(max(sigma * 4.0, radius_r * 6.0)))
    max_radius = max(3, min(max_radius, 256))

    coords = np.arange(-max_radius, max_radius + 1, dtype=np.float32)
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    r = np.sqrt(xx * xx + yy * yy)

    gaussian = float(film.halation_gaussian_amplitude) * np.exp(-(r * r) / (2.0 * sigma * sigma))
    exponential = float(film.halation_exponential_amplitude) * np.exp(-r / max(radius_r, 1e-6))
    kernel = gaussian + exponential
    kernel /= max(float(kernel.sum()), 1e-6)
    return kernel.astype(np.float32)


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


def apply_halation(image: np.ndarray, film: FilmStockConfig, fast: bool = False) -> np.ndarray:
    """用 PSF 对高光泄漏能量做卷积，再以暖色散射能量加回线性图像。"""
    image = np.asarray(image, dtype=np.float32)
    y = _halation_source_luminance(image, film)
    highlight_mask = soft_threshold(y, film.halation_threshold, film.halation_softness)
    excess_energy = np.clip(y - film.halation_threshold, 0.0, None)
    source = highlight_mask * excess_energy

    # 快速模式先在较小能量源上扩散，再放回原尺寸；视觉比例仍按图像尺寸计算。
    work_source = _resize_mask(source, long_edge=1600) if fast else source
    work_shape = (*work_source.shape, 3)
    kernel = halation_psf_kernel(work_shape, film)
    spread = cv2.filter2D(work_source, cv2.CV_32F, kernel, borderType=cv2.BORDER_REFLECT)
    if spread.shape != source.shape:
        spread = cv2.resize(spread, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_LINEAR)

    halo_color = np.asarray(film.halation_color, dtype=np.float32)
    halo_color = halo_color / max(float(halo_color.max(initial=1.0)), 1e-6)
    halo = spread[..., None] * halo_color[None, None, :] * film.halation_strength
    return np.clip(image + halo, 0.0, 4.0).astype(np.float32)
