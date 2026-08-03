"""Coordinate-stable metallic-silver grain in RGB optical-density space.

Unlike dye-cloud grain, this component belongs to retained metallic silver. It
is therefore neutral, is never transformed by the dye absorption matrix, and
is applied only to an already derived RGB optical-density master.  The random
field is a pure function of global pixel coordinates and a dedicated seed, so
row tiling changes memory use without changing the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from half_frame_darkroom.core.halation import radius_to_sigma


@dataclass(frozen=True, slots=True)
class SilverGrainPlan:
    """Immutable execution plan for one full material frame."""

    full_shape: tuple[int, int]
    seed: int
    strength: float
    radius: float
    clump_mix: float
    tile_rows: int = 256

    @property
    def enabled(self) -> bool:
        return self.strength > 0.0 and self.radius > 0.0


def _mix_u32(values: np.ndarray) -> np.ndarray:
    """Vectorized 32-bit avalanche hash with intentional wraparound."""
    values = np.asarray(values, dtype=np.uint32)
    values ^= values >> np.uint32(16)
    values *= np.uint32(0x7FEB352D)
    values ^= values >> np.uint32(15)
    values *= np.uint32(0x846CA68B)
    values ^= values >> np.uint32(16)
    return values


def _coordinate_unit_noise(
    height: int,
    width: int,
    *,
    row_start: int,
    seed: int,
    stream: int,
) -> np.ndarray:
    """Return approximately unit-variance noise for absolute frame rows."""
    x = np.arange(width, dtype=np.uint32)[None, :]
    y = np.arange(row_start, row_start + height, dtype=np.uint32)[:, None]
    base = (
        x * np.uint32(0x9E3779B1)
        + y * np.uint32(0x85EBCA77)
        + np.uint32(seed & 0xFFFFFFFF)
        + np.uint32((stream * 0xC2B2AE3D) & 0xFFFFFFFF)
    )
    first = _mix_u32(base.copy())
    second = _mix_u32(base ^ np.uint32(0xA511E9B3))
    # Two centered uniforms have variance 1/6; sqrt(6) makes the sum unit
    # variance while avoiding a costly transcendental normal transform.
    scale = np.float32(np.sqrt(6.0) / float(2**32))
    noise = (first.astype(np.float32) + second.astype(np.float32)) * scale
    noise -= np.float32(np.sqrt(6.0))
    return noise.astype(np.float32, copy=False)


@lru_cache(maxsize=32)
def _gaussian_kernel(sigma_key: float) -> tuple[np.ndarray, int, float]:
    sigma = max(float(sigma_key), 0.35)
    radius = max(2, int(np.ceil(4.0 * sigma)))
    coordinates = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (coordinates / sigma) ** 2).astype(np.float32)
    kernel /= max(float(kernel.sum()), 1e-12)
    # For white unit noise and a separable 2-D kernel, output standard
    # deviation is sum(k**2). Compensating it keeps strength independent of
    # the chosen grain radius without per-tile statistical normalization.
    noise_gain = max(float(np.sum(kernel * kernel)), 1e-6)
    return kernel.reshape(-1, 1), radius, noise_gain


def _blurred_coordinate_noise(
    full_shape: tuple[int, int],
    *,
    output_start: int,
    output_stop: int,
    seed: int,
    stream: int,
    radius_scale: float,
) -> np.ndarray:
    full_height, width = full_shape
    sigma = radius_to_sigma(float(radius_scale), (full_height, width, 3))
    # Quantization bounds the cache without producing a meaningful visual
    # difference and guarantees the same kernel for all tiles in one frame.
    kernel, halo, noise_gain = _gaussian_kernel(round(float(sigma), 5))
    read_start = max(0, int(output_start) - halo)
    read_stop = min(full_height, int(output_stop) + halo)
    noise = _coordinate_unit_noise(
        read_stop - read_start,
        width,
        row_start=read_start,
        seed=seed,
        stream=stream,
    )
    blurred = cv2.sepFilter2D(
        noise,
        ddepth=cv2.CV_32F,
        kernelX=kernel,
        kernelY=kernel,
        borderType=cv2.BORDER_REFLECT_101,
    )
    crop_start = int(output_start) - read_start
    crop_stop = crop_start + (int(output_stop) - int(output_start))
    return (blurred[crop_start:crop_stop] / noise_gain).astype(np.float32)


def apply_metallic_silver_grain(
    optical_density_rgb: np.ndarray,
    silver_density: np.ndarray,
    plan: SilverGrainPlan,
    *,
    row_offset: int = 0,
) -> bool:
    """Apply neutral silver-density variation in place.

    ``silver_density`` is the scalar optical-density contribution of metallic
    silver before grain. It must describe the same rows as
    ``optical_density_rgb``. The function returns ``False`` without generating
    any random field when no silver is present.
    """
    optical = np.asarray(optical_density_rgb, dtype=np.float32)
    silver = np.asarray(silver_density, dtype=np.float32)
    if optical.ndim != 3 or optical.shape[-1] != 3:
        raise ValueError(f"optical_density_rgb must have shape (H, W, 3), got {optical.shape}")
    if silver.shape != optical.shape[:2]:
        raise ValueError(
            "silver_density must match optical-density rows, got "
            f"{silver.shape} and {optical.shape[:2]}"
        )
    full_height, full_width = plan.full_shape
    if optical.shape[1] != full_width or row_offset < 0 or row_offset + optical.shape[0] > full_height:
        raise ValueError("silver-grain tile lies outside the declared full frame")
    if not plan.enabled or silver.size == 0 or float(np.max(silver, initial=0.0)) <= 1e-8:
        return False

    clump_mix = float(np.clip(plan.clump_mix, 0.0, 1.0))
    fine_mix = float(np.sqrt(max(1.0 - clump_mix * clump_mix, 0.0)))
    tile_rows = max(1, int(plan.tile_rows))
    for local_start in range(0, optical.shape[0], tile_rows):
        local_stop = min(local_start + tile_rows, optical.shape[0])
        global_start = int(row_offset) + local_start
        global_stop = int(row_offset) + local_stop
        noise = _blurred_coordinate_noise(
            plan.full_shape,
            output_start=global_start,
            output_stop=global_stop,
            seed=int(plan.seed),
            stream=0,
            radius_scale=float(plan.radius),
        )
        if clump_mix > 1e-6:
            coarse = _blurred_coordinate_noise(
                plan.full_shape,
                output_start=global_start,
                output_stop=global_stop,
                seed=int(plan.seed),
                stream=1,
                radius_scale=float(plan.radius) * 3.2,
            )
            noise *= fine_mix
            noise += coarse * clump_mix
            del coarse

        local_silver = np.maximum(silver[local_start:local_stop], 0.0)
        # A relative density perturbation naturally vanishes with zero silver;
        # the soft denominator changes the response from linear near D=0
        # toward sqrt(D) through useful silver densities.
        amplitude = (
            float(plan.strength)
            * local_silver
            / np.sqrt(local_silver + np.float32(0.05))
        )
        delta = noise * amplitude
        np.maximum(delta, -0.90 * local_silver, out=delta)
        optical[local_start:local_stop] += delta[..., None]
        del noise, amplitude, delta
    return True
