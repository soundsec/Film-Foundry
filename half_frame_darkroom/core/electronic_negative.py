"""电子负片输出工具。

这里保存的是扫描器看到的线性负片图像，而不是最终正像。
边框使用未曝光片基的透射值，方便后续手动或自动去橙罩。
"""

from __future__ import annotations

from pathlib import Path
import json
import shutil
from typing import Any

import cv2
import numpy as np
from PIL import Image

from half_frame_darkroom.core.scanner import render_negative_image
from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


def _dye_density_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
    return np.einsum("...l,rl->...r", density_cmy, absorption).astype(np.float32)


def _normalize_channel(values: np.ndarray, low: float = 0.0, high: float | None = None) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    if high is None:
        high = float(np.percentile(values, 99.5))
    scale = max(float(high) - float(low), 1e-6)
    return np.clip((values - float(low)) / scale, 0.0, 1.0).astype(np.float32)


def _save_gray_png(values: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.round(np.clip(values, 0.0, 1.0) * 255.0).astype(np.uint8)
    Image.fromarray(arr, mode="L").save(path)
    return path


def _save_rgba_png(rgba: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.round(np.clip(rgba, 0.0, 1.0) * 255.0).astype(np.uint8)
    Image.fromarray(arr, mode="RGBA").save(path)
    return path


def _save_rgba_tiff_16(rgba: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.round(np.clip(rgba, 0.0, 1.0) * 65535.0).astype(np.uint16)
    bgra = arr[..., [2, 1, 0, 3]]
    if not cv2.imwrite(str(path), bgra):
        raise OSError(f"Failed to save transparent plate TIFF: {path}")
    return path


def density_alpha(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """把染料沉积强度压成 alpha 蒙版，透明区约等于未曝光片基。"""
    dye_density = _dye_density_rgb(density_cmy, film)
    density = dye_density[..., 0] * 0.2126 + dye_density[..., 1] * 0.7152 + dye_density[..., 2] * 0.0722
    return _normalize_channel(density, low=0.0)


def transparent_negative_rgba(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """生成透明片基电子负片 RGBA；RGB 是无橙罩染料透射色，A 是沉积强度。"""
    dye_density = np.clip(_dye_density_rgb(density_cmy, film), 0.0, None)
    transmittance = np.power(10.0, -dye_density).astype(np.float32)
    alpha = density_alpha(density_cmy, film)
    return np.dstack((np.clip(transmittance, 0.0, 1.0), alpha)).astype(np.float32)


def grain_alpha(density_cmy: np.ndarray, density_grain: np.ndarray) -> np.ndarray:
    """颗粒层：密度域 grain 与无 grain 密度的差异强度。"""
    delta = np.mean(np.abs(np.asarray(density_grain, dtype=np.float32) - np.asarray(density_cmy, dtype=np.float32)), axis=-1)
    return _normalize_channel(delta)


def halation_alpha(after_mtf: np.ndarray, after_halation: np.ndarray) -> np.ndarray:
    """光晕层：halation 相对 MTF 后曝光代理增加的亮度。"""
    diff = np.maximum(np.asarray(after_halation, dtype=np.float32) - np.asarray(after_mtf, dtype=np.float32), 0.0)
    values = diff[..., 0] * 0.2126 + diff[..., 1] * 0.7152 + diff[..., 2] * 0.0722
    return _normalize_channel(values)


def export_transparent_plate_set(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    output_dir: str | Path,
) -> dict[str, str]:
    """导出透明片基电子负片和 density alpha。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rgba = transparent_negative_rgba(density_cmy, film)
    alpha = rgba[..., 3]
    paths = {
        "negative_transparent_png": str(_save_rgba_png(rgba, output_dir / "negative_transparent.png")),
        "negative_transparent_16bit_tiff": str(_save_rgba_tiff_16(rgba, output_dir / "negative_transparent_16bit.tiff")),
        "density_alpha_png": str(_save_gray_png(alpha, output_dir / "density_alpha.png")),
    }
    return paths


def export_plate_set(
    density_cmy: np.ndarray,
    density_grain: np.ndarray,
    after_mtf: np.ndarray,
    after_halation: np.ndarray,
    film: FilmStockConfig,
    output_dir: str | Path,
) -> dict[str, str]:
    """导出 CMY 制版分层、总密度、颗粒层和光晕层。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    d_min = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    d_max = np.asarray(film.density_max, dtype=np.float32).reshape(1, 1, 3)
    density_norm = np.clip((np.asarray(density_grain, dtype=np.float32) - d_min) / np.maximum(d_max - d_min, 1e-6), 0.0, 1.0)
    total_density = np.mean(density_norm, axis=-1)
    paths = {
        "cyan_plate": str(_save_gray_png(density_norm[..., 0], output_dir / "cyan_plate.png")),
        "magenta_plate": str(_save_gray_png(density_norm[..., 1], output_dir / "magenta_plate.png")),
        "yellow_plate": str(_save_gray_png(density_norm[..., 2], output_dir / "yellow_plate.png")),
        "density_plate": str(_save_gray_png(total_density, output_dir / "density_plate.png")),
        "grain_layer": str(_save_gray_png(grain_alpha(density_cmy, density_grain), output_dir / "grain_layer.png")),
        "halation_layer": str(_save_gray_png(halation_alpha(after_mtf, after_halation), output_dir / "halation_layer.png")),
    }
    return paths


def export_layer_pack(
    negative,
    film: FilmStockConfig,
    output_dir: str | Path,
    *,
    source_negative_path: str | Path | None = None,
    scanner_raw_path: str | Path | None = None,
    orange_preview_path: str | Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """导出一整套可复用图像材料包。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    npz_path = output_dir / "electronic_negative.npz"
    np.savez_compressed(
        npz_path,
        density_cmy=negative.density_cmy.astype(np.float32),
        density_grain=negative.density_grain.astype(np.float32),
    )
    paths["electronic_negative_npz"] = str(npz_path)

    if source_negative_path is not None and Path(source_negative_path).exists():
        paths["source_negative_npz"] = str(source_negative_path)
    if scanner_raw_path is not None and Path(scanner_raw_path).exists():
        copied = output_dir / "scanner_raw_linear_16bit.tiff"
        shutil.copy2(scanner_raw_path, copied)
        paths["scanner_raw_linear_16bit"] = str(copied)
    if orange_preview_path is not None and Path(orange_preview_path).exists():
        copied = output_dir / "negative_visual_orange_base.png"
        shutil.copy2(orange_preview_path, copied)
        paths["negative_visual_orange_base"] = str(copied)

    paths.update(export_transparent_plate_set(negative.density_grain, film, output_dir))
    paths.update(
        export_plate_set(
            negative.density_cmy,
            negative.density_grain,
            negative.after_mtf,
            negative.after_halation,
            film,
            output_dir,
        )
    )

    sidecar_path = output_dir / "sidecar.json"
    with sidecar_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "kind": "ElectronicNegativeLayerPack",
                "project": "Film Foundry / Electronic Negative Factory",
                "paths": paths,
                "metadata": metadata or {},
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )
    paths["sidecar"] = str(sidecar_path)
    return paths


def scanner_raw_with_clear_border(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig,
    border_percent: float = 0.04,
    border_min_px: int = 32,
) -> np.ndarray:
    """生成带未曝光片基边框的 scanner raw 负片图像。"""
    scanner_raw = render_negative_image(density_cmy, film, scanner)
    height, width = scanner_raw.shape[:2]
    border = int(round(min(height, width) * max(float(border_percent), 0.0)))
    if border > 0:
        border = max(border, int(border_min_px))
    if border <= 0:
        return scanner_raw

    clear_density = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)
    clear_raw = render_negative_image(clear_density, film, scanner)[0, 0]
    canvas = np.empty((height + border * 2, width + border * 2, 3), dtype=np.float32)
    canvas[...] = clear_raw.reshape(1, 1, 3)
    canvas[border : border + height, border : border + width] = scanner_raw
    return np.clip(canvas, 0.0, 1.0).astype(np.float32)


def save_linear_rgb_tiff(image: np.ndarray, path: str | Path) -> Path:
    """把线性 RGB 保存成 16-bit TIFF，不做 sRGB gamma 编码。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    bgr16 = np.round(image * 65535.0).astype(np.uint16)[..., ::-1]
    if not cv2.imwrite(str(path), bgr16):
        raise OSError(f"Failed to save scanner raw TIFF: {path}")
    return path


def load_linear_rgb_tiff(path: str | Path) -> np.ndarray:
    """读取 16-bit/8-bit TIFF 电子负片，按线性 RGB 返回 float32。"""
    path = Path(path)
    image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED | cv2.IMREAD_COLOR)
    if image is None:
        raise OSError(f"Failed to load scanner raw TIFF: {path}")
    image = image[..., ::-1]
    if image.dtype == np.uint16:
        return (image.astype(np.float32) / 65535.0).clip(0.0, 1.0)
    if image.dtype == np.uint8:
        return (image.astype(np.float32) / 255.0).clip(0.0, 1.0)
    return np.asarray(image, dtype=np.float32).clip(0.0, 1.0)


def scanner_raw_border_width(
    shape: tuple[int, int] | tuple[int, int, int],
    border_percent: float = 0.04,
    border_min_px: int = 32,
) -> int:
    """根据图像尺寸和配置估算电子负片片基边框宽度。"""
    height, width = int(shape[0]), int(shape[1])
    border = int(round(min(height, width) * max(float(border_percent), 0.0)))
    if border > 0:
        border = max(border, int(border_min_px))
    max_border = max(0, min(height, width) // 3)
    return min(border, max_border)


def split_scanner_raw_border(
    scanner_raw: np.ndarray,
    border_percent: float = 0.04,
    border_min_px: int = 32,
) -> tuple[np.ndarray, np.ndarray | None]:
    """把带片基边框的电子负片拆成画面区域和边框采样区域。"""
    scanner_raw = np.asarray(scanner_raw, dtype=np.float32)
    border = scanner_raw_border_width(scanner_raw.shape, border_percent, border_min_px)
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
