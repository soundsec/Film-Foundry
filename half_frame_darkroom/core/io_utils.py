"""图像读取与保存工具。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

from half_frame_darkroom.core.antibanding import apply_output_antibanding_region
from half_frame_darkroom.core.atomic_io import (
    atomic_output_path,
    atomic_path_set,
    atomic_write_bytes,
    atomic_write_json,
)
from half_frame_darkroom.core.color import ensure_rgb_float
from half_frame_darkroom.core.provenance import PROJECT_NAME
from half_frame_darkroom.model.config import OutputConfig


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".rw2", ".orf", ".raf", ".pef"}
_EXIF_ORIENTATION_TAG = 274


def output_target_is_file(path: str | Path) -> bool:
    """Resolve an output target without misclassifying existing dotted folders."""
    target = Path(path)
    if target.exists():
        return target.is_file()
    return bool(target.suffix)


def scan_output_stem(path: str | Path) -> str:
    """Build a collision-safe stem for developed media and transmission raws."""
    stem = Path(path).stem
    lowered = stem.lower()
    polarity: str | None = None
    for suffix, raw_polarity in (
        (".scanner_raw", "negative"),
        (".light_table_raw", "positive"),
    ):
        if lowered.endswith(suffix):
            stem = stem[: -len(suffix)]
            lowered = lowered[: -len(suffix)]
            polarity = raw_polarity
            break
    for suffix, medium_polarity in (
        (".darkroom_negative", "negative"),
        (".darkroom_positive", "positive"),
    ):
        if lowered.endswith(suffix):
            stem = stem[: -len(suffix)]
            polarity = medium_polarity
            break
    return f"{stem}_{polarity}" if polarity else stem


def assert_unique_output_stems(paths: list[Path], label: str = "Batch") -> None:
    """Reject batch inputs that would map to the same generated output stem."""
    seen: dict[str, Path] = {}
    for path in paths:
        stem = scan_output_stem(path)
        key = stem.casefold()
        previous = seen.get(key)
        if previous is not None:
            raise ValueError(
                f"{label} inputs would overwrite the same output stem '{stem}': "
                f"{previous} and {path}"
            )
        seen[key] = path


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
    suffix = path.suffix or ".png"
    ok, encoded = cv2.imencode(suffix, image)
    if not ok:
        return False
    # Write the contiguous encoder buffer directly. ``tobytes()`` would keep a
    # second full encoded payload alive during atomic output on large images.
    atomic_write_bytes(path, encoded)
    return True


def _pil_exif_orientation(
    image: Image.Image,
    *,
    metadata_only: bool = False,
) -> int:
    """Return a valid EXIF orientation, optionally without loading pixels."""
    try:
        encoded_exif = image.info.get("exif")
        if encoded_exif:
            exif = Image.Exif()
            exif.load(encoded_exif)
        elif metadata_only:
            return 1
        else:
            exif = image.getexif()
        orientation = int(exif.get(_EXIF_ORIENTATION_TAG, 1))
    except (AttributeError, TypeError, ValueError):
        return 1
    return orientation if 1 <= orientation <= 8 else 1


def _read_exif_orientation(path: Path) -> int:
    """Inspect orientation for formats decoded through OpenCV."""
    try:
        with Image.open(path) as image:
            return _pil_exif_orientation(image, metadata_only=True)
    except (OSError, TypeError, ValueError):
        return 1


def _apply_exif_orientation_array(image: np.ndarray, orientation: int) -> np.ndarray:
    """Apply the eight EXIF orientation transforms to an HxW[xC] array."""
    if orientation == 2:
        oriented = np.flip(image, axis=1)
    elif orientation == 3:
        oriented = np.rot90(image, 2)
    elif orientation == 4:
        oriented = np.flip(image, axis=0)
    elif orientation == 5:
        oriented = np.swapaxes(image, 0, 1)
    elif orientation == 6:
        oriented = np.rot90(image, 3)
    elif orientation == 7:
        oriented = np.flip(np.swapaxes(image, 0, 1), axis=(0, 1))
    elif orientation == 8:
        oriented = np.rot90(image, 1)
    else:
        return image
    # Flips and rotations commonly return negative-stride views.  Downstream
    # OpenCV and encoders expect a conventional contiguous image buffer.
    return np.ascontiguousarray(oriented)


def _load_cv_preserve(path: Path) -> np.ndarray:
    image = cv_imread_unicode(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise OSError(f"Failed to load image: {path}")
    if image.ndim == 3 and image.shape[-1] == 3:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    elif image.ndim == 3 and image.shape[-1] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
    # OpenCV already normalizes TIFF orientation in the supported runtime, but
    # leaves PNG eXIf orientation untouched.  Applying both would rotate TIFF
    # inputs twice while omitting this step would mishandle oriented PNG files.
    if path.suffix.lower() == ".png":
        image = _apply_exif_orientation_array(image, _read_exif_orientation(path))
    return ensure_rgb_float(image)


def load_image(
    path: str | Path,
    *,
    decode_long_edge: int | None = None,
) -> np.ndarray:
    """读取图像，并整理成显示编码 sRGB 下的 float32 RGB。

    ``decode_long_edge`` is used only by the explicit scaled-fast path. JPEG
    decoders may then select a smaller DCT decode before the exact requested
    resize; quality and reduced-fast modes always decode the complete source.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in RAW_EXTENSIONS:
        raise ValueError(
            f"RAW/DNG input is not supported yet: {path}. "
            "Please export a display-referred TIFF/PNG/JPEG first. Future RAW input support is planned for DNG only, not vendor-private RAW formats."
        )
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported input format: {path.suffix or '(no extension)'}. "
            f"Supported formats are: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    if suffix in {".png", ".tif", ".tiff"}:
        return _load_cv_preserve(path)
    with Image.open(path) as image:
        if decode_long_edge is not None and suffix in {".jpg", ".jpeg"}:
            requested_edge = int(decode_long_edge)
            if requested_edge <= 0:
                raise ValueError("decode_long_edge must be positive or None.")
            width, height = image.size
            current_edge = max(width, height)
            if current_edge > requested_edge:
                scale = requested_edge / current_edge
                requested_size = (
                    max(1, round(width * scale)),
                    max(1, round(height * scale)),
                )
                image.draft("RGB", requested_size)
        image = ImageOps.exif_transpose(image)
        if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
            return ensure_rgb_float(np.asarray(image.convert("RGBA")))
        return ensure_rgb_float(np.asarray(image.convert("RGB")))


