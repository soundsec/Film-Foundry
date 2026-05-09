"""基于 H-D 曲线的感光测定核心。"""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def rgb_exposure_to_layer_exposure(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """把近似线性 RGB 曝光代理映射到 C/M/Y 三层乳剂的相对曝光量 E。"""
    image = np.asarray(image, dtype=np.float32)
    matrix = np.asarray(film.layer_sensitivity_matrix, dtype=np.float32).reshape(3, 3)
    exposure = np.einsum("...c,lc->...l", np.clip(image, 0.0, None), matrix)
    return np.clip(exposure, 1e-6, None).astype(np.float32)


def _softplus(x: np.ndarray, width: float) -> np.ndarray:
    """稳定的 softplus(x / width) * width，用于连续趾部/肩部过渡。"""
    width = max(float(width), 1e-6)
    z = np.clip(x / width, -60.0, 60.0)
    return (np.log1p(np.exp(z)) * width).astype(np.float32)


def hd_density_curve(
    exposure: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
) -> np.ndarray:
    """由相对曝光量 E 计算三层染料密度 D。

    中间直线区满足 D = gamma * log10(E) + 常量。趾部和肩部通过两个
    softplus 差分项形成渐近线，使斜率从 0 -> gamma -> 0 连续变化。
    """
    exposure = np.clip(np.asarray(exposure, dtype=np.float32), 1e-6, None)
    log_e = np.log10(exposure)

    push = float(chemistry.push_stops)
    exhaustion = float(np.clip(chemistry.developer_exhaustion, 0.0, 1.0))
    temp_delta = float(chemistry.temperature_c) - 20.0

    gamma = np.asarray(film.hd_gamma, dtype=np.float32) * (1.0 + 0.08 * push)
    gamma *= 1.0 - 0.12 * exhaustion
    gamma *= 1.0 + 0.004 * temp_delta

    d_min = np.asarray(film.density_min, dtype=np.float32)
    d_min = d_min + 0.012 * max(push, 0.0) + 0.030 * exhaustion
    d_max = np.asarray(film.density_max, dtype=np.float32)
    d_max = d_max * (1.0 - 0.08 * exhaustion)

    toe = np.asarray(film.log_exposure_toe, dtype=np.float32) - 0.10 * push
    shoulder = np.asarray(film.log_exposure_shoulder, dtype=np.float32) - 0.06 * push

    toe_term = _softplus(log_e - toe, film.hd_toe_width)
    shoulder_term = _softplus(log_e - shoulder, film.hd_shoulder_width)
    density = d_min + gamma * (toe_term - shoulder_term)
    return np.clip(density, d_min, d_max).astype(np.float32)


def exposure_to_density(
    image: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
) -> np.ndarray:
    """完整的 RGB 曝光代理 -> 三层 CMY 染料密度。"""
    layer_exposure = rgb_exposure_to_layer_exposure(image, film)
    return hd_density_curve(layer_exposure, film, chemistry)

