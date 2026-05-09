"""图像读取与保存工具。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from half_frame_darkroom.core.color import ensure_rgb_float
from half_frame_darkroom.model.config import OutputConfig


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def load_image(path: str | Path) -> np.ndarray:
    """读取图像，并整理成显示编码 sRGB 下的 float32 RGB。"""
    with Image.open(path) as image:
        return ensure_rgb_float(np.asarray(image.convert("RGB")))


def save_image(image: np.ndarray, path: str | Path, output: OutputConfig) -> None:
    """保存归一化 sRGB 图像，支持 8-bit 和部分 16-bit 格式。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)

    if int(output.bit_depth) == 16:
        if path.suffix.lower() not in {".png", ".tif", ".tiff"}:
            raise ValueError("16-bit output is supported for PNG and TIFF paths only.")
        arr = np.round(image * 65535.0).astype(np.uint16)[..., ::-1]
        if not cv2.imwrite(str(path), arr):
            raise OSError(f"Failed to save image: {path}")
        return

    arr = np.round(image * 255.0).astype(np.uint8)
    pil_image = Image.fromarray(arr, mode="RGB")

    save_kwargs: dict[str, int] = {}
    if path.suffix.lower() in {".jpg", ".jpeg", ".webp"}:
        save_kwargs["quality"] = int(output.quality)
    pil_image.save(path, **save_kwargs)


def iter_images(path: str | Path) -> list[Path]:
    """从单个文件或文件夹中找出支持的图像文件。"""
    path = Path(path)
    if path.is_file():
        return [path]
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
    )