def probe_image_dimensions(path: str | Path) -> tuple[int, int]:
    """Read image width/height from metadata without decoding its pixel array."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in RAW_EXTENSIONS:
        raise ValueError(
            f"RAW/DNG input is not supported yet: {path}. "
            "Please export a display-referred TIFF/PNG/JPEG first."
        )
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported input format: {path.suffix or '(no extension)'}. "
            f"Supported formats are: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    try:
        with Image.open(path) as image:
            width, height = image.size
            orientation = (
                _pil_exif_orientation(image, metadata_only=True)
                if suffix not in {".tif", ".tiff"}
                else 1
            )
    except (OSError, ValueError) as exc:
        raise OSError(f"Failed to inspect image dimensions: {path}") from exc
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    if width <= 0 or height <= 0:
        raise ValueError(f"Image dimensions must be positive: {path}")
    return int(width), int(height)


def _encoding_tile_rows(image: np.ndarray, output: OutputConfig | None = None) -> int:
    height, width = image.shape[:2]
    if output is None:
        configured_rows = 512
        threshold = 8.0
    else:
        configured_rows = int(output.encode_tile_rows)
        threshold = float(output.encode_tile_threshold_megapixels)
    if configured_rows <= 0 or height * width / 1_000_000.0 < threshold:
        return height
    # Keep conversion scratch near four megapixels on very wide frames.
    bounded_rows = max(1, int(4_000_000 / max(width, 1)))
    return max(1, min(height, configured_rows, bounded_rows))


def _quantize_rgb_rows(
    image: np.ndarray,
    *,
    bit_depth: int,
    tile_rows: int,
    anti_banding_strength: float = 0.0,
    bgr: bool = False,
) -> np.ndarray:
    """Quantize with bounded float scratch while retaining one encoded array."""
    image = np.asarray(image)
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    scale = np.float32(65535.0 if bit_depth == 16 else 255.0)
    output = np.empty(image.shape, dtype=dtype)
    height = image.shape[0]
    for y_start in range(0, height, tile_rows):
        y_end = min(y_start + tile_rows, height)
        source = np.asarray(image[y_start:y_end], dtype=np.float32)
        if not np.isfinite(source).all():
            raise ValueError("Output image contains NaN or infinite values and will not be saved.")
        block = apply_output_antibanding_region(
            image,
            anti_banding_strength,
            y_start,
            y_end,
        )
        np.multiply(block, scale, out=block)
        np.rint(block, out=block)
        converted = block.astype(dtype)
        output[y_start:y_end] = converted[..., ::-1] if bgr else converted
    return output


def quantize_linear_rgb16(image: np.ndarray, *, bgr: bool = True) -> np.ndarray:
    """Convert linear RGB to uint16 with bounded row temporaries."""
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[-1] != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Linear RGB export must have non-empty HxWx3 shape, got {image.shape}.")
    return _quantize_rgb_rows(
        image,
        bit_depth=16,
        tile_rows=_encoding_tile_rows(image),
        bgr=bgr,
    )


def quantize_unit_float_rows(
    image: np.ndarray,
    *,
    bit_depth: int,
    tile_rows: int = 512,
    channel_order: tuple[int, ...] | None = None,
    label: str = "Image export",
) -> np.ndarray:
    """Quantize gray/RGB/RGBA unit floats without full-size float scratch."""
    image = np.asarray(image)
    if image.size == 0 or image.ndim not in {2, 3}:
        raise ValueError(f"{label} must be a non-empty HxW or HxWxC array, got {image.shape}.")
    if bit_depth not in {8, 16}:
        raise ValueError("Quantization bit depth must be 8 or 16.")
    tile_rows = max(1, int(tile_rows))
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    scale = np.float32(65535.0 if bit_depth == 16 else 255.0)
    output = np.empty(image.shape, dtype=dtype)
    for y_start in range(0, image.shape[0], tile_rows):
        y_end = min(y_start + tile_rows, image.shape[0])
        source = np.asarray(image[y_start:y_end], dtype=np.float32)
        if not np.isfinite(source).all():
            raise ValueError(f"{label} contains NaN or infinite values and will not be saved.")
        block = np.clip(source, 0.0, 1.0).astype(np.float32)
        np.multiply(block, scale, out=block)
        np.rint(block, out=block)
        converted = block.astype(dtype)
        if channel_order is not None:
            converted = converted[..., channel_order]
        output[y_start:y_end] = converted
    return output


def save_image(image: np.ndarray, path: str | Path, output: OutputConfig) -> None:
    """保存归一化 sRGB 图像，支持 8-bit 和部分 16-bit 格式。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[-1] != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Output image must have non-empty HxWx3 shape, got {image.shape}.")
    bit_depth = int(output.bit_depth)
    if bit_depth not in {8, 16}:
        raise ValueError(f"Unsupported output bit depth {bit_depth}; expected 8 or 16.")

    if bit_depth == 16:
        if path.suffix.lower() not in {".png", ".tif", ".tiff"}:
            raise ValueError("16-bit output is supported for PNG and TIFF paths only.")
        arr = _quantize_rgb_rows(
            image,
            bit_depth=16,
            tile_rows=_encoding_tile_rows(image, output),
            bgr=True,
        )
        if not cv_imwrite_unicode(path, arr):
            raise OSError(f"Failed to save image: {path}")
        return

    arr = _quantize_rgb_rows(
        image,
        bit_depth=8,
        tile_rows=_encoding_tile_rows(image, output),
        anti_banding_strength=getattr(output, "anti_banding_strength", 0.0),
    )
    pil_image = Image.fromarray(arr, mode="RGB")

    save_kwargs: dict[str, object] = {}
    if path.suffix.lower() in {".jpg", ".jpeg", ".webp"}:
        save_kwargs["quality"] = int(output.quality)
    if output.watermark_metadata:
        metadata_text = f"Created by {PROJECT_NAME}"
        suffix = path.suffix.lower()
        if suffix == ".png":
            pnginfo = PngInfo()
            pnginfo.add_text("Software", PROJECT_NAME)
            pnginfo.add_text("Comment", metadata_text)
            save_kwargs["pnginfo"] = pnginfo
        elif suffix in {".jpg", ".jpeg"}:
            save_kwargs["comment"] = metadata_text.encode("utf-8")
        elif suffix in {".tif", ".tiff"}:
            save_kwargs["tiffinfo"] = {270: metadata_text, 305: PROJECT_NAME}
    with atomic_output_path(path) as temporary:
        pil_image.save(temporary, **save_kwargs)


def save_image_bundle(
    image: np.ndarray,
    path: str | Path,
    output: OutputConfig,
    sidecar: dict | None = None,
    *,
    protected_paths: Iterable[str | Path] = (),
) -> Path:
    """Save an observed image and its optional sidecar as one generation."""
    target = Path(path)
    resolved_target = target.resolve()
    for protected in protected_paths:
        if resolved_target == Path(protected).resolve():
            raise ValueError(f"Observed image output must not overwrite its source: {target}")
    targets = [target]
    sidecar_path: Path | None = None
    if sidecar is not None:
        sidecar_path = target.with_suffix(target.suffix + ".json")
        targets.append(sidecar_path)
    with atomic_path_set(targets):
        save_image(image, target, output)
        if sidecar_path is not None:
            atomic_write_json(sidecar_path, sidecar)
    return target


def iter_images(path: str | Path) -> list[Path]:
    """从单个文件或文件夹中找出支持的图像文件。"""
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() in SUPPORTED_EXTENSIONS else []
    if not path.exists():
        return []
    if not path.is_dir():
        return []
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS
    )
