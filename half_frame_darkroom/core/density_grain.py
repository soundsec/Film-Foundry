"""遵循密度统计关系的随机颗粒核心。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


DEFAULT_DENSITY_GRAIN_RADIUS = 0.0014
LAYER_SHARED_GRAIN_MIX = 0.32


def _grain_scales_for_film(film: FilmStockConfig, radius_factor: float) -> tuple[np.ndarray, np.ndarray]:
    scales = np.asarray(getattr(film, "grain_scales", ()), dtype=np.float32)
    weights = np.asarray(getattr(film, "grain_scale_weights", ()), dtype=np.float32)
    if scales.size == 0 or weights.size == 0:
        scales = np.asarray((film.grain_density_correlation_radius,), dtype=np.float32)
        weights = np.asarray((1.0,), dtype=np.float32)
    count = int(min(scales.size, weights.size))
    scales = scales[:count]
    weights = weights[:count]
    valid = (scales > 0.0) & (weights > 0.0)
    if not bool(np.any(valid)):
        scales = np.asarray((film.grain_density_correlation_radius,), dtype=np.float32)
        weights = np.asarray((1.0,), dtype=np.float32)
    else:
        scales = scales[valid]
        weights = weights[valid]

    radius_gain = float(film.grain_density_correlation_radius) / DEFAULT_DENSITY_GRAIN_RADIUS
    scales = np.clip(scales * radius_gain * float(radius_factor), 1e-5, 0.08).astype(np.float32)
    weights = weights / max(float(np.sqrt(np.sum(weights * weights))), 1e-6)
    return scales, weights.astype(np.float32)


def _smoothstep(values: np.ndarray, edge0: float, edge1: float) -> np.ndarray:
    x = np.clip((values - float(edge0)) / max(float(edge1) - float(edge0), 1e-6), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _normalize_unit_field(field: np.ndarray) -> np.ndarray:
    field = np.asarray(field, dtype=np.float32)
    field = field - float(np.mean(field))
    return (field / max(float(np.std(field)), 1e-6)).astype(np.float32)


def _subpixel_shift(field: np.ndarray, dx: float, dy: float) -> np.ndarray:
    matrix = np.asarray(((1.0, 0.0, float(dx)), (0.0, 1.0, float(dy))), dtype=np.float32)
    return cv2.warpAffine(
        field.astype(np.float32),
        matrix,
        (field.shape[1], field.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    ).astype(np.float32)


def _blurred_multilayer_field(
    rng: np.random.Generator,
    work_shape: tuple[int, int],
    scale: float,
    shared_mix: float = LAYER_SHARED_GRAIN_MIX,
) -> np.ndarray:
    blur_sigma = radius_to_sigma(float(scale), (*work_shape, 3))
    max_shift = float(np.clip(blur_sigma * 0.42, 0.35, 2.25))

    shared = rng.normal(0.0, 1.0, work_shape).astype(np.float32)
    shared = cv2.GaussianBlur(shared, (0, 0), sigmaX=blur_sigma, sigmaY=blur_sigma)
    shared = _normalize_unit_field(shared)

    shared_channels = np.empty((*work_shape, 3), dtype=np.float32)
    layer_channels = np.empty((*work_shape, 3), dtype=np.float32)
    for channel in range(3):
        dx, dy = rng.uniform(-max_shift, max_shift, 2)
        shared_channels[..., channel] = _subpixel_shift(shared, dx=float(dx), dy=float(dy))

        layer = rng.normal(0.0, 1.0, work_shape).astype(np.float32)
        layer = cv2.GaussianBlur(layer, (0, 0), sigmaX=blur_sigma, sigmaY=blur_sigma)
        dx, dy = rng.uniform(-max_shift, max_shift, 2)
        layer_channels[..., channel] = _subpixel_shift(_normalize_unit_field(layer), dx=float(dx), dy=float(dy))

    shared_mix = float(np.clip(shared_mix, 0.0, 1.0))
    layer_mix = float(np.sqrt(max(1.0 - shared_mix * shared_mix, 0.0)))
    return _normalize_unit_field(shared_channels * shared_mix + layer_channels * layer_mix)


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
    d_max = np.asarray(film.density_max, dtype=np.float32)
    degradation = float(np.clip(getattr(film, "material_degradation", 0.0), 0.0, 1.0))
    sigma_base = np.asarray(film.granularity_sigma, dtype=np.float32) * (1.0 + 0.35 * degradation)
    density_norm = np.clip((small_density - d_min) / np.maximum(d_max - d_min, 1e-6), 0.0, 1.0)
    density_level = np.mean(density_norm, axis=-1)

    sigma = sigma_base * development.grain_factor * np.sqrt(np.clip(small_density - d_min, 0.0, None))
    unit_noise = np.zeros_like(small_density, dtype=np.float32)
    grain_scales, grain_weights = _grain_scales_for_film(film, development.grain_radius_factor)
    for scale, weight in zip(grain_scales, grain_weights, strict=False):
        field = _blurred_multilayer_field(rng, work_shape, float(scale))
        unit_noise += field * float(weight)
    noise = unit_noise * sigma

    shadow_weight = 1.0 - _smoothstep(density_level, 0.10, 0.46)
    if float(np.max(shadow_weight)) > 1e-6:
        shadow_noise = np.zeros_like(small_density, dtype=np.float32)
        coarse_scales = np.clip(grain_scales * 2.8, 1e-5, 0.10)
        coarse_weights = grain_weights / max(float(np.sqrt(np.sum(grain_weights * grain_weights))), 1e-6)
        for scale, weight in zip(coarse_scales, coarse_weights, strict=False):
            field = _blurred_multilayer_field(rng, work_shape, float(scale), shared_mix=0.46)
            shadow_noise += field * float(weight)
        shadow_chroma = np.asarray((1.18, 0.92, 1.10), dtype=np.float32).reshape(1, 1, 3)
        shadow_sigma = (
            sigma_base.reshape(1, 1, 3)
            * float(development.grain_factor)
            * shadow_chroma
            * shadow_weight[..., None]
            * 0.42
        )
        noise = noise + shadow_noise * shadow_sigma

    if development.residue_factor > 1e-6:
        residue_sigma = radius_to_sigma(0.010 * development.grain_radius_factor, (*work_shape, 3))
        residue = rng.normal(0.0, 1.0, small_density.shape[:2]).astype(np.float32)
        residue = cv2.GaussianBlur(residue, (0, 0), sigmaX=residue_sigma, sigmaY=residue_sigma)
        residue = residue - float(np.min(residue))
        residue = residue / max(float(np.max(residue)), 1e-6)
        residue = (residue - 0.35).clip(0.0, 1.0)
        residue_strength = 0.018 * float(development.residue_factor)
        residue_density = residue[..., None] * residue_strength
        noise = noise + residue_density.astype(np.float32)

    if work_shape != (height, width):
        noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)

    return np.clip(density_cmy + noise, 0.0, None).astype(np.float32)
