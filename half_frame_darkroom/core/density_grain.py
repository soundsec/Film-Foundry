"""遵循密度统计关系的随机颗粒核心。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def apply_density_grain(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
    rng: np.random.Generator | None = None,
    fast: bool = False,
) -> np.ndarray:
    """在密度域加入颗粒扰动，sigma_D 与 sqrt(D) 绑定。"""
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    rng = rng or np.random.default_rng()
    height, width = density_cmy.shape[:2]

    if fast and max(height, width) > 1600:
        scale = 1600.0 / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
        small_density = cv2.resize(density_cmy, (work_shape[1], work_shape[0]), interpolation=cv2.INTER_AREA)
    else:
        work_shape = (height, width)
        small_density = density_cmy

    d_min = np.asarray(film.density_min, dtype=np.float32)
    sigma_base = np.asarray(film.granularity_sigma, dtype=np.float32)
    push = max(float(chemistry.push_stops), 0.0)
    exhaustion = float(np.clip(chemistry.developer_exhaustion, 0.0, 1.0))
    temp_gain = max(0.0, float(chemistry.temperature_c) - 20.0) * 0.012
    chemistry_gain = 1.0 + 0.18 * push + 0.35 * exhaustion + temp_gain

    sigma = sigma_base * chemistry_gain * np.sqrt(np.clip(small_density - d_min, 0.0, None))
    noise = rng.normal(0.0, 1.0, small_density.shape).astype(np.float32) * sigma

    # grain_density_correlation_radius 是相对画幅的尺寸；这里按当前处理尺寸换算为像素 sigma。
    blur_sigma = radius_to_sigma(film.grain_density_correlation_radius, (*work_shape, 3))
    for channel in range(3):
        noise[..., channel] = cv2.GaussianBlur(
            noise[..., channel],
            (0, 0),
            sigmaX=blur_sigma,
            sigmaY=blur_sigma,
        )

    if work_shape != (height, width):
        noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)

    return np.clip(density_cmy + noise, 0.0, None).astype(np.float32)
