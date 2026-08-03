"""负片与反转正片的透射介质输出工具。

这里保存最终介质密度、物理透射、扫描器/灯台 raw 和派生材料层；
扫描/观看解释不会反写冲洗母版。
"""

from __future__ import annotations

from pathlib import Path
import json
from typing import Any

import cv2
import numpy as np
from PIL import Image

from half_frame_darkroom.core.atomic_io import (
    atomic_copy2,
    atomic_output_directory,
    atomic_output_path,
    atomic_savez_compressed,
    atomic_write_json,
)
from half_frame_darkroom.core.io_utils import (
    cv_imread_unicode,
    cv_imwrite_unicode,
    quantize_linear_rgb16,
    quantize_unit_float_rows,
)
from half_frame_darkroom.core.scanner import (
    capture_optical_density,
    negative_backlight_illuminant_rgb,
    render_negative_image,
    transmission_illuminant_rgb,
)
from half_frame_darkroom.core.sidecar import PROJECT_NAME, created_at
from half_frame_darkroom.core.states import developed_medium_metadata
from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


def _dye_density_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
    return np.einsum("...l,rl->...r", density_cmy, absorption).astype(np.float32)


def _published_paths(paths: dict[str, str], staging: Path, target: Path) -> dict[str, str]:
    published: dict[str, str] = {}
    for key, value in paths.items():
        path = Path(value)
        try:
            published[key] = str(target / path.relative_to(staging))
        except ValueError:
            published[key] = str(path)
    return published


def _normalize_channel(values: np.ndarray, low: float = 0.0, high: float | None = None) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if high is None:
        high = float(np.percentile(values, 99.5))
    scale = max(float(high) - float(low), 1e-6)
    return np.clip((values - float(low)) / scale, 0.0, 1.0).astype(np.float32)


