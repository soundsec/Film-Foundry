"""高光触发的光学扩散与 halation。"""

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from half_frame_darkroom.core.color import luminance
from half_frame_darkroom.core.spatial_fields import (
    GlobalFieldGrid,
    SpatialFieldPlan,
    global_field_grid,
    resample_global_scalar_field,
)
from half_frame_darkroom.model.config import FilmStockConfig


PARAMETERIZED_WARM_HALATION_PLAN = SpatialFieldPlan(
    key="parameterized_warm_halation",
    stage="pre_latent_exposure",
    quantity="exposure_addition",
    field_kind="global_scalar",
    requires_global_source=True,
    requires_tile_halo=True,
    random_field_policy="none",
    persistent_state=False,
    disabled_identity_guarantee=True,
)


def radius_to_sigma(radius_scale: float, image_shape: tuple[int, int, int]) -> float:
    """把相对图像尺寸的半径转换为 Gaussian sigma。"""
    height, width = image_shape[:2]
    reference = max(1, min(height, width))
    radius_px = max(0.5, float(radius_scale) * reference)
    return max(0.35, radius_px / 2.0)


def _smoothstep_unit_in_place(values: np.ndarray) -> np.ndarray:
    """Consume a private clipped unit field and return its smoothstep."""
    curve = np.multiply(values, values, dtype=np.float32)
    values *= -2.0
    values += 3.0
    curve *= values
    return curve


def soft_threshold(values: np.ndarray, threshold: float, softness: float) -> np.ndarray:
    """生成软阈值遮罩，避免硬切边。"""
    softness = max(float(softness), 1e-6)
    edge0 = threshold - softness
    edge1 = threshold + softness
    source = np.asarray(values)
    if source.dtype != np.float32:
        # Preserve the historical higher-precision intermediate behaviour for
        # standalone callers. Formation fields are FP32 and use the bounded
        # private-buffer path below.
        x = np.clip((source - edge0) / (edge1 - edge0), 0.0, 1.0)
        return (x * x * (3.0 - 2.0 * x)).astype(np.float32)
    x = np.subtract(source, edge0, dtype=np.float32)
    x /= edge1 - edge0
    np.clip(x, 0.0, 1.0, out=x)
    return _smoothstep_unit_in_place(x)


def _resize_mask(mask: np.ndarray, long_edge: int) -> np.ndarray:
    """Compatibility wrapper for the frame-global scalar-grid contract."""
    grid = global_field_grid(mask.shape, long_edge)
    return resample_global_scalar_field(mask, grid, to_work_grid=True)


@lru_cache(maxsize=64)
def _cached_halation_psf_kernel(
    height: int,
    width: int,
    core_radius: float,
    exponential_radius: float,
    gaussian_amplitude: float,
    exponential_amplitude: float,
) -> np.ndarray:
    image_shape = (int(height), int(width), 3)
    sigma = radius_to_sigma(core_radius, image_shape)
    radius_r = max(1.0, float(exponential_radius) * min(image_shape[:2]))
    max_radius = int(np.ceil(max(sigma * 4.0, radius_r * 6.0)))
    max_radius = max(3, min(max_radius, 256))

    coords = np.arange(-max_radius, max_radius + 1, dtype=np.float32)
    yy, xx = np.meshgrid(coords, coords, indexing="ij")
    r = np.sqrt(xx * xx + yy * yy)

    gaussian = float(gaussian_amplitude) * np.exp(-(r * r) / (2.0 * sigma * sigma))
    exponential = float(exponential_amplitude) * np.exp(-r / max(radius_r, 1e-6))
    kernel = gaussian + exponential
    kernel /= max(float(kernel.sum()), 1e-6)
    return kernel.astype(np.float32)


def halation_psf_kernel(image_shape: tuple[int, int, int], film: FilmStockConfig) -> np.ndarray:
    """Generate and cache the immutable halation PSF for a material/work size."""
    return _cached_halation_psf_kernel(
        int(image_shape[0]),
        int(image_shape[1]),
        float(film.halation_core_radius),
        float(film.halation_exponential_radius),
        float(film.halation_gaussian_amplitude),
        float(film.halation_exponential_amplitude),
    )


