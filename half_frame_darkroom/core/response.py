"""简化的胶片化学响应。"""

from __future__ import annotations

# Legacy RGB film-response helper.
#
# The current electronic-negative pipeline uses
# sensitometry.exposure_to_density() to form CMY density. This module is kept
# for old tests and experiments.

import numpy as np

from half_frame_darkroom.core.color import apply_color_matrix, luminance
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def _tone_curve(x: np.ndarray, contrast: float, toe: float, shoulder: float, shoulder_point: float) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    x = np.clip(x, 0.0, 8.0)

    mid = 0.18
    contrasted = mid + (x - mid) * contrast
    contrasted = np.clip(contrasted, 0.0, 8.0)

    toe_strength = max(0.0, toe)
    toe_curve = contrasted / (contrasted + toe_strength * (1.0 - contrasted) + 1e-6)
    toe_blend = np.exp(-contrasted * 7.0)
    shaped = contrasted * (1.0 - toe_blend) + toe_curve * toe_blend

    shoulder_strength = max(1e-4, shoulder)
    shoulder_point = np.clip(shoulder_point, 0.35, 0.98)
    excess = np.maximum(shaped - shoulder_point, 0.0)
    compressed = shoulder_point + excess / (1.0 + shoulder_strength * excess * 4.0)
    return np.where(shaped > shoulder_point, compressed, shaped).astype(np.float32)


def _apply_saturation(image: np.ndarray, saturation: float) -> np.ndarray:
    y = luminance(image)[..., None]
    return (y + (image - y) * saturation).astype(np.float32)


def apply_film_response(
    image: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
) -> np.ndarray:
    """应用色彩串扰、片基灰雾、迫冲/药水疲劳和胶片响应曲线。"""
    image = np.asarray(image, dtype=np.float32)
    push = float(chemistry.push_stops)
    exhaustion = float(np.clip(chemistry.developer_exhaustion, 0.0, 1.0))

    x = apply_color_matrix(image, film.color_matrix)
    x = np.clip(x, 0.0, None)

    exposure_shift = 2.0 ** (0.12 * push - 0.10 * exhaustion)
    x = x * exposure_shift

    fog = film.base_fog + max(push, 0.0) * 0.010 + exhaustion * 0.035
    x = x + fog

    contrast = film.contrast * (1.0 + 0.10 * push) * (1.0 - 0.18 * exhaustion)
    toe = film.toe_strength * (1.0 - 0.10 * push) + exhaustion * 0.08
    shoulder = film.shoulder_strength * (1.0 + 0.08 * push + 0.35 * exhaustion)
    shoulder_point = film.shoulder_point - 0.025 * push - 0.060 * exhaustion

    x = _tone_curve(x, contrast=contrast, toe=toe, shoulder=shoulder, shoulder_point=shoulder_point)

    saturation = film.saturation * (1.0 - 0.05 * exhaustion + 0.015 * push)
    x = _apply_saturation(x, saturation=saturation)
    return np.clip(x, 0.0, 1.0).astype(np.float32)
