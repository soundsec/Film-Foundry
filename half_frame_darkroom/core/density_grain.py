"""遵循密度统计关系的随机颗粒核心。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def apply_density_grain(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
    rng: np.random.Generator | None = None,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> np.ndarray:
    """在密度域加入颗粒扰动，sigma_D 与 sqrt(D) 绑定。"""
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    rng = rng or np.random.default_rng()
    height, width = density_cmy.shape[:2]

    if work_long_edge is None and fast:
        work_long_edge = 1600

    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
        small_density = cv2.resize(density_cmy, (work_shape[1], work_shape[0]), interpolation=cv2.INTER_AREA)
    else:
        work_shape = (height, width)
        small_density = density_cmy

    development = build_effective_development(chemistry)
    d_min = np.asarray(film.density_min, dtype=np.float32) + development.d_min_shift
    sigma_base = np.asarray(film.granularity_sigma, dtype=np.float32)

    sigma = sigma_base * development.grain_factor * np.sqrt(np.clip(small_density - d_min, 0.0, None))
    noise = rng.normal(0.0, 1.0, small_density.shape).astype(np.float32) * sigma

    # grain_density_correlation_radius 是相对画幅的尺寸；这里按当前处理尺寸换算为像素 sigma。
    blur_sigma = radius_to_sigma(
        film.grain_density_correlation_radius * development.grain_radius_factor,
        (*work_shape, 3),
    )
    for channel in range(3):
        noise[..., channel] = cv2.GaussianBlur(
            noise[..., channel],
            (0, 0),
            sigmaX=blur_sigma,
            sigmaY=blur_sigma,
        )

    if development.residue_factor > 1e-6:
        residue_sigma = radius_to_sigma(0.010 * development.grain_radius_factor, (*work_shape, 3))
        residue = rng.normal(0.0, 1.0, small_density.shape[:2]).astype(np.float32)
        residue = cv2.GaussianBlur(residue, (0, 0), sigmaX=residue_sigma, sigmaY=residue_sigma)
        residue = residue - float(np.min(residue))
        residue = residue / max(float(np.max(residue)), 1e-6)
        residue = (residue - 0.35).clip(0.0, 1.0)
        residue_strength = 0.018 * float(development.residue_factor)
        silver_strength = 0.012 * float(development.silvering_factor)
        residue_density = residue[..., None] * (residue_strength + silver_strength)
        noise = noise + residue_density.astype(np.float32)

    if work_shape != (height, width):
        noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)

    return np.clip(density_cmy + noise, 0.0, None).astype(np.float32)
