"""Intentional darkroom accident effects.

These helpers model expert-mode accidents as part of negative formation. Dirty
chemistry and uneven development are modulated by EffectiveDevelopmentState, so
bad process conditions amplify the base accident tendency. They are deliberately
bounded so playful controls can be pulled hard without making the numeric
pipeline unstable.
"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.derived_cache import pseudoinverse_3x3

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.model.config import DevelopRecipeConfig, FilmStockConfig


def _low_frequency_noise(
    shape: tuple[int, int],
    rng: np.random.Generator,
    sigma: float,
) -> np.ndarray:
    height, width = shape
    noise = rng.normal(0.0, 1.0, (height, width)).astype(np.float32)
    noise = cv2.GaussianBlur(noise, (0, 0), sigmaX=sigma, sigmaY=sigma)
    noise -= float(np.min(noise))
    peak = float(np.max(noise))
    if peak <= 1e-6:
        return np.zeros((height, width), dtype=np.float32)
    return (noise / peak).astype(np.float32)


def _normalize01(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    values = values - float(np.min(values))
    peak = float(np.max(values))
    if peak <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return (values / peak).astype(np.float32)


def _soft_threshold(values: np.ndarray, edge0: float, edge1: float) -> np.ndarray:
    edge0_arr = np.asarray(edge0, dtype=np.float32)
    edge1_arr = np.asarray(edge1, dtype=np.float32)
    x = np.clip((values - edge0_arr) / np.maximum(edge1_arr - edge0_arr, 1e-6), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def _stain_deposit_map(
    shape: tuple[int, int],
    rng: np.random.Generator,
    sigma: float,
) -> np.ndarray:
    base = _low_frequency_noise(shape, rng, sigma)
    mid = _low_frequency_noise(shape, rng, max(1.5, sigma * 0.22))
    fine = _low_frequency_noise(shape, rng, max(0.8, sigma * 0.055))

    edge = cv2.Laplacian(base, cv2.CV_32F, ksize=3)
    edge = _normalize01(np.abs(edge))
    deposits = _soft_threshold(mid, 0.62, 0.88) * (0.65 + 0.35 * fine)
    voids = _soft_threshold(1.0 - mid, 0.72, 0.92) * 0.22
    ragged = 0.82 + 0.30 * (mid - 0.5) + 0.16 * (fine - 0.5)

    stain = base * ragged + deposits * 0.30 + edge * 0.12 - voids
    stain = cv2.GaussianBlur(stain, (0, 0), sigmaX=max(0.45, sigma * 0.012), sigmaY=max(0.45, sigma * 0.012))
    return _normalize01(stain)


def _axis_streaks(
    shape: tuple[int, int],
    rng: np.random.Generator,
    *,
    along_y: bool,
    long_sigma: float,
    short_sigma: float,
) -> np.ndarray:
    noise = rng.normal(0.0, 1.0, shape).astype(np.float32)
    sigma_x = short_sigma if along_y else long_sigma
    sigma_y = long_sigma if along_y else short_sigma
    streaks = cv2.GaussianBlur(noise, (0, 0), sigmaX=sigma_x, sigmaY=sigma_y)
    return (_normalize01(streaks) - 0.5) * 2.0


def _uneven_development_map(
    shape: tuple[int, int],
    rng: np.random.Generator,
    sigma: float,
    agitation_deficit: float,
) -> np.ndarray:
    base = _low_frequency_noise(shape, rng, sigma)
    base = (base - 0.5) * 2.0

    long_sigma = max(4.0, sigma * (0.70 + 0.45 * agitation_deficit))
    short_sigma = max(0.8, sigma * 0.035)
    vertical = _axis_streaks(shape, rng, along_y=True, long_sigma=long_sigma, short_sigma=short_sigma)
    horizontal = _axis_streaks(shape, rng, along_y=False, long_sigma=long_sigma, short_sigma=short_sigma)
    mix = float(rng.uniform(0.20, 0.80))
    streaks = vertical * mix + horizontal * (1.0 - mix)

    mottles = _stain_deposit_map(shape, rng, max(2.0, sigma * 0.42))
    mottles = (mottles - 0.5) * 2.0

    uneven = base * 0.68 + streaks * (0.18 + 0.22 * agitation_deficit) + mottles * 0.16
    return np.clip(uneven, -1.0, 1.0).astype(np.float32)


def _edge_leak_mask(
    xx: np.ndarray,
    yy: np.ndarray,
    rng: np.random.Generator,
    side_weights: np.ndarray,
) -> np.ndarray:
    height, width = xx.shape
    shape = (height, width)
    softness = 0.035 + 0.055 * _low_frequency_noise(shape, rng, max(2.0, min(height, width) * 0.035))
    rough = _stain_deposit_map(shape, rng, max(2.0, min(height, width) * 0.10))

    left_width = float(rng.uniform(0.025, 0.18))
    right_width = float(rng.uniform(0.025, 0.18))
    top_width = float(rng.uniform(0.020, 0.14))
    bottom_width = float(rng.uniform(0.020, 0.14))
    left = _soft_threshold(left_width * (0.70 + 0.75 * rough) - xx, -softness, softness) * side_weights[0]
    right = _soft_threshold(right_width * (0.70 + 0.75 * rough) - (1.0 - xx), -softness, softness) * side_weights[1]
    top = _soft_threshold(top_width * (0.70 + 0.75 * rough) - yy, -softness, softness) * side_weights[2]
    bottom = _soft_threshold(bottom_width * (0.70 + 0.75 * rough) - (1.0 - yy), -softness, softness) * side_weights[3]

    vertical_channel = _axis_streaks(shape, rng, along_y=True, long_sigma=max(5.0, height * 0.22), short_sigma=max(0.8, width * 0.006))
    horizontal_channel = _axis_streaks(shape, rng, along_y=False, long_sigma=max(5.0, width * 0.22), short_sigma=max(0.8, height * 0.006))
    channel = np.clip(0.5 + 0.24 * vertical_channel + 0.24 * horizontal_channel, 0.0, 1.0)
    edge = np.maximum.reduce((left, right, top, bottom))
    return np.clip(edge * (0.72 + 0.55 * channel), 0.0, 1.0).astype(np.float32)


def _resize_work_map(map_data: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    height, width = target_shape
    if map_data.shape == (height, width):
        return map_data.astype(np.float32)
    return cv2.resize(map_data, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32)


def _bounded_pathology(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def apply_uneven_development_to_latent_proxy(
    image_linear: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Apply local developer-activity variation before pool conversion.

    Uneven development is not a stain laid over the finished film. In this
    reduced model a spatial activity field becomes a bounded exposure-equivalent
    shift before latent state is consumed, so negative and reversal programs
    both carry the defect through their actual silver/dye transitions.
    """
    state = build_effective_development(recipe)
    strength = float(state.uneven_development)
    if strength <= 1e-6:
        return np.asarray(image_linear, dtype=np.float32), None

    agitation_deficit = _bounded_pathology(state.agitation_deficit)
    underdevelopment = _bounded_pathology(state.underdevelopment)
    exhaustion = _bounded_pathology(state.exhaustion)
    concentration_stress = _bounded_pathology(state.concentration_stress)
    temperature_stress = _bounded_pathology(state.temperature_stress)
    pathology = (
        (1.0 + 0.70 * agitation_deficit)
        * (1.0 + 0.35 * underdevelopment)
        * (1.0 + 0.30 * exhaustion)
        * (1.0 + 0.22 * concentration_stress)
        * (1.0 + 0.22 * temperature_stress)
    )
    effective_strength = float(np.clip(strength * pathology, 0.0, 1.0))

    image_linear = np.asarray(image_linear, dtype=np.float32)
    height, width = image_linear.shape[:2]
    if work_long_edge is None and fast:
        work_long_edge = 1200
    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
    else:
        work_shape = (height, width)

    rng = rng or np.random.default_rng()
    uneven_sigma = max(2.0, float(max(work_shape)) * 0.16 * (1.0 + 0.24 * agitation_deficit))
    uneven = _resize_work_map(
        _uneven_development_map(work_shape, rng, uneven_sigma, agitation_deficit),
        (height, width),
    )
    # ±0.45 stop is the severe-control limit. This is a local activity proxy,
    # not a claim that developer concentration literally changes exposure.
    local_factor = np.exp2(uneven * (0.45 * effective_strength)).astype(np.float32)
    varied = image_linear * local_factor[..., None]
    return np.clip(varied, 0.0, 4.0).astype(np.float32), uneven.astype(np.float32)


