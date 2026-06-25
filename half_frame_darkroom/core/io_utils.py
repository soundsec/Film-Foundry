"""图像读取与保存工具。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from half_frame_darkroom.core.color import ensure_rgb_float
from half_frame_darkroom.model.config import OutputConfig


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".rw2", ".orf", ".raf", ".pef"}


def cv_imread_unicode(path: str | Path, flags: int = cv2.IMREAD_UNCHANGED) -> np.ndarray | None:
    """用 OpenCV 解码图像，同时兼容 Windows 中文路径。"""
    path = Path(path)
    if not path.exists():
        return None
    data = np.fromfile(path, dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def cv_imwrite_unicode(path: str | Path, image: np.ndarray) -> bool:
    """用 OpenCV 编码图像，同时兼容 Windows 中文路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        return False
    encoded.tofile(path)
    return True


def _load_cv_preserve(path: Path) -> np.ndarray:
    image = cv_imread_unicode(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise OSError(f"Failed to load image: {path}")
    if image.ndim == 3 and image.shape[-1] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    elif image.ndim == 3 and image.shape[-1] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    return ensure_rgb_float(image)


def load_image(path: str | Path) -> np.ndarray:
    """读取图像，并整理成显示编码 sRGB 下的 float32 RGB。"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in RAW_EXTENSIONS:
        raise ValueError(
            f"RAW/DNG input is not supported yet: {path}. "
            "Please export a display-referred TIFF/PNG/JPEG first, or add a dedicated RAW adapter later."
        )
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported input format: {path.suffix or '(no extension)'}. "
            f"Supported formats are: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    if suffix in {".png", ".tif", ".tiff"}:
        return _load_cv_preserve(path)
    with Image.open(path) as image:
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            return ensure_rgb_float(np.asarray(image.convert("RGBA")))
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
        if not cv_imwrite_unicode(path, arr):
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
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
    )
