"""乳剂解析力、空间频率和数字锐化伪影抑制。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.color import luminance
from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.model.config import FilmStockConfig


def _robust_normalize(values: np.ndarray, percentile: float = 96.0) -> np.ndarray:
    """用高百分位做归一化，避免少数极端边缘把遮罩压扁。"""
    scale = float(np.percentile(values, percentile))
    if scale <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip(values / scale, 0.0, 1.0).astype(np.float32)


def _above_threshold_mask(values: np.ndarray, threshold: float, width: float) -> np.ndarray:
    """只让阈值以上的区域逐渐进入遮罩，平坦区域保持 0。"""
    x = np.clip((values - threshold) / max(width, 1e-6), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def local_frequency_mask(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """估计局部高空间频率区域，用于调制乳剂低通强度。"""
    y = luminance(image)
    sigma = radius_to_sigma(film.emulsion_blur_radius, image.shape)
    low = cv2.GaussianBlur(y, (0, 0), sigmaX=sigma, sigmaY=sigma)

    # 高频残差捕捉单像素锐边和 ISP 锐化产生的局部振铃。
    highpass = np.abs(y - low)

    # 梯度用于识别接近阶跃函数的边缘。Sobel 比简单差分更稳一点。
    grad_x = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)

    combined = highpass + 0.35 * gradient
    normalized = _robust_normalize(combined)
    return _above_threshold_mask(normalized, film.high_frequency_threshold, 0.32)


def apply_emulsion_mtf(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """模拟胶片乳剂/银盐颗粒系统的有限空间截止频率。

    这一步不是普通柔焦。它主要作用于数码输入中超过胶片颗粒承载能力的
    高频边缘和锐化振铃，让细节先进入乳剂解析力限制，再由后续颗粒显影出来。
    """
    image = np.asarray(image, dtype=np.float32)
    strength = float(np.clip(film.emulsion_mtf_strength, 0.0, 1.0))
    artifact_strength = float(np.clip(film.digital_artifact_suppression, 0.0, 1.0))
    if strength <= 0.0 and artifact_strength <= 0.0:
        return image

    sigma = radius_to_sigma(film.emulsion_blur_radius, image.shape)
    emulsion_blur = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma, sigmaY=sigma)
    stronger_blur = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma * 1.8, sigmaY=sigma * 1.8)

    frequency_mask = local_frequency_mask(image, film)[..., None]

    # 高频区域按乳剂解析力做低通；极端锐化伪影再额外靠向稍强的模糊结果。
    mtf_blend = np.clip(frequency_mask * strength, 0.0, 1.0)
    artifact_blend = np.clip(frequency_mask * artifact_strength * 0.55, 0.0, 1.0)
    out = image * (1.0 - mtf_blend) + emulsion_blur * mtf_blend
    out = out * (1.0 - artifact_blend) + stronger_blur * artifact_blend
    return np.clip(out, 0.0, 4.0).astype(np.float32)