def apply_light_leak_to_exposure(
    image_linear: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Add light-leak exposure before H-D density formation."""
    state = build_effective_development(recipe)
    strength = float(state.light_leak_strength)
    if strength <= 1e-6:
        return np.asarray(image_linear, dtype=np.float32), None

    rng = rng or np.random.default_rng()
    image_linear = np.asarray(image_linear, dtype=np.float32)
    height, width = image_linear.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    if width > 1:
        xx /= float(width - 1)
    if height > 1:
        yy /= float(height - 1)

    # A real leak normally has a local entry path. Lighting all four edges at
    # once creates a synthetic halo frame, so keep one dominant edge and only
    # occasionally a neighbouring/secondary entry.
    side_weights = np.zeros(4, dtype=np.float32)
    active_count = 1 if float(rng.random()) < 0.76 else 2
    active_sides = rng.choice(4, size=active_count, replace=False)
    side_weights[active_sides] = rng.uniform(0.55, 1.0, active_count).astype(np.float32)
    smooth_edge = np.maximum.reduce(
        (
            side_weights[0] * np.power(1.0 - xx, 4.5),
            side_weights[1] * np.power(xx, 4.5),
            side_weights[2] * np.power(1.0 - yy, 4.5),
            side_weights[3] * np.power(yy, 4.5),
        )
    )
    corner = np.maximum.reduce(
        (
            max(side_weights[0], side_weights[2]) * np.power((1.0 - xx) * (1.0 - yy), 2.2),
            max(side_weights[1], side_weights[2]) * np.power(xx * (1.0 - yy), 2.2),
            max(side_weights[0], side_weights[3]) * np.power((1.0 - xx) * yy, 2.2),
            max(side_weights[1], side_weights[3]) * np.power(xx * yy, 2.2),
        )
    )
    blob_sigma = max(3.0, float(max(height, width)) * 0.18)
    blobs = _low_frequency_noise((height, width), rng, blob_sigma)
    ragged_edge = _edge_leak_mask(xx, yy, rng, side_weights)
    leak_map = np.clip(
        np.maximum(smooth_edge * 0.48, ragged_edge * 0.86) + corner * 0.36 + blobs * 0.18,
        0.0,
        1.0,
    )
    leak_map = cv2.GaussianBlur(leak_map, (0, 0), sigmaX=blob_sigma * 0.045, sigmaY=blob_sigma * 0.045)
    leak_map = np.clip(leak_map * strength, 0.0, 1.0).astype(np.float32)

    # Base-side entry tends toward red/orange; emulsion-side entry can be much
    # less red. Randomly interpolate those reduced spectral tendencies instead
    # of claiming that every leak has the same colour.
    base_side_mix = float(rng.uniform(0.30, 1.0))
    leak_color = (
        np.asarray((1.0, 0.92, 0.78), dtype=np.float32) * (1.0 - base_side_mix)
        + np.asarray((1.0, 0.42, 0.15), dtype=np.float32) * base_side_mix
    )
    color_variation = _low_frequency_noise((height, width), rng, max(2.0, float(max(height, width)) * 0.12))
    warm = 0.85 + 0.35 * color_variation
    leak_color_map = np.stack(
        (
            leak_color[0] * warm,
            leak_color[1] * (0.92 + 0.16 * (1.0 - color_variation)),
            leak_color[2] * (0.78 + 0.22 * color_variation),
        ),
        axis=-1,
    ).astype(np.float32)
    leaked = image_linear + leak_map[..., None] * leak_color_map * 1.25
    return np.clip(leaked, 0.0, 4.0).astype(np.float32), leak_map


def apply_density_accidents(
    density_cmy: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    film: FilmStockConfig | None = None,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Add post-formation stain and surface-silver deposits in density space."""
    state = build_effective_development(recipe)
    underdevelopment = _bounded_pathology(state.underdevelopment)
    agitation_deficit = _bounded_pathology(state.agitation_deficit)
    clearing_failure = _bounded_pathology(state.clearing_failure)
    residue_factor = _bounded_pathology(state.residue_factor / 3.0)

    stain_pathology = (
        (1.0 + 0.35 * underdevelopment)
        * (1.0 + 0.40 * clearing_failure)
        * (1.0 + 0.35 * residue_factor)
        * (1.0 + 0.20 * agitation_deficit)
    )
    stain_strength = float(np.clip(state.chemical_stain * stain_pathology, 0.0, 1.0))
    plating_strength = float(np.clip(state.silvering_factor, 0.0, 1.5))
    if stain_strength <= 1e-6 and plating_strength <= 1e-6:
        return np.asarray(density_cmy, dtype=np.float32), {}

    rng = rng or np.random.default_rng()
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    height, width = density_cmy.shape[:2]
    if work_long_edge is None and fast:
        work_long_edge = 1200
    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
    else:
        work_shape = (height, width)

    maps: dict[str, np.ndarray] = {}
    result = density_cmy.copy()

    if stain_strength > 1e-6:
        stain_sigma = max(2.0, float(max(work_shape)) * 0.10 * (1.0 + 0.18 * residue_factor))
        stain_map = _resize_work_map(_stain_deposit_map(work_shape, rng, stain_sigma), (height, width))
        stain_map = np.clip(0.34 + stain_map * 0.86, 0.0, 1.0).astype(np.float32)
        # CMY density bias: murky retained chemistry leans yellow/green-brown
        # after scanning, while still being stored as a physical negative stain.
        stain_bias = np.asarray((0.08, 0.18, 0.26), dtype=np.float32)
        result += stain_map[..., None] * stain_bias[None, None, :] * stain_strength
        maps["chemical_stain"] = stain_map

    if plating_strength > 1e-6:
        deposit_sigma = max(2.0, float(max(work_shape)) * 0.075)
        deposit = _stain_deposit_map(work_shape, rng, deposit_sigma)
        along_y = bool(rng.integers(0, 2))
        streaks = _axis_streaks(
            work_shape,
            rng,
            along_y=along_y,
            long_sigma=max(5.0, float(max(work_shape)) * 0.20),
            short_sigma=max(0.8, float(min(work_shape)) * 0.006),
        )
        plating_map = np.clip(0.12 + 0.72 * deposit + 0.22 * np.maximum(streaks, 0.0), 0.0, 1.0)
        plating_map = _resize_work_map(plating_map.astype(np.float32), (height, width))
        # Deposited metallic silver is a broad-band RGB optical-density
        # component. On color material, equal layer density is not necessarily
        # optically neutral after the dye-absorption matrix, so solve the layer
        # proxy that observes as equal RGB density. Monochrome/legacy callers
        # can keep the equal-channel fallback.
        plating_density_rgb = np.repeat(
            (plating_map * (0.20 * plating_strength))[..., None],
            3,
            axis=-1,
        ).astype(np.float32)
        if film is not None:
            inverse = pseudoinverse_3x3(film.dye_absorption_matrix)
            plating_layers = np.einsum("...r,lr->...l", plating_density_rgb, inverse)
            result += np.clip(plating_layers, 0.0, None).astype(np.float32)
        else:
            result += plating_density_rgb
        maps["silver_plating"] = plating_map.astype(np.float32)

    return np.clip(result, 0.0, None).astype(np.float32), maps