def _gradient_magnitude(values: np.ndarray) -> np.ndarray:
    """Return the Sobel magnitude while reusing one derivative buffer."""
    grad_x = cv2.Sobel(values, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(values, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y, grad_x)
    del grad_y
    return gradient


def _gradient_mask(
    values: np.ndarray,
    *,
    global_scale: float | None = None,
) -> np.ndarray:
    """估计极陡边缘区域，用于降低阶跃边缘对光晕的异常激发。"""
    gradient = _gradient_magnitude(values)
    scale = (
        float(np.percentile(gradient, 96.0))
        if global_scale is None
        else float(global_scale)
    )
    if scale <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    gradient /= scale
    np.clip(gradient, 0.0, 1.0, out=gradient)
    gradient -= 0.18
    gradient /= 0.42
    np.clip(gradient, 0.0, 1.0, out=gradient)
    return _smoothstep_unit_in_place(gradient)


def _halation_source_luminance(
    image: np.ndarray,
    film: FilmStockConfig,
    *,
    reference_shape: tuple[int, int, int] | None = None,
    gradient_scale: float | None = None,
) -> np.ndarray:
    """在触发光晕前加入乳剂层微散射和陡梯度补偿。"""
    shape = image.shape if reference_shape is None else reference_shape
    y = luminance(image)
    sigma = radius_to_sigma(film.halation_source_blur_radius, shape)
    scattered = cv2.GaussianBlur(y, (0, 0), sigmaX=sigma, sigmaY=sigma)
    edge_mask = _gradient_mask(y, global_scale=gradient_scale)
    edge_mask *= float(np.clip(film.halation_gradient_suppression, 0.0, 1.0))
    # Preserve the established blend order while reusing its two disposable
    # buffers. At large frame sizes the previous expression temporarily held
    # both products plus a third output scalar field.
    scattered *= edge_mask
    np.subtract(1.0, edge_mask, out=edge_mask)
    edge_mask *= y
    scattered += edge_mask
    return scattered.astype(np.float32, copy=False)


def _local_peak_mask(values: np.ndarray, film: FilmStockConfig, image_shape: tuple[int, int, int]) -> np.ndarray:
    """Estimate local highlight peaks and suppress broad bright matte surfaces."""
    sigma = radius_to_sigma(film.halation_peak_radius, image_shape)
    local_base = cv2.GaussianBlur(values, (0, 0), sigmaX=sigma, sigmaY=sigma)
    local_peak = np.subtract(values, local_base, dtype=np.float32)
    np.maximum(local_peak, 0.0, out=local_peak)
    local_base += 0.05
    np.divide(local_peak, local_base, out=local_peak)
    return soft_threshold(
        local_peak,
        float(film.halation_peak_threshold),
        float(film.halation_peak_softness),
    )


def _large_bright_area_weight(highlight_mask: np.ndarray, film: FilmStockConfig, image_shape: tuple[int, int, int]) -> np.ndarray:
    """Down-weight large bright regions such as sky, white walls, or white cups."""
    suppression = float(np.clip(film.halation_area_suppression, 0.0, 1.0))
    if suppression <= 0.0:
        return np.ones_like(highlight_mask, dtype=np.float32)

    sigma = radius_to_sigma(film.halation_area_radius, image_shape)
    local_area = cv2.GaussianBlur(highlight_mask, (0, 0), sigmaX=sigma, sigmaY=sigma)
    area_mask = soft_threshold(
        local_area,
        float(film.halation_area_threshold),
        0.25,
    )
    area_mask *= -suppression
    area_mask += 1.0
    np.clip(area_mask, 0.0, 1.0, out=area_mask)
    return area_mask.astype(np.float32, copy=False)


def _halation_source_energy(
    image: np.ndarray,
    film: FilmStockConfig,
    *,
    reference_shape: tuple[int, int, int],
    gradient_scale: float | None,
) -> np.ndarray:
    y = _halation_source_luminance(
        image,
        film,
        reference_shape=reference_shape,
        gradient_scale=gradient_scale,
    )
    highlight_mask = soft_threshold(y, film.halation_threshold, film.halation_softness)
    peak_mask = _local_peak_mask(y, film, reference_shape)
    area_weight = _large_bright_area_weight(highlight_mask, film, reference_shape)
    # Keep the legacy multiplication order, but consume disposable masks in
    # place and reuse luminance as the final excess-energy buffer.
    highlight_mask *= peak_mask
    del peak_mask
    highlight_mask *= area_weight
    del area_weight
    y -= float(film.halation_threshold)
    np.maximum(y, 0.0, out=y)
    highlight_mask *= y
    return highlight_mask.astype(np.float32, copy=False)


def halation_source_energy(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """Build the leaked highlight energy source for halation."""
    return _halation_source_energy(
        image,
        film,
        reference_shape=image.shape,
        gradient_scale=None,
    )


def _halation_source_halo_radius(
    film: FilmStockConfig,
    image_shape: tuple[int, int, int],
) -> int:
    """Finite overlap that contains the chained Gaussian source operations."""
    source_sigma = radius_to_sigma(film.halation_source_blur_radius, image_shape)
    neighbourhood_sigma = max(
        radius_to_sigma(film.halation_peak_radius, image_shape),
        radius_to_sigma(film.halation_area_radius, image_shape),
    )
    return max(2, int(np.ceil(4.0 * source_sigma + 4.0 * neighbourhood_sigma)) + 2)


def _halation_global_gradient_scale_tiled(
    image: np.ndarray,
    tile_rows: int,
) -> float:
    """Measure the same full-frame gradient percentile using bounded stripes."""
    height, width = image.shape[:2]
    gradient = np.empty((height, width), dtype=np.float32)
    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        read_start = max(0, start - 1)
        read_stop = min(height, stop + 1)
        y = luminance(image[read_start:read_stop])
        tile_gradient = _gradient_magnitude(y)
        crop_start = start - read_start
        gradient[start:stop] = tile_gradient[
            crop_start : crop_start + (stop - start)
        ]
        del y, tile_gradient
    scale = float(np.percentile(gradient, 96.0))
    del gradient
    return scale


def halation_source_energy_tiled(
    image: np.ndarray,
    film: FilmStockConfig,
    *,
    tile_rows: int,
) -> np.ndarray:
    """Build one global source with frozen statistics and finite row halos.

    The first pass freezes the frame-global gradient percentile. The second
    pass evaluates exactly the same source formula over overlapping stripes.
    Tiles never estimate their own normalization and only their central rows
    are committed.
    """
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or image.size == 0:
        raise ValueError("halation image must be a non-empty HxWx3 array")
    rows = int(tile_rows)
    if rows <= 0:
        raise ValueError("halation source tile_rows must be positive")
    height, width = image.shape[:2]
    rows = min(rows, height)
    gradient_scale = _halation_global_gradient_scale_tiled(image, rows)
    source = np.empty((height, width), dtype=np.float32)
    halo = _halation_source_halo_radius(film, image.shape)
    for start in range(0, height, rows):
        stop = min(start + rows, height)
        read_start = max(0, start - halo)
        read_stop = min(height, stop + halo)
        tile_source = _halation_source_energy(
            image[read_start:read_stop],
            film,
            reference_shape=image.shape,
            gradient_scale=gradient_scale,
        )
        crop_start = start - read_start
        source[start:stop] = tile_source[
            crop_start : crop_start + (stop - start)
        ]
        del tile_source
    return source


def _automatic_halation_source_tile_rows(
    image_shape: tuple[int, int, int],
    work_long_edge: int | None,
) -> int | None:
    """Use bounded source stripes only for genuinely reduced large frames."""
    grid = global_field_grid(image_shape, work_long_edge)
    height, width = image_shape[:2]
    if not grid.reduced or height * width < 8_000_000:
        return None
    # A wider stripe amortizes the deliberately generous source halo.  Roughly
    # four megapixels per centre region kept more than half of the traced-memory
    # saving near the 8 MP activation threshold while avoiding the severe
    # repeated-blur cost of 2 MP / 256-row stripes.
    rows_for_four_megapixels = max(1, int(4_000_000 / max(width, 1)))
    return max(1, min(512, rows_for_four_megapixels, height))


def prepare_halation_work_source(
    source: np.ndarray,
    *,
    work_long_edge: int | None,
) -> tuple[GlobalFieldGrid, np.ndarray]:
    """Map a global source to its declared work grid before releasing it."""
    grid = global_field_grid(source.shape, work_long_edge)
    return grid, resample_global_scalar_field(source, grid, to_work_grid=True)


def spread_halation_work_source(
    work_source: np.ndarray,
    film: FilmStockConfig,
    grid: GlobalFieldGrid,
) -> np.ndarray:
    """Spread a prepared work source and restore the full-frame grid."""
    kernel = halation_psf_kernel((*grid.work_shape, 3), film)
    spread = cv2.filter2D(
        work_source,
        cv2.CV_32F,
        kernel,
        borderType=cv2.BORDER_CONSTANT,
    )
    if grid.reduced:
        spread = resample_global_scalar_field(spread, grid, to_work_grid=False)
    return spread.astype(np.float32, copy=False)


def _normalized_spread_scale_weights(
    weights: tuple[float, float, float] | np.ndarray,
) -> np.ndarray:
    values = np.asarray(weights, dtype=np.float32)
    if values.shape != (3,) or not np.all(np.isfinite(values)):
        raise ValueError("halation spread scale weights must contain three finite values")
    if np.any(values < 0.0):
        raise ValueError("halation spread scale weights must be non-negative")
    total = float(np.sum(values, dtype=np.float64))
    if total <= 1e-8:
        raise ValueError("halation spread scale weights must contain a positive value")
    values /= total
    return values


def spread_multiscale_halation_work_source(
    work_source: np.ndarray,
    film: FilmStockConfig,
    grid: GlobalFieldGrid,
    scale_weights: tuple[float, float, float] | np.ndarray,
) -> np.ndarray:
    """Spread one source over compact/main/wide scales and merge immediately.

    The established combined PSF remains the main component. Compact and wide
    components use separable Gaussian passes. Components are evaluated one at
    a time on the work grid; no three full-resolution return fields are kept.
    """
    work_source = np.asarray(work_source, dtype=np.float32)
    if work_source.shape != grid.work_shape:
        raise ValueError(
            f"halation work source must have shape {grid.work_shape}, got {work_source.shape}"
        )
    weights = _normalized_spread_scale_weights(scale_weights)
    if np.array_equal(weights, np.asarray((0.0, 1.0, 0.0), dtype=np.float32)):
        return spread_halation_work_source(work_source, film, grid)

    accumulator: np.ndarray | None = None
    work_shape = (*grid.work_shape, 3)
    for component_index, weight in enumerate(weights):
        component_weight = float(weight)
        if component_weight <= 0.0:
            continue
        if component_index == 0:
            sigma = radius_to_sigma(film.halation_core_radius, work_shape)
            component = cv2.GaussianBlur(
                work_source,
                (0, 0),
                sigmaX=sigma,
                sigmaY=sigma,
                borderType=cv2.BORDER_CONSTANT,
            )
        elif component_index == 1:
            kernel = halation_psf_kernel(work_shape, film)
            component = cv2.filter2D(
                work_source,
                cv2.CV_32F,
                kernel,
                borderType=cv2.BORDER_CONSTANT,
            )
        else:
            # Wide veil is deliberately evaluated on one global coarse grid.
            # It is a low-frequency return, so retaining the main 1200-1800 px
            # work grid only increases Gaussian cost without preserving useful
            # local detail. The fixed grid mapping remains frame-global.
            wide_grid = global_field_grid(
                work_source.shape,
                min(640, max(work_source.shape)),
            )
            wide_source = resample_global_scalar_field(
                work_source,
                wide_grid,
                to_work_grid=True,
            )
            # ``outer_radius`` is the wide-veil Gaussian sigma scale itself.
            sigma = max(
                0.35,
                float(film.halation_outer_radius) * min(wide_grid.work_shape),
            )
            component = cv2.GaussianBlur(
                wide_source,
                (0, 0),
                sigmaX=sigma,
                sigmaY=sigma,
                borderType=cv2.BORDER_CONSTANT,
            )
            del wide_source
            if wide_grid.reduced:
                component = resample_global_scalar_field(
                    component,
                    wide_grid,
                    to_work_grid=False,
                )
        if component_weight != 1.0:
            component *= component_weight
        if accumulator is None:
            accumulator = component
        else:
            accumulator += component
            del component

    if accumulator is None:
        raise RuntimeError("halation multiscale spread produced no component")
    if grid.reduced:
        accumulator = resample_global_scalar_field(
            accumulator,
            grid,
            to_work_grid=False,
        )
    return accumulator.astype(np.float32, copy=False)


def spread_halation_source(
    source: np.ndarray,
    film: FilmStockConfig,
    *,
    work_long_edge: int | None,
) -> np.ndarray:
    """Spread one already-gated global source and restore the full-frame grid.

    Source extraction deliberately remains full-resolution/tile-safe and occurs
    before this function.  The work-grid reduction therefore never decides
    whether a small highlight exists; it only changes how the low-frequency
    return field is evaluated.
    """
    source = np.asarray(source, dtype=np.float32)
    if source.ndim != 2:
        raise ValueError("halation source must be a two-dimensional scalar field")
    grid, work_source = prepare_halation_work_source(
        source,
        work_long_edge=work_long_edge,
    )
    return spread_halation_work_source(work_source, film, grid)


def apply_parameterized_warm_return(
    image: np.ndarray,
    spread: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    """Apply the legacy RGB exposure return without a full RGB halo buffer.

    This is deliberately named as the compatibility return, not as a material
    layer response.  A future layer-selective return must branch before this
    function and enter the latent-layer exposure path directly; it must not
    reinterpret layer weights as another RGB colour vector.
    """
    image = np.asarray(image, dtype=np.float32)
    spread = np.asarray(spread, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError("halation image must be an HxWx3 array")
    if spread.shape != image.shape[:2]:
        raise ValueError("halation spread must match the image height and width")

    halo_color = np.asarray(film.halation_color, dtype=np.float32)
    if halo_color.shape != (3,):
        raise ValueError("film halation_color must contain exactly three values")
    halo_color = halo_color / max(float(halo_color.max(initial=1.0)), 1e-6)
    strength = float(film.halation_strength)

    # Preserve the established float32 multiplication order while replacing
    # one HxWx3 halo plus the expression result with one output and one reusable
    # HxW scalar buffer.  The caller's exposure image remains immutable.
    output = image.copy()
    channel_delta = np.empty_like(spread, dtype=np.float32)
    for channel in range(3):
        np.multiply(spread, halo_color[channel], out=channel_delta)
        np.multiply(channel_delta, strength, out=channel_delta)
        np.add(output[..., channel], channel_delta, out=output[..., channel])
    np.clip(output, 0.0, 4.0, out=output)
    return output


def halation_return_field(
    image: np.ndarray,
    film: FilmStockConfig,
    fast: bool = False,
    work_long_edge: int | None = None,
    spread_scale_weights: tuple[float, float, float] | np.ndarray | None = None,
) -> np.ndarray:
    """Build one scalar material-return field before choosing its coupling."""
    image = np.asarray(image, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or image.size == 0:
        raise ValueError("halation image must be a non-empty HxWx3 array")

    if work_long_edge is None and fast:
        work_long_edge = 1600
    source_tile_rows = _automatic_halation_source_tile_rows(
        image.shape,
        work_long_edge,
    )
    source = (
        halation_source_energy(image, film)
        if source_tile_rows is None
        else halation_source_energy_tiled(
            image,
            film,
            tile_rows=source_tile_rows,
        )
    )
    grid, work_source = prepare_halation_work_source(
        source,
        work_long_edge=work_long_edge,
    )
    del source
    spread = (
        spread_halation_work_source(work_source, film, grid)
        if spread_scale_weights is None
        else spread_multiscale_halation_work_source(
            work_source,
            film,
            grid,
            spread_scale_weights,
        )
    )
    del work_source
    return spread


def apply_halation(
    image: np.ndarray,
    film: FilmStockConfig,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> np.ndarray:
    """用 PSF 对高光泄漏能量做卷积，再以暖色散射能量加回线性图像。"""
    image = np.asarray(image, dtype=np.float32)
    if float(film.halation_strength) <= 0.0:
        return image

    spread = halation_return_field(
        image,
        film,
        fast=fast,
        work_long_edge=work_long_edge,
    )
    return apply_parameterized_warm_return(image, spread, film)
