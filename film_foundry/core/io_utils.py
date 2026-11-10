"""图像读取与保存工具。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

try:
    from PIL import ImageCms
except ImportError:  # pragma: no cover - Pillow is a required dependency
    ImageCms = None

try:
    import lcms2
except ImportError:  # pragma: no cover - declared runtime dependency
    lcms2 = None

from film_foundry.core.antibanding import apply_output_antibanding_region
from film_foundry.core.atomic_io import (
    atomic_output_path,
    atomic_path_set,
    atomic_write_bytes,
    atomic_write_json,
)
from film_foundry.core.color import ensure_rgb_float
from film_foundry.core.provenance import PROJECT_NAME
from film_foundry.model.config import OutputConfig


SUPPORTED_EXTENSIONS = {".dng", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
RAW_EXTENSIONS = {".dng", ".cr2", ".cr3", ".nef", ".arw", ".rw2", ".orf", ".raf", ".pef"}
_EXIF_ORIENTATION_TAG = 274
INPUT_ICC_POLICIES = ("apply", "ignore")
WORKING_INPUT_SPACE = "srgb_iec61966_2_1"
_SRGB_PROFILE = None
_LCMS2_SRGB_PROFILE = None
_ICC_LUT_GRID = 17


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


_TIFF_SAMPLE_MIN_PIXELS = 8_000_000


def _tiff_write_params(image: np.ndarray) -> list[int]:
    """Avoid expensive LZW expansion on large, noisy 16-bit image exports.

    A bounded set of original rows estimates transport compression only. It
    never changes pixel data, precision, alpha, or the TIFF container contract.
    """
    from .execution_topology import reference_execution_enabled
    if (reference_execution_enabled() or image.dtype != np.uint16
            or image.ndim != 3 or image.shape[2] not in (3, 4)
            or image.shape[0] * image.shape[1] < _TIFF_SAMPLE_MIN_PIXELS):
        return []
    rows = np.linspace(0, image.shape[0] - 1, min(16, image.shape[0]), dtype=np.intp)
    sample = np.ascontiguousarray(image[rows])
    try:
        ok, encoded = cv2.imencode(".tiff", sample)
    except cv2.error:
        return []  # Retain the established encoder path on other builds.
    if ok and encoded.nbytes >= sample.nbytes * 0.9:
        return [cv2.IMWRITE_TIFF_COMPRESSION, 1]  # Baseline uncompressed TIFF.
    return []


def cv_imwrite_unicode(path: str | Path, image: np.ndarray) -> bool:
    """用 OpenCV 编码图像，同时兼容 Windows 中文路径。"""
    path = Path(path)
    suffix = path.suffix or ".png"
    params = _tiff_write_params(image) if suffix.lower() in {".tif", ".tiff"} else []
    ok, encoded = cv2.imencode(suffix, image, params) if params else cv2.imencode(suffix, image)
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


def _load_cv_native(path: Path) -> np.ndarray:
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
    return image


def _load_cv_preserve(path: Path) -> np.ndarray:
    return ensure_rgb_float(_load_cv_native(path))


def _normalize_input_icc_policy(value: str | None) -> str:
    policy = str(value or "apply").strip().lower()
    if policy not in INPUT_ICC_POLICIES:
        raise ValueError(
            f"input_icc_policy must be one of {', '.join(INPUT_ICC_POLICIES)}; got {value!r}."
        )
    return policy


def _input_color_record(
    *,
    source: str,
    policy: str,
    bit_depth: int,
    profile_description: str | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "working_space": WORKING_INPUT_SPACE,
        "encoding": "display_referred",
        "policy": policy,
        "source": source,
        "bit_depth": int(bit_depth),
    }
    if profile_description:
        payload["profile_description"] = profile_description
    if reason:
        payload["reason"] = reason
    return payload


def _read_embedded_color_tags(path: Path) -> tuple[bytes | None, object | None]:
    try:
        with Image.open(path) as image:
            icc = image.info.get("icc_profile")
            srgb_chunk = image.info.get("srgb")
    except (OSError, TypeError, ValueError, SyntaxError):
        return None, None
    if isinstance(icc, (bytes, bytearray)) and icc:
        return bytes(icc), srgb_chunk
    return None, srgb_chunk


def _native_channel_bit_depth(array: np.ndarray) -> int:
    if np.issubdtype(array.dtype, np.integer):
        return 16 if np.iinfo(array.dtype).max > 255 else 8
    return 16 if array.dtype == np.float32 or array.dtype == np.float64 else 8


def _srgb_cms_profile():
    global _SRGB_PROFILE
    if ImageCms is None:
        return None
    if _SRGB_PROFILE is None:
        _SRGB_PROFILE = ImageCms.createProfile("sRGB")
    return _SRGB_PROFILE


def _profile_description(profile) -> str | None:
    if ImageCms is None:
        return None
    try:
        description = str(ImageCms.getProfileDescription(profile) or "").strip()
    except Exception:
        return None
    return description or None


def _profile_is_srgb(profile) -> bool:
    if ImageCms is None:
        return False
    try:
        def color_tags(value):
            data = value.tobytes() if hasattr(value, "tobytes") else ImageCms.ImageCmsProfile(value).tobytes()
            count = int.from_bytes(data[128:132], "big")
            tags = {}
            for index in range(count):
                entry = 132 + 12 * index
                name = data[entry:entry + 4]
                offset = int.from_bytes(data[entry + 4:entry + 8], "big")
                size = int.from_bytes(data[entry + 8:entry + 12], "big")
                if name not in {b"desc", b"cprt"}:
                    tags[name] = data[offset:offset + size]
            return data[12:24], tags

        return color_tags(profile) == color_tags(_srgb_cms_profile())
    except Exception:
        return False


def _open_rgb_icc_profile(icc_bytes: bytes):
    if ImageCms is None:
        raise RuntimeError("lcms_unavailable")
    profile = ImageCms.getOpenProfile(BytesIO(icc_bytes))
    cms = profile.profile if hasattr(profile, "profile") else profile
    color_space = str(getattr(cms, "xcolor_space", "RGB ") or "RGB ")
    if color_space.strip() not in {"RGB", "RGB "}:
        raise RuntimeError(f"unsupported_color_space:{color_space.strip() or 'unknown'}")
    return profile, cms


def _cms_transform_flags():
    return ImageCms.Flags.HIGHRESPRECALC | ImageCms.Flags.BLACKPOINTCOMPENSATION


def _apply_icc_uint8(rgb: np.ndarray, profile) -> np.ndarray:
    image = Image.fromarray(np.ascontiguousarray(rgb), mode="RGB")
    converted = ImageCms.profileToProfile(
        image,
        profile,
        _srgb_cms_profile(),
        outputMode="RGB",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        flags=_cms_transform_flags(),
    )
    return np.asarray(converted, dtype=np.uint8)


def _apply_icc_float_native(rgb: np.ndarray, icc_bytes: bytes) -> np.ndarray:
    """Apply the complete RGB ICC transform through LittleCMS float pixels."""
    global _LCMS2_SRGB_PROFILE
    if lcms2 is None:
        raise RuntimeError("lcms2_float_unavailable")
    if _LCMS2_SRGB_PROFILE is None:
        _LCMS2_SRGB_PROFILE = lcms2.Profile("sRGB")
    source_profile = lcms2.Profile(buffer=icc_bytes)
    transform = lcms2.Transform(
        source_profile,
        "RGB_FLT",
        _LCMS2_SRGB_PROFILE,
        "RGB_FLT",
        intent="RELATIVE_COLORIMETRIC",
        flags="HIGHRESPRECALC,BLACKPOINTCOMPENSATION",
    )
    source = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    output = np.empty_like(source, dtype=np.float32)
    tile_rows = 128
    for y_start in range(0, source.shape[0], tile_rows):
        y_end = min(y_start + tile_rows, source.shape[0])
        output[y_start:y_end] = transform.apply(
            np.ascontiguousarray(source[y_start:y_end])
        )
    return np.clip(output, 0.0, 1.0).astype(np.float32, copy=False)


def _build_icc_srgb_lut(profile) -> np.ndarray:
    grid = _ICC_LUT_GRID
    axis = np.linspace(0, 255, grid, dtype=np.uint8)
    red, green, blue = np.meshgrid(axis, axis, axis, indexing="ij")
    cube = np.stack((red, green, blue), axis=-1)
    flat = np.ascontiguousarray(cube.reshape(grid * grid, grid, 3))
    converted = _apply_icc_uint8(flat, profile)
    return converted.astype(np.float32).reshape(grid, grid, grid, 3) / np.float32(255.0)


def _interpolate_rgb_lut(rgb: np.ndarray, lut: np.ndarray) -> np.ndarray:
    grid_max = lut.shape[0] - 1
    # Pillow's RGB transform samples integer code values. Interpolate against
    # those actual nodes, not an ideal uniform grid (255 / 16 is not integral).
    axis = np.linspace(0, 255, grid_max + 1, dtype=np.uint8).astype(np.float32) / np.float32(255.0)
    values = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
    origin = np.searchsorted(axis, values, side="right").astype(np.int32) - 1
    np.clip(origin, 0, grid_max - 1, out=origin)
    frac = (values - axis[origin]) / (axis[origin + 1] - axis[origin])
    r0, g0, b0 = origin[..., 0], origin[..., 1], origin[..., 2]
    r1, g1, b1 = r0 + 1, g0 + 1, b0 + 1
    fr, fg, fb = frac[..., 0:1], frac[..., 1:2], frac[..., 2:3]
    c000 = lut[r0, g0, b0]
    c001 = lut[r0, g0, b1]
    c010 = lut[r0, g1, b0]
    c011 = lut[r0, g1, b1]
    c100 = lut[r1, g0, b0]
    c101 = lut[r1, g0, b1]
    c110 = lut[r1, g1, b0]
    c111 = lut[r1, g1, b1]
    c00 = c000 + (c001 - c000) * fb
    c01 = c010 + (c011 - c010) * fb
    c10 = c100 + (c101 - c100) * fb
    c11 = c110 + (c111 - c110) * fb
    c0 = c00 + (c01 - c00) * fg
    c1 = c10 + (c11 - c10) * fg
    return c0 + (c1 - c0) * fr


def _apply_icc_float_tiled(rgb: np.ndarray, profile) -> np.ndarray:
    lut = _build_icc_srgb_lut(profile)
    rgb = np.asarray(rgb, dtype=np.float32)
    output = np.empty_like(rgb, dtype=np.float32)
    tile_rows = 128
    for y_start in range(0, rgb.shape[0], tile_rows):
        y_end = min(y_start + tile_rows, rgb.shape[0])
        output[y_start:y_end] = _interpolate_rgb_lut(rgb[y_start:y_end], lut)
    return output


def _convert_rgb_to_working_srgb(
    rgb: np.ndarray,
    icc_bytes: bytes | None,
    *,
    policy: str,
    png_srgb_chunk: object | None = None,
) -> tuple[np.ndarray, dict[str, object]]:
    bit_depth = _native_channel_bit_depth(rgb)
    if policy == "ignore":
        source = "assumed_srgb"
        reason = None
        description = None
        if icc_bytes:
            source = "icc_ignored"
            reason = "policy_ignore"
            try:
                profile, _cms = _open_rgb_icc_profile(icc_bytes)
                description = _profile_description(profile)
            except Exception:
                description = None
        return ensure_rgb_float(rgb), _input_color_record(
            source=source,
            policy=policy,
            bit_depth=bit_depth,
            profile_description=description,
            reason=reason,
        )
    if not icc_bytes:
        source = "tagged_srgb" if png_srgb_chunk is not None else "assumed_srgb"
        return ensure_rgb_float(rgb), _input_color_record(
            source=source,
            policy=policy,
            bit_depth=bit_depth,
        )
    try:
        profile, cms = _open_rgb_icc_profile(icc_bytes)
    except Exception as exc:
        reason = str(exc).strip() or "unreadable_profile"
        if "lcms_unavailable" in reason:
            reason = "lcms_unavailable"
        elif not reason.startswith("unsupported_"):
            reason = "unreadable_profile"
        return ensure_rgb_float(rgb), _input_color_record(
            source="icc_ignored",
            policy=policy,
            bit_depth=bit_depth,
            reason=reason,
        )
    description = _profile_description(profile)
    if _profile_is_srgb(profile):
        return ensure_rgb_float(rgb), _input_color_record(
            source="embedded_srgb",
            policy=policy,
            bit_depth=bit_depth,
            profile_description=description,
        )
    try:
        if rgb.dtype == np.uint8 and rgb.ndim == 3 and rgb.shape[-1] == 3:
            converted = _apply_icc_uint8(rgb, profile)
            working = ensure_rgb_float(converted)
            transform = "lcms_rgb8"
        else:
            source = ensure_rgb_float(rgb)
            try:
                working = _apply_icc_float_native(source, icc_bytes)
                transform = "lcms_float32"
            except RuntimeError as exc:
                if str(exc) != "lcms2_float_unavailable":
                    raise
                # A source install missing its declared float binding retains
                # the old, explicitly labelled approximation instead of
                # silently treating valid embedded color as working sRGB.
                working = _apply_icc_float_tiled(source, profile)
                transform = "lcms_rgb8_lut17_trilinear"
            working = np.clip(working, 0.0, 1.0).astype(np.float32, copy=False)
    except Exception:
        return ensure_rgb_float(rgb), _input_color_record(
            source="icc_ignored",
            policy=policy,
            bit_depth=bit_depth,
            profile_description=description,
            reason="unsupported_transform",
        )
    record = _input_color_record(
        source="converted_to_srgb",
        policy=policy,
        bit_depth=bit_depth,
        profile_description=description,
    )
    record["transform"] = transform
    return working, record


def load_image_with_color(
    path: str | Path,
    *,
    decode_long_edge: int | None = None,
    input_icc_policy: str = "apply",
) -> tuple[np.ndarray, dict[str, object]]:
    """Load a display-referred working-sRGB image and the color-contract record."""
    path = Path(path)
    suffix = path.suffix.lower()
    policy = _normalize_input_icc_policy(input_icc_policy)
    if suffix == ".dng":
        from .source_image import load_formation_image, exposure_preview
        pixels, record = load_formation_image(path)
        return exposure_preview(pixels, "linear_rgb"), {**record, "encoding": "srgb", "preview_only": True}
    if suffix in RAW_EXTENSIONS:
        raise ValueError(
            f"Only DNG RAW input is supported: {path}. "
            "Please export a display-referred TIFF/PNG/JPEG first. Vendor-private RAW formats are not supported."
        )
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported input format: {path.suffix or '(no extension)'}. "
            f"Supported formats are: {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )
    icc_bytes, png_srgb_chunk = _read_embedded_color_tags(path)
    if suffix in {".png", ".tif", ".tiff"}:
        native = _load_cv_native(path)
        if np.issubdtype(native.dtype,np.floating):
            low=float(np.min(native));high=float(np.max(native))
            if not np.isfinite(low) or not np.isfinite(high) or low<0 or high>1:
                raise ValueError('Floating-point PNG/TIFF input must contain normalized display RGB in [0, 1]. HDR float TIFF is not supported; use the supported DNG or Ultra HDR JPEG capture path.')
        return _convert_rgb_to_working_srgb(
            native,
            icc_bytes,
            policy=policy,
            png_srgb_chunk=png_srgb_chunk,
        )
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
            native = np.asarray(image.convert("RGBA"))
        else:
            native = np.asarray(image.convert("RGB"))
        if icc_bytes is None:
            icc = image.info.get("icc_profile")
            if isinstance(icc, (bytes, bytearray)) and icc:
                icc_bytes = bytes(icc)
    return _convert_rgb_to_working_srgb(
        native,
        icc_bytes,
        policy=policy,
        png_srgb_chunk=png_srgb_chunk,
    )


def load_image(
    path: str | Path,
    *,
    decode_long_edge: int | None = None,
    input_icc_policy: str = "apply",
) -> np.ndarray:
    """读取图像，并整理成显示编码工作 sRGB 下的 float32 RGB。

    无嵌入剖面时按历史行为假定 sRGB。有效的非 sRGB ICC 会先转换到
    IEC 61966-2-1 sRGB，再交给形成路径的 ``srgb_to_linear``。损坏或不支持
    的剖面会被忽略并记录来源，而不是静默套用错误变换。

    ``decode_long_edge`` is used only by the explicit scaled-fast path. JPEG
    decoders may then select a smaller DCT decode before the exact requested
    resize; quality and reduced-fast modes always decode the complete source.
    """
    image, _record = load_image_with_color(
        path,
        decode_long_edge=decode_long_edge,
        input_icc_policy=input_icc_policy,
    )
    return image


def probe_image_dimensions(path: str | Path) -> tuple[int, int]:
    """Read image width/height from metadata without decoding its pixel array."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".dng":
        from .source_image import dng_dimensions
        return dng_dimensions(path)
    if suffix in RAW_EXTENSIONS:
        raise ValueError(
            f"Only DNG RAW input is supported: {path}. "
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
    workers: int = 1,
) -> np.ndarray:
    """Quantize with bounded float scratch while retaining one encoded array."""
    image = np.asarray(image)
    dtype = np.uint16 if bit_depth == 16 else np.uint8
    scale = np.float32(65535.0 if bit_depth == 16 else 255.0)
    output = np.empty(image.shape, dtype=dtype)
    height = image.shape[0]
    def quantize_rows(y_start, y_end):
        source = np.asarray(image[y_start:y_end], dtype=np.float32)
        minimum = float(np.min(source))
        maximum = float(np.max(source))
        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError("Output image contains NaN or infinite values and will not be saved.")
        block = apply_output_antibanding_region(
            image,
            anti_banding_strength,
            y_start,
            y_end,
        )
        np.multiply(block, scale, out=block)
        np.rint(block, out=block)
        target = output[y_start:y_end]
        np.copyto(
            target,
            block[..., ::-1] if bgr else block,
            casting="unsafe",
        )
    from .material_parallel import run_rows
    run_rows(height, image.shape[1], quantize_rows,
             chunk_pixels=max(1, tile_rows)*image.shape[1], workers=workers)
    return output


