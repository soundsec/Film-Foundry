"""Intentional darkroom accident effects.

These helpers model expert-mode accidents as part of negative formation. They
are deliberately bounded so playful controls can be pulled hard without making
the numeric pipeline unstable.
"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.model.config import DevelopRecipeConfig


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


def _resize_work_map(map_data: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    height, width = target_shape
    if map_data.shape == (height, width):
        return map_data.astype(np.float32)
    return cv2.resize(map_data, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32)


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

    side_weights = rng.uniform(0.35, 1.0, 4).astype(np.float32)
    edge = np.maximum.reduce(
        (
            side_weights[0] * np.power(1.0 - xx, 4.5),
            side_weights[1] * np.power(xx, 4.5),
            side_weights[2] * np.power(1.0 - yy, 4.5),
            side_weights[3] * np.power(yy, 4.5),
        )
    )
    corner = np.maximum.reduce(
        (
            np.power((1.0 - xx) * (1.0 - yy), 2.2),
            np.power(xx * (1.0 - yy), 2.2),
            np.power((1.0 - xx) * yy, 2.2),
            np.power(xx * yy, 2.2),
        )
    )
    blob_sigma = max(3.0, float(max(height, width)) * 0.18)
    blobs = _low_frequency_noise((height, width), rng, blob_sigma)
    leak_map = np.clip(edge * 0.72 + corner * 0.48 + blobs * 0.22, 0.0, 1.0)
    leak_map = cv2.GaussianBlur(leak_map, (0, 0), sigmaX=blob_sigma * 0.18, sigmaY=blob_sigma * 0.18)
    leak_map = np.clip(leak_map * strength, 0.0, 1.0).astype(np.float32)

    leak_color = np.asarray((1.0, 0.45, 0.18), dtype=np.float32)
    leaked = image_linear + leak_map[..., None] * leak_color[None, None, :] * 1.25
    return np.clip(leaked, 0.0, 4.0).astype(np.float32), leak_map


def apply_density_accidents(
    density_cmy: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Add dirty-chemistry stain and uneven development in density space."""
    state = build_effective_development(recipe)
    stain_strength = float(state.chemical_stain)
    uneven_strength = float(state.uneven_development)
    if stain_strength <= 1e-6 and uneven_strength <= 1e-6:
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
        stain_sigma = max(2.0, float(max(work_shape)) * 0.10)
        stain_map = _resize_work_map(_low_frequency_noise(work_shape, rng, stain_sigma), (height, width))
        stain_map = np.clip(0.45 + stain_map * 0.70, 0.0, 1.0).astype(np.float32)
        # CMY density bias: murky retained chemistry leans yellow/green-brown
        # after scanning, while still being stored as a physical negative stain.
        stain_bias = np.asarray((0.08, 0.18, 0.26), dtype=np.float32)
        result += stain_map[..., None] * stain_bias[None, None, :] * stain_strength
        maps["chemical_stain"] = stain_map

    if uneven_strength > 1e-6:
        uneven_sigma = max(2.0, float(max(work_shape)) * 0.16)
        uneven = _resize_work_map(_low_frequency_noise(work_shape, rng, uneven_sigma), (height, width))
        uneven = (uneven - 0.5) * 2.0
        result *= np.clip(1.0 + uneven[..., None] * 0.22 * uneven_strength, 0.55, 1.55)
        result += np.clip(uneven, 0.0, 1.0)[..., None] * 0.045 * uneven_strength
        maps["uneven_development"] = uneven.astype(np.float32)

    return np.clip(result, 0.0, None).astype(np.float32), maps
