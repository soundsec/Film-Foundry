"""预览尺寸处理。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.scanner import negative_total_density_rgb
from half_frame_darkroom.model.config import FilmStockConfig


def resize_to_long_edge(image: np.ndarray, long_edge: int | None) -> np.ndarray:
    """把图像最长边缩到指定像素；None 或尺寸已足够小时保持原图。"""
    if long_edge is None:
        return image
    long_edge = int(long_edge)
    if long_edge <= 0:
        return image

    height, width = image.shape[:2]
    current_long_edge = max(height, width)
    if current_long_edge <= long_edge:
        return image

    scale = long_edge / float(current_long_edge)
    new_size = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    return cv2.resize(image, new_size, interpolation=cv2.INTER_AREA).astype(np.float32)


def negative_visual_preview(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    base_mask_color: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """把 CMY 底片密度转换成接近肉眼透射观看的彩色负片预览。

    density_cmy 是物理/诊断数据，不应直接当作显示图像。负片外观应先把染料
    密度映射到 RGB 光学密度，再用 T = 10^-D 得到透过率；橙色片基在这里用
    一个简化的透射色罩表示。
    """
    if base_mask_color is None:
        rgb_density = negative_total_density_rgb(density_cmy, film)
        preview = np.power(10.0, -np.clip(rgb_density, 0.0, None))
    else:
        rgb_density = negative_total_density_rgb(density_cmy, film)
        base_density = np.asarray(film.film_base_density_rgb, dtype=np.float32).reshape(1, 1, 3)
        dye_density = np.clip(rgb_density - base_density, 0.0, None)
        dye_transmittance = np.power(10.0, -dye_density)
        base = np.asarray(base_mask_color, dtype=np.float32).reshape(1, 1, 3)
        preview = dye_transmittance * base

    # 仅为屏幕查看做全局曝光归一化，不做分通道白平衡，避免洗掉负片色罩。
    luma = preview[..., 0] * 0.2126 + preview[..., 1] * 0.7152 + preview[..., 2] * 0.0722
    white = float(np.percentile(luma, 99.5))
    if white > 1e-6:
        preview = preview * (0.92 / white)
    return np.clip(preview, 0.0, 1.0).astype(np.float32)


def optical_density_visual_preview(optical_density_rgb: np.ndarray) -> np.ndarray:
    """Display an authoritative final RGB optical-density field as transmission."""
    density = np.asarray(optical_density_rgb, dtype=np.float32)
    if density.ndim != 3 or density.shape[-1] != 3:
        raise ValueError(f"optical_density_rgb must have HxWx3 shape, got {density.shape}")
    preview = np.power(10.0, -np.clip(density, 0.0, None)).astype(np.float32)
    luma = preview[..., 0] * 0.2126 + preview[..., 1] * 0.7152 + preview[..., 2] * 0.0722
    white = float(np.percentile(luma, 99.5))
    if white > 1e-6:
        preview *= 0.92 / white
    return np.clip(preview, 0.0, 1.0).astype(np.float32)