def quantize_linear_rgb16(image: np.ndarray, *, bgr: bool = True, workers: int = 1) -> np.ndarray:
    """Convert linear RGB to uint16 with bounded row temporaries."""
    image = np.asarray(image)
    if image.ndim != 3 or image.shape[-1] != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Linear RGB export must have non-empty HxWx3 shape, got {image.shape}.")
    return _quantize_rgb_rows(
        image,
        bit_depth=16,
        tile_rows=_encoding_tile_rows(image),
        bgr=bgr,
        workers=workers,
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
        minimum = float(np.min(source))
        maximum = float(np.max(source))
        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError(f"{label} contains NaN or infinite values and will not be saved.")
        block = np.clip(source, 0.0, 1.0).astype(np.float32)
        np.multiply(block, scale, out=block)
        np.rint(block, out=block)
        target = output[y_start:y_end]
        if channel_order is None:
            np.copyto(target, block, casting="unsafe")
        else:
            if len(channel_order) != block.shape[-1]:
                raise ValueError(
                    f"{label} channel_order must contain {block.shape[-1]} entries."
                )
            for target_channel, source_channel in enumerate(channel_order):
                np.copyto(
                    target[..., target_channel],
                    block[..., source_channel],
                    casting="unsafe",
                )
    return output


def png_compression_level() -> int:
    """Use fast lossless compression; reference keeps Pillow's former level 6.

    This changes encoding effort and file size, never quantization or pixels.
    """
    from .execution_topology import reference_execution_enabled
    return 6 if reference_execution_enabled() else 1


def save_image(image: np.ndarray, path: str | Path, output: OutputConfig, *, workers: int = 1, apply_orientation: bool = False) -> None:
    """保存归一化 sRGB 图像，支持 8-bit 和部分 16-bit 格式。"""
    if apply_orientation:
        from .image_orientation import rotate_output
        image=rotate_output(image,output.rotation_quarters)
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
            workers=workers,
        )
        if not cv_imwrite_unicode(path, arr):
            raise OSError(f"Failed to save image: {path}")
        return

    arr = _quantize_rgb_rows(
        image,
        bit_depth=8,
        tile_rows=_encoding_tile_rows(image, output),
        anti_banding_strength=getattr(output, "anti_banding_strength", 0.0),
        workers=workers,
    )
    pil_image = Image.fromarray(arr, mode="RGB")

    save_kwargs: dict[str, object] = {}
    if path.suffix.lower() == ".png":
        save_kwargs["compress_level"] = png_compression_level()
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
        save_image(image, target, output,apply_orientation=True)
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
