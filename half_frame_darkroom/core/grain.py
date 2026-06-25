"""Correlated, multi-scale film grain deposition."""

from __future__ import annotations

# Legacy image-space correlated grain helper.
#
# The current electronic-negative pipeline uses
# density_grain.apply_density_grain() to perturb CMY density. This module is
# kept for old tests and experiments.

import cv2
import numpy as np

from half_frame_darkroom.core.color import luminance
from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def midtone_weight(luma: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    """Gaussian midtone weighting for visible grain deposition."""
    sigma = max(float(sigma), 1e-6)
    return np.exp(-((luma - mu) ** 2) / (2.0 * sigma * sigma)).astype(np.float32)


def _correlated_noise(
    rng: np.random.Generator,
    shape: tuple[int, int],
    sigma: float,
) -> np.ndarray:
    noise = rng.normal(0.0, 1.0, shape).astype(np.float32)
    blurred = cv2.GaussianBlur(noise, (0, 0), sigmaX=sigma, sigmaY=sigma)
    std = float(blurred.std())
    if std > 1e-6:
        blurred = blurred / std
    return blurred.astype(np.float32)


def apply_grain(
    image: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
    rng: np.random.Generator | None = None,
    fast: bool = False,
) -> np.ndarray:
    """沉积多尺度、空间相关、受亮度影响的胶片颗粒。"""
    image = np.asarray(image, dtype=np.float32)
    rng = rng or np.random.default_rng()
    height, width = image.shape[:2]

    weights = np.asarray(film.grain_scale_weights, dtype=np.float32)
    weights = weights / max(float(weights.sum()), 1e-6)

    # 快速模式在较小尺寸上生成颗粒场，再放大回原图。它牺牲一点精细随机性，
    # 但用于预览和大图试参数会快很多。
    if fast and max(height, width) > 1600:
        scale = 1600.0 / float(max(height, width))
        noise_height = max(1, int(round(height * scale)))
        noise_width = max(1, int(round(width * scale)))
        noise_shape = (noise_height, noise_width)
        sigma_shape = (noise_height, noise_width, 3)
    else:
        noise_shape = (height, width)
        sigma_shape = image.shape

    field = np.zeros(noise_shape, dtype=np.float32)
    for scale, weight in zip(film.grain_scales, weights, strict=False):
        sigma = radius_to_sigma(scale, sigma_shape)
        field += float(weight) * _correlated_noise(rng, noise_shape, sigma)

    if field.shape != (height, width):
        field = cv2.resize(field, (width, height), interpolation=cv2.INTER_LINEAR)

    luma = luminance(image)
    visibility = midtone_weight(luma, film.grain_midtone_mu, film.grain_midtone_sigma)

    push = float(max(chemistry.push_stops, 0.0))
    temp_delta = max(0.0, float(chemistry.temperature_c) - 20.0)
    exhaustion = float(np.clip(chemistry.developer_exhaustion, 0.0, 1.0))
    chemistry_gain = 1.0 + 0.32 * push + 0.018 * temp_delta + 0.45 * exhaustion

    chroma_bias = np.asarray((1.00, 0.94, 0.88), dtype=np.float32)
    amplitude = film.grain_strength * chemistry_gain
    deposit = amplitude * visibility[..., None] * field[..., None] * chroma_bias[None, None, :]

    density_bias = 0.35 + 0.65 * np.sqrt(np.clip(image, 0.0, 1.0))
    return np.clip(image + deposit * density_bias, 0.0, 1.0).astype(np.float32)
