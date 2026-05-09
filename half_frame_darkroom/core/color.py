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
    """把灰度/RGBA/整数图像整理成 [0, 1] 范围内的 float32 RGB。"""
    image = np.asarray(image)
    if image.ndim == 2:
        image = np.repeat(image[..., None], 3, axis=-1)
    if image.shape[-1] == 4:
        image = image[..., :3]
    if image.dtype == np.uint8:
        image = image.astype(np.float32) / 255.0
    elif image.dtype == np.uint16:
        image = image.astype(np.float32) / 65535.0
    else:
        image = image.astype(np.float32)
        if image.max(initial=0.0) > 1.0:
            image = image / 255.0
    return np.clip(image, 0.0, 1.0).astype(np.float32)