def _save_gray_png(values: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    values = np.asarray(values)
    if values.ndim != 2 or values.size == 0:
        raise ValueError(f"Gray export must be a non-empty finite HxW array, got {values.shape}.")
    arr = quantize_unit_float_rows(
        values,
        bit_depth=8,
        label="Gray export",
    )
    with atomic_output_path(path) as temporary:
        Image.fromarray(arr, mode="L").save(temporary)
    return path


def _save_rgba_png(rgba: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    rgba = np.asarray(rgba)
    if rgba.ndim != 3 or rgba.shape[-1] != 4 or rgba.size == 0:
        raise ValueError(f"RGBA export must be a non-empty finite HxWx4 array, got {rgba.shape}.")
    arr = quantize_unit_float_rows(
        rgba,
        bit_depth=8,
        label="RGBA export",
    )
    with atomic_output_path(path) as temporary:
        Image.fromarray(arr, mode="RGBA").save(temporary)
    return path


def _save_rgba_tiff_16(rgba: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    rgba = np.asarray(rgba)
    if rgba.ndim != 3 or rgba.shape[-1] != 4 or rgba.size == 0:
        raise ValueError(f"RGBA export must be a non-empty finite HxWx4 array, got {rgba.shape}.")
    bgra = quantize_unit_float_rows(
        rgba,
        bit_depth=16,
        channel_order=(2, 1, 0, 3),
        label="RGBA export",
    )
    if not cv_imwrite_unicode(path, bgra):
        raise OSError(f"Failed to save transparent plate TIFF: {path}")
    return path


def density_alpha(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """把染料沉积强度压成 alpha 蒙版，透明区约等于未曝光片基。"""
    dye_density = _dye_density_rgb(density_cmy, film)
    density = dye_density[..., 0] * 0.2126 + dye_density[..., 1] * 0.7152 + dye_density[..., 2] * 0.0722
    return _normalize_channel(density, low=0.0)


def transparent_medium_rgba(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """Generate transparent-base developed medium RGBA from dye density."""
    dye_density = np.clip(_dye_density_rgb(density_cmy, film), 0.0, None)
    transmittance = np.power(10.0, -dye_density).astype(np.float32)
    alpha = density_alpha(density_cmy, film)
    return np.dstack((np.clip(transmittance, 0.0, 1.0), alpha)).astype(np.float32)


def medium_transmission_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """Linear physical transmission including film base and formed image density."""
    dye_density = np.clip(_dye_density_rgb(density_cmy, film), 0.0, None)
    base_density = np.asarray(film.film_base_density_rgb, dtype=np.float32).reshape(1, 1, 3)
    return np.power(10.0, -(dye_density + base_density)).astype(np.float32)


def transparent_negative_rgba(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """生成透明片基电子负片 RGBA；RGB 是无橙罩染料透射色，A 是沉积强度。"""
    return transparent_medium_rgba(density_cmy, film)


def postprocess_density_delta_alpha(
    density_cmy: np.ndarray,
    density_grain: np.ndarray,
) -> np.ndarray:
    """Visualize the combined legacy layer-density delta.

    This includes emulsion grain and layer-space proxies for some post-process
    accidents. It must not be interpreted as a pure grain field.
    """
    delta = np.mean(np.abs(np.asarray(density_grain, dtype=np.float32) - np.asarray(density_cmy, dtype=np.float32)), axis=-1)
    return _normalize_channel(delta)


def grain_alpha(density_cmy: np.ndarray, density_grain: np.ndarray) -> np.ndarray:
    """Compatibility alias for the historical combined-delta visualization."""
    return postprocess_density_delta_alpha(density_cmy, density_grain)


def _halation_luma(after_mtf: np.ndarray, after_halation: np.ndarray) -> np.ndarray:
    """Return the shared positive halation difference in luminance space."""
    diff = np.maximum(
        np.asarray(after_halation, dtype=np.float32)
        - np.asarray(after_mtf, dtype=np.float32),
        0.0,
    )
    return (
        diff[..., 0] * 0.2126
        + diff[..., 1] * 0.7152
        + diff[..., 2] * 0.0722
    ).astype(np.float32)


def halation_alpha(after_mtf: np.ndarray, after_halation: np.ndarray) -> np.ndarray:
    """光晕层：halation 相对 MTF 后曝光代理增加的亮度。"""
    return _normalize_channel(_halation_luma(after_mtf, after_halation))


def halation_alpha_linear(after_mtf: np.ndarray, after_halation: np.ndarray, scale: float = 0.05) -> np.ndarray:
    """Fixed-scale halation strength map; unlike preview, it is not normalized per image."""
    values = _halation_luma(after_mtf, after_halation)
    return np.clip(values / max(float(scale), 1e-6), 0.0, 1.0).astype(np.float32)


def export_transparent_plate_set(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    output_dir: str | Path,
    *,
    polarity: str = "negative",
    optical_density_rgb: np.ndarray | None = None,
    clear_base_optical_density_rgb: np.ndarray | tuple[float, float, float] | None = None,
    _transactional: bool = True,
) -> dict[str, str]:
    """导出透明片基电子负片和 density alpha。"""
    output_dir = Path(output_dir)
    if _transactional:
        with atomic_output_directory(output_dir) as staging:
            staged_paths = export_transparent_plate_set(
                density_cmy,
                film,
                staging,
                polarity=polarity,
                optical_density_rgb=optical_density_rgb,
                clear_base_optical_density_rgb=clear_base_optical_density_rgb,
                _transactional=False,
            )
        return _published_paths(staged_paths, staging, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    prefix = "positive" if str(polarity).lower() == "positive" else "negative"
    # Reuse one dye-density transform for RGBA, alpha, and physical
    # transmission. These were previously three identical full-frame matrix
    # transforms; sharing the result does not change the optical formulas.
    if optical_density_rgb is None:
        image_density = np.clip(_dye_density_rgb(density_cmy, film), 0.0, None)
        physical_density = image_density + np.asarray(
            film.film_base_density_rgb,
            dtype=np.float32,
        ).reshape(1, 1, 3)
    else:
        physical_density = np.clip(
            np.asarray(optical_density_rgb, dtype=np.float32),
            0.0,
            None,
        )
        if physical_density.shape != np.asarray(density_cmy).shape:
            raise ValueError("optical_density_rgb must match density_cmy geometry")
        if clear_base_optical_density_rgb is None:
            clear = np.asarray(film.film_base_density_rgb, dtype=np.float32)
        else:
            clear = np.asarray(clear_base_optical_density_rgb, dtype=np.float32)
        if clear.size != 3 or not np.all(np.isfinite(clear)) or np.any(clear < 0.0):
            raise ValueError("clear_base_optical_density_rgb must contain three finite nonnegative values")
        image_density = np.clip(physical_density - clear.reshape(1, 1, 3), 0.0, None)
    density_luma = (
        image_density[..., 0] * 0.2126
        + image_density[..., 1] * 0.7152
        + image_density[..., 2] * 0.0722
    )
    alpha = _normalize_channel(density_luma, low=0.0)
    del density_luma

    # Encode both transparent renditions before allocating the additional
    # physical-transmission RGB array.  This shortens the lifetime overlap of
    # full-frame buffers without changing any optical formula or pixel value.
    transmittance = np.power(10.0, -image_density).astype(np.float32)
    rgba = np.dstack((np.clip(transmittance, 0.0, 1.0), alpha)).astype(np.float32)
    paths: dict[str, str] = {}
    paths[f"{prefix}_transparent_png"] = str(
        _save_rgba_png(rgba, output_dir / f"{prefix}_transparent.png")
    )
    paths[f"{prefix}_transparent_16bit_tiff"] = str(
        _save_rgba_tiff_16(rgba, output_dir / f"{prefix}_transparent_16bit.tiff")
    )
    del rgba, transmittance

    physical_transmission = np.power(10.0, -physical_density).astype(np.float32)
    paths[f"{prefix}_transmission_16bit_tiff"] = str(
        save_linear_rgb_tiff(
            physical_transmission,
            output_dir / f"{prefix}_transmission_16bit.tiff",
        )
    )
    del physical_transmission, physical_density, image_density
    paths["density_alpha_png"] = str(_save_gray_png(alpha, output_dir / "density_alpha.png"))
    return paths


def export_plate_set(
    density_cmy: np.ndarray,
    density_grain: np.ndarray,
    after_mtf: np.ndarray,
    after_halation: np.ndarray,
    film: FilmStockConfig,
    output_dir: str | Path,
    *,
    _transactional: bool = True,
) -> dict[str, str]:
    """导出 CMY 制版分层、总密度、颗粒层和光晕层。"""
    output_dir = Path(output_dir)
    if _transactional:
        with atomic_output_directory(output_dir) as staging:
            staged_paths = export_plate_set(
                density_cmy,
                density_grain,
                after_mtf,
                after_halation,
                film,
                staging,
                _transactional=False,
            )
        return _published_paths(staged_paths, staging, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_norm = np.clip((np.asarray(density_grain, dtype=np.float32) - d_min) / np.maximum(d_max - d_min, 1e-6), 0.0, 1.0)
    paths = {
        "cyan_plate": str(_save_gray_png(density_norm[..., 0], output_dir / "cyan_plate.png")),
        "magenta_plate": str(_save_gray_png(density_norm[..., 1], output_dir / "magenta_plate.png")),
        "yellow_plate": str(_save_gray_png(density_norm[..., 2], output_dir / "yellow_plate.png")),
    }
    total_density = np.mean(density_norm, axis=-1)
    paths["density_plate"] = str(
        _save_gray_png(total_density, output_dir / "density_plate.png")
    )
    del total_density, density_norm

    density_delta = postprocess_density_delta_alpha(density_cmy, density_grain)
    legacy_delta_path = str(
        _save_gray_png(density_delta, output_dir / "grain_layer.png")
    )
    # Preserve the old key and filename while exposing the truthful semantic
    # alias in new manifests and sidecars.
    paths["grain_layer"] = legacy_delta_path
    paths["postprocess_density_delta_layer"] = legacy_delta_path
    del density_delta

    # Preview and fixed-scale plates are two interpretations of the same
    # halation luma map; compute the full-frame RGB difference only once.
    halation_luma = _halation_luma(after_mtf, after_halation)
    halation_preview = _normalize_channel(halation_luma)
    paths["halation_layer_preview"] = str(
        _save_gray_png(halation_preview, output_dir / "halation_layer_preview.png")
    )
    del halation_preview
    halation_linear = np.clip(halation_luma / 0.05, 0.0, 1.0).astype(np.float32)
    paths["halation_layer_linear"] = str(
        _save_gray_png(halation_linear, output_dir / "halation_layer_linear.png")
    )
    return paths


def export_layer_pack(
    negative,
    film: FilmStockConfig,
    output_dir: str | Path,
    *,
    polarity: str | None = None,
    source_negative_path: str | Path | None = None,
    scanner_raw_path: str | Path | None = None,
    orange_preview_path: str | Path | None = None,
    metadata: dict[str, Any] | None = None,
    _published_dir: Path | None = None,
) -> dict[str, str]:
    """Export an archive bundle containing the medium and all derived products."""
    output_dir = Path(output_dir)
    if _published_dir is None:
        with atomic_output_directory(output_dir) as staging:
            return export_layer_pack(
                negative,
                film,
                staging,
                polarity=polarity,
                source_negative_path=source_negative_path,
                scanner_raw_path=scanner_raw_path,
                orange_preview_path=orange_preview_path,
                metadata=metadata,
                _published_dir=output_dir,
            )
    published_dir = Path(_published_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    resolved_polarity = str(polarity or getattr(negative, "image_polarity", "negative")).lower()
    is_positive = resolved_polarity == "positive"
    prefix = "positive" if is_positive else "negative"

    npz_path = output_dir / f"electronic_{prefix}.npz"
    if source_negative_path is not None and Path(source_negative_path).exists():
        atomic_copy2(source_negative_path, npz_path)
    else:
        medium_arrays = {
            "density_cmy": np.asarray(negative.density_cmy, dtype=np.float32),
            "density_grain": np.asarray(negative.density_grain, dtype=np.float32),
            "developed_medium_metadata": np.asarray(
                json.dumps(developed_medium_metadata(negative), ensure_ascii=False, allow_nan=False)
            ),
        }
        if getattr(negative, "optical_density_rgb", None) is not None:
            medium_arrays["optical_density_rgb"] = np.asarray(
                negative.optical_density_rgb,
                dtype=np.float32,
            )
        if getattr(negative, "clear_base_optical_density_rgb", None) is not None:
            medium_arrays["clear_base_optical_density_rgb"] = np.asarray(
                negative.clear_base_optical_density_rgb,
                dtype=np.float32,
            )
        atomic_savez_compressed(npz_path, **medium_arrays)
    paths[f"electronic_{prefix}_npz"] = str(npz_path)

    if source_negative_path is not None and Path(source_negative_path).exists():
        paths["source_negative_npz"] = str(source_negative_path)
    if scanner_raw_path is not None and Path(scanner_raw_path).exists():
        copied = output_dir / ("light_table_raw_linear_16bit.tiff" if is_positive else "scanner_raw_linear_16bit.tiff")
        atomic_copy2(scanner_raw_path, copied)
        paths["light_table_raw_linear_16bit" if is_positive else "scanner_raw_linear_16bit"] = str(copied)
    if orange_preview_path is not None and Path(orange_preview_path).exists():
        copied = output_dir / ("positive_visual.png" if is_positive else "negative_visual_orange_base.png")
        atomic_copy2(orange_preview_path, copied)
        paths["positive_visual" if is_positive else "negative_visual_orange_base"] = str(copied)

    paths.update(export_transparent_plate_set(
        negative.density_grain,
        film,
        output_dir,
        polarity=resolved_polarity,
        optical_density_rgb=getattr(negative, "optical_density_rgb", None),
        clear_base_optical_density_rgb=getattr(
            negative,
            "clear_base_optical_density_rgb",
            None,
        ),
        _transactional=False,
    ))
    paths.update(
        export_plate_set(
            negative.density_cmy,
            negative.density_grain,
            negative.after_mtf,
            negative.after_halation,
            film,
            output_dir,
            _transactional=False,
        )
    )

    sidecar_path = output_dir / "sidecar.json"
    metadata_payload = metadata or {}
    published_paths = _published_paths(paths, output_dir, published_dir)
    atomic_write_json(
        sidecar_path,
        {
            "kind": "ElectronicPositiveLayerPack" if is_positive else "ElectronicNegativeLayerPack",
            "created_at": created_at(),
            "project": PROJECT_NAME,
            "image_polarity": resolved_polarity,
            "paths": published_paths,
            "developed_medium": metadata_payload.get("developed_medium", developed_medium_metadata(negative)),
            "interpreter": metadata_payload.get("interpreter"),
            "metadata": metadata_payload,
        },
    )
    published_paths["sidecar"] = str(published_dir / sidecar_path.relative_to(output_dir))
    return published_paths


def scanner_raw_with_clear_border(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig,
    border_percent: float = 0.04,
    border_min_px: int = 32,
    *,
    optical_density_rgb: np.ndarray | None = None,
    clear_base_optical_density_rgb: np.ndarray | tuple[float, float, float] | None = None,
) -> np.ndarray:
    """生成带未曝光片基边框的 scanner raw 负片图像。"""
    if optical_density_rgb is None:
        scanner_raw = render_negative_image(density_cmy, film, scanner)
    else:
        scanner_raw = capture_optical_density(
            np.asarray(optical_density_rgb, dtype=np.float32),
            scanner,
            illuminant_rgb=transmission_illuminant_rgb(scanner),
        )
    if clear_base_optical_density_rgb is None:
        clear_density = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
        clear_raw = render_negative_image(clear_density, film, scanner)[0, 0]
    else:
        clear_density = np.asarray(clear_base_optical_density_rgb, dtype=np.float32).reshape(1, 1, 3)
        clear_raw = capture_optical_density(
            clear_density,
            scanner,
            illuminant_rgb=transmission_illuminant_rgb(scanner),
        )[0, 0]
    return scanner_raw_with_reference_border(
        scanner_raw,
        clear_raw,
        border_percent=border_percent,
        border_min_px=border_min_px,
    )


def scanner_raw_with_reference_border(
    scanner_raw: np.ndarray,
    clear_base_raw_rgb: np.ndarray | tuple[float, float, float],
    *,
    border_percent: float = 0.04,
    border_min_px: int = 32,
) -> np.ndarray:
    """Place captured transmission inside a synthetic clear-base reference.

    The border is an observation aid. It is derived after the immutable final
    medium has been composed and therefore cannot alter process state or the
    authoritative RGB optical-density master.
    """
    scanner_raw = np.asarray(scanner_raw, dtype=np.float32)
    if (
        scanner_raw.ndim != 3
        or scanner_raw.shape[-1] != 3
        or scanner_raw.shape[0] <= 0
        or scanner_raw.shape[1] <= 0
    ):
        raise ValueError(
            f"Scanner raw must have non-empty HxWx3 shape, got {scanner_raw.shape}."
        )
    clear_raw = np.asarray(clear_base_raw_rgb, dtype=np.float32)
    if clear_raw.size != 3 or not np.all(np.isfinite(clear_raw)):
        raise ValueError("clear_base_raw_rgb must contain three finite values.")
    height, width = scanner_raw.shape[:2]
    border = scanner_raw_export_border_width(
        scanner_raw.shape,
        border_percent,
        border_min_px,
    )
    if border <= 0:
        return scanner_raw
    canvas = np.empty((height + border * 2, width + border * 2, 3), dtype=np.float32)
    canvas[...] = clear_raw.reshape(1, 1, 3)
    canvas[border : border + height, border : border + width] = scanner_raw
    return np.clip(canvas, 0.0, 1.0).astype(np.float32)


def scanner_raw_export_border_width(
    inner_shape: tuple[int, int] | tuple[int, int, int],
    border_percent: float = 0.04,
    border_min_px: int = 32,
) -> int:
    """Return the exact border added around an unbordered image at export."""
    if len(inner_shape) < 2 or int(inner_shape[0]) <= 0 or int(inner_shape[1]) <= 0:
        raise ValueError(f"Scanner raw inner shape must have positive height and width, got {inner_shape}.")
    height, width = int(inner_shape[0]), int(inner_shape[1])
    percent_value = float(border_percent)
    if not np.isfinite(percent_value):
        raise ValueError("Scanner raw border percent must be finite.")
    percent = max(percent_value, 0.0)
    if percent <= 0.0:
        return 0
    border = int(round(min(height, width) * percent))
    border = max(border, int(border_min_px))
    return max(border, 0)


def save_linear_rgb_tiff(image: np.ndarray, path: str | Path) -> Path:
    """把线性 RGB 保存成 16-bit TIFF，不做 sRGB gamma 编码。"""
    path = Path(path)
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[-1] != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Linear RGB export must have non-empty HxWx3 shape, got {image.shape}.")
    bgr16 = quantize_linear_rgb16(image, bgr=True)
    if not cv_imwrite_unicode(path, bgr16):
        raise OSError(f"Failed to save scanner raw TIFF: {path}")
    return path


def load_linear_rgb_tiff(path: str | Path) -> np.ndarray:
    """读取 16-bit/8-bit TIFF 电子负片，按线性 RGB 返回 float32。"""
    path = Path(path)
    image = cv_imread_unicode(path, cv2.IMREAD_UNCHANGED | cv2.IMREAD_COLOR)
    if image is None:
        raise OSError(f"Failed to load scanner raw TIFF: {path}")
    if image.ndim != 3 or image.shape[-1] != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Scanner raw TIFF must decode to non-empty HxWx3 data, got {image.shape}: {path}")
    image = image[..., ::-1]
    if image.dtype == np.uint16:
        result = (image.astype(np.float32) / 65535.0).clip(0.0, 1.0)
    elif image.dtype == np.uint8:
        result = (image.astype(np.float32) / 255.0).clip(0.0, 1.0)
    else:
        result = np.asarray(image, dtype=np.float32)
        minimum = float(np.min(result))
        maximum = float(np.max(result))
        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError(f"Scanner raw TIFF contains NaN or infinite values: {path}")
        result = result.clip(0.0, 1.0)
    return result


def scanner_raw_border_width(
    shape: tuple[int, int] | tuple[int, int, int],
    border_percent: float = 0.04,
    border_min_px: int = 32,
) -> int:
    """根据图像尺寸和配置估算电子负片片基边框宽度。"""
    if len(shape) < 2 or int(shape[0]) <= 0 or int(shape[1]) <= 0:
        raise ValueError(f"Scanner raw shape must have positive height and width, got {shape}.")
    height, width = int(shape[0]), int(shape[1])
    percent = float(border_percent)
    if not np.isfinite(percent):
        raise ValueError("Scanner raw border percent must be finite.")
    border = int(round(min(height, width) * max(percent, 0.0)))
    if border > 0:
        border = max(border, int(border_min_px))
    max_border = max(0, min(height, width) // 3)
    return min(border, max_border)


def split_scanner_raw_border(
    scanner_raw: np.ndarray,
    border_percent: float = 0.04,
    border_min_px: int = 32,
    *,
    border_width_px: int | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """把带片基边框的电子负片拆成画面区域和边框采样区域。"""
    scanner_raw = np.asarray(scanner_raw, dtype=np.float32)
    if scanner_raw.ndim != 3 or scanner_raw.shape[-1] != 3 or scanner_raw.shape[0] <= 0 or scanner_raw.shape[1] <= 0:
        raise ValueError(f"Scanner raw must have non-empty HxWx3 shape, got {scanner_raw.shape}.")
    minimum = float(np.min(scanner_raw))
    maximum = float(np.max(scanner_raw))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("Scanner raw contains NaN or infinite values.")
    if border_width_px is None:
        border = scanner_raw_border_width(scanner_raw.shape, border_percent, border_min_px)
    else:
        # An explicit project sidecar is trusted export geometry.  Small test
        # images can legitimately have a minimum border wider than one third
        # of the outer raw while still leaving a non-empty inner image.
        max_border = max(0, (min(scanner_raw.shape[:2]) - 1) // 2)
        border = min(max(int(border_width_px), 0), max_border)
    if border <= 0:
        return scanner_raw, None

    height, width = scanner_raw.shape[:2]
    if height <= border * 2 or width <= border * 2:
        return scanner_raw, None

    inner = scanner_raw[border : height - border, border : width - border]
    top = scanner_raw[:border, :, :].reshape(-1, 3)
    bottom = scanner_raw[height - border :, :, :].reshape(-1, 3)
    left = scanner_raw[border : height - border, :border, :].reshape(-1, 3)
    right = scanner_raw[border : height - border, width - border :, :].reshape(-1, 3)
    border_samples = np.concatenate((top, bottom, left, right), axis=0)
    return inner.astype(np.float32), border_samples.astype(np.float32)
