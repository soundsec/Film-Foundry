"""Final-output anti-banding helpers."""

from __future__ import annotations

import cv2
import numpy as np

from film_foundry.core.color import luminance

# Output dither v2: lowbias32 (Chris Wellons, Unlicense), with project-owned
# coordinate composition. See THIRD_PARTY_NOTICES.md. This intentionally changes
# the dither realization, not its amplitude, mask, or the developed medium.


def _dither_mix(values: np.ndarray) -> np.ndarray:
    """lowbias32 on a private uint32 array; wraparound is intentional."""
    values = np.array(values, dtype=np.uint32, copy=True)
    values ^= values >> np.uint32(16)
    values *= np.uint32(0x7FEB352D)
    values ^= values >> np.uint32(15)
    values *= np.uint32(0x846CA68B)
    values ^= values >> np.uint32(16)
    return values


def _coordinate_fields(y: np.ndarray, x: np.ndarray) -> np.ndarray:
    # Mix axes separately before combining; absolute coordinates keep row tiles
    # independent from tile size, image content, and the application's RNG.
    base = _dither_mix(x) ^ _dither_mix(y ^ np.uint32(0xA511E9B3))
    fields = []
    for salt in (0x9E3779B9, 0x85EBCA6B, 0xC2B2AE35):
        value = _dither_mix(base ^ np.uint32(salt))
        fields.append(value.astype(np.float32) / np.float32(2**32 - 1) - 0.5)
    return np.stack(fields, axis=-1).astype(np.float32)


def _smooth_mask(image: np.ndarray) -> np.ndarray:
    gray = luminance(image)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    low, high = 0.004, 0.035
    t = np.clip((grad - low) / (high - low), 0.0, 1.0)
    smooth = 1.0 - (t * t * (3.0 - 2.0 * t))
    return cv2.GaussianBlur(smooth.astype(np.float32), (0, 0), sigmaX=1.0, sigmaY=1.0)


def _coordinate_noise(height: int, width: int) -> np.ndarray:
    y = np.arange(height, dtype=np.uint32)[:, None]
    x = np.arange(width, dtype=np.uint32)[None, :]
    noise = _coordinate_fields(y, x)
    # Average with a wrapped field shifted by three rows and five columns.
    return (noise + np.roll(noise, shift=(3, 5), axis=(0, 1))) * 0.5


def _coordinate_noise_region(
    y_start: int,
    y_end: int,
    width: int,
    full_height: int,
) -> np.ndarray:
    """Generate the same global deterministic field for a row interval."""
    y = np.arange(y_start, y_end, dtype=np.uint32).reshape(-1, 1)
    x = np.arange(width, dtype=np.uint32).reshape(1, -1)
    shifted_y = ((y.astype(np.int64) - 3) % full_height).astype(np.uint32)
    shifted_x = ((x.astype(np.int64) - 5) % width).astype(np.uint32)

    return (_coordinate_fields(y, x) + _coordinate_fields(shifted_y, shifted_x)) * 0.5


def apply_output_antibanding_region(
    image: np.ndarray,
    strength: float,
    y_start: int,
    y_end: int,
) -> np.ndarray:
    """Apply anti-banding to one row interval with full-frame semantics.

    The four-row halo covers the Sobel radius plus OpenCV's sigma=1 Gaussian
    kernel. Coordinate noise is evaluated in global coordinates, including
    the original whole-frame wrap used by ``np.roll``.
    """
    strength = float(np.clip(float(strength), 0.0, 1.0))
    image = np.asarray(image)
    height, width = image.shape[:2]
    y_start = max(0, int(y_start))
    y_end = min(height, int(y_end))
    if y_end <= y_start:
        return np.empty((0, width, 3), dtype=np.float32)
    block = np.clip(image[y_start:y_end], 0.0, 1.0).astype(np.float32)
    if strength <= 0.0 or height < 2 or width < 2:
        return block

    halo = 4
    source_start = max(0, y_start - halo)
    source_end = min(height, y_end + halo)
    source = np.clip(image[source_start:source_end], 0.0, 1.0).astype(np.float32)
    smooth = _smooth_mask(source)
    local_start = y_start - source_start
    mask = smooth[local_start : local_start + (y_end - y_start), ..., None]
    noise = _coordinate_noise_region(y_start, y_end, width, height)
    amplitude = strength * 1.35 / 255.0
    return np.clip(block + noise * mask * amplitude, 0.0, 1.0).astype(np.float32)


def apply_output_antibanding(image: np.ndarray, strength: float) -> np.ndarray:
    """Apply subtle deterministic dither before 8-bit quantization.

    The effect is strongest in low-texture regions such as skies and gradients.
    It does not attempt to reconstruct lost color information.
    """
    strength = float(np.clip(float(strength), 0.0, 1.0))
    if strength <= 0.0:
        return np.asarray(image, dtype=np.float32)
    image = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    if image.ndim != 3 or image.shape[-1] != 3:
        return image
    height, width = image.shape[:2]
    if height < 2 or width < 2:
        return image
    mask = _smooth_mask(image)[..., None]
    noise = _coordinate_noise(height, width)
    amplitude = strength * 1.35 / 255.0
    return np.clip(image + noise * mask * amplitude, 0.0, 1.0).astype(np.float32)
