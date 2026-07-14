"""Film Foundry 的颜色空间工具。

输入图像被视为显示编码的“场景代理”。这里的 sRGB 解码只用于建立近似线性
工作空间，方便后续做光学扩散、响应曲线和颗粒叠加；它不代表还原真实场景辐照度。
"""

from __future__ import annotations

import numpy as np


Array = np.ndarray


def srgb_to_linear(image: Array) -> Array:
    """把归一化 sRGB 转成近似线性工作空间。"""
    image = np.asarray(image, dtype=np.float32)
    image = np.clip(image, 0.0, 1.0)
    low = image <= 0.04045
    return np.where(low, image / 12.92, ((image + 0.055) / 1.055) ** 2.4).astype(
        np.float32
    )


def linear_to_srgb(image: Array) -> Array:
    """把近似线性 RGB 编码回归一化 sRGB。"""
    image = np.asarray(image, dtype=np.float32)
    image = np.clip(image, 0.0, 1.0)
    low = image <= 0.0031308
    return np.where(low, image * 12.92, 1.055 * np.power(image, 1.0 / 2.4) - 0.055).astype(
        np.float32
    )


def luminance(image: Array) -> Array:
    """从线性 RGB 计算 Rec. 709 亮度。"""
    image = np.asarray(image, dtype=np.float32)
    return (
        image[..., 0] * 0.2126
        + image[..., 1] * 0.7152
        + image[..., 2] * 0.0722
    ).astype(np.float32)


def apply_color_matrix(image: Array, matrix: Array) -> Array:
    """对 RGB 图像应用 3x3 色彩矩阵，用于层间串扰/色彩响应近似。"""
    image = np.asarray(image, dtype=np.float32)
    matrix = np.asarray(matrix, dtype=np.float32).reshape(3, 3)
    return np.einsum("...c,dc->...d", image, matrix).astype(np.float32)


def ensure_rgb_float(image: Array) -> Array:
    """把灰度/RGB/RGBA 图像整理成 [0, 1] 范围内的 float32 RGB。"""
    image = np.asarray(image)
    if image.ndim not in {2, 3}:
        raise ValueError(f"Image array must be 2D grayscale or 3D color data, got shape {image.shape}.")
    if image.size == 0 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Image array must have non-zero width and height, got shape {image.shape}.")

    if np.issubdtype(image.dtype, np.integer):
        image = image.astype(np.float32) / max(float(np.iinfo(image.dtype).max), 1.0)
    else:
        image = image.astype(np.float32)
        if not np.isfinite(image).all():
            raise ValueError("Image array contains NaN or infinite values.")
        max_value = float(np.max(image))
        if max_value > 1.0:
            image = image / 255.0

    if image.ndim == 2:
        return np.clip(np.repeat(image[..., None], 3, axis=-1), 0.0, 1.0).astype(np.float32)

    channels = image.shape[-1]
    if channels == 1:
        image = np.repeat(image, 3, axis=-1)
    elif channels == 2:
        gray = np.repeat(image[..., :1], 3, axis=-1)
        alpha = np.clip(image[..., 1:2], 0.0, 1.0)
        image = gray * alpha + (1.0 - alpha)
    elif channels == 3:
        pass
    elif channels == 4:
        rgb = image[..., :3]
        alpha = np.clip(image[..., 3:4], 0.0, 1.0)
        # 透明图不是物理照片；按白底合成，避免透明区域被静默当成黑色曝光。
        image = rgb * alpha + (1.0 - alpha)
    else:
        raise ValueError(
            f"Unsupported channel count {channels}. Film Foundry currently accepts grayscale, RGB, or RGBA images."
        )
    image = np.clip(image, 0.0, 1.0).astype(np.float32)
    if not np.isfinite(image).all():
        raise ValueError("Image conversion produced NaN or infinite RGB values.")
    return image
