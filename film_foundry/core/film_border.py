"""Film rebate, sprocket-hole layout and film-plane edge-imprint exposure.

Sprocket and rebate geometry is resolved only; it is not rasterized and does
not expand the developed medium. Edge imprint styles other than ``none`` add
a super-bright film-plane flash into linearized exposure after MTF. The
scanner ``include_clear_base_border`` path remains an observation-canvas
feature and must not be reused as sprocket geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from film_foundry.model.config import FilmBorderConfig


FILM_BORDER_PIPELINE_STATUS = "interface_only"
FILM_BORDER_FORMAT_KEYS = ("135", "120", "110", "half_frame")
FILM_BORDER_SPROCKET_ROWS = ("none", "top", "bottom", "both")
FILM_BORDER_IMPRINT_STYLES = ("none", "laser_dot", "optical_amber", "lab_edge")
FILM_BORDER_IMPRINT_SIDES = ("top", "bottom")

_DEFAULT_SPROCKET_COUNTS = {
    "135": 8,
    "half_frame": 4,
    "110": 6,
    "120": 0,
}
_EDGE_IMPRINT_EXPOSURE = {
    "laser_dot": (np.array([1.00, 0.16, 0.05], dtype=np.float32), 72.0),
    "optical_amber": (np.array([1.00, 0.55, 0.12], dtype=np.float32), 56.0),
    "lab_edge": (np.array([0.55, 1.00, 0.48], dtype=np.float32), 48.0),
}
_IMPRINT_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyh.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)


@dataclass(frozen=True, slots=True)
class SprocketHoleSpec:
    """One sprocket opening in normalized canvas coordinates (x0, y0, x1, y1)."""

    normalized_rect: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class FilmBorderImprintSpec:
    """Laser/optical/lab edge marking placed on the rebate, not the image gate."""

    style: str
    side: str
    text: str
    time: str
    origin_xy: tuple[float, float]
    height_ratio: float


@dataclass(frozen=True, slots=True)
class FilmBorderLayout:
    """Resolved rebate geometry. Contains no pixels and is not pipeline-applied."""

    format_key: str
    canvas_size: tuple[int, int]
    image_rect: tuple[int, int, int, int]
    sprocket_holes: tuple[SprocketHoleSpec, ...]
    imprint: FilmBorderImprintSpec | None
    pipeline_status: str = FILM_BORDER_PIPELINE_STATUS


def validate_film_border_config(config: FilmBorderConfig) -> FilmBorderConfig:
    format_key = str(config.format_key).strip().lower()
    if format_key not in FILM_BORDER_FORMAT_KEYS:
        raise ValueError(
            "film_border.format_key must be one of: "
            + ", ".join(FILM_BORDER_FORMAT_KEYS)
        )
    sprocket_rows = str(config.sprocket_rows).strip().lower()
    if sprocket_rows not in FILM_BORDER_SPROCKET_ROWS:
        raise ValueError(
            "film_border.sprocket_rows must be one of: "
            + ", ".join(FILM_BORDER_SPROCKET_ROWS)
        )
    imprint_style = str(config.imprint_style).strip().lower()
    if imprint_style not in FILM_BORDER_IMPRINT_STYLES:
        raise ValueError(
            "film_border.imprint_style must be one of: "
            + ", ".join(FILM_BORDER_IMPRINT_STYLES)
        )
    imprint_side = str(config.imprint_side).strip().lower()
    if imprint_side not in FILM_BORDER_IMPRINT_SIDES:
        raise ValueError(
            "film_border.imprint_side must be one of: "
            + ", ".join(FILM_BORDER_IMPRINT_SIDES)
        )
    rebate = float(config.rebate_height_ratio)
    if not math.isfinite(rebate) or not 0.02 <= rebate <= 0.80:
        raise ValueError("film_border.rebate_height_ratio must be between 0.02 and 0.80.")
    sprocket_count = config.sprocket_count
    if sprocket_count is not None:
        try:
            sprocket_count = int(sprocket_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("film_border.sprocket_count must be an integer or None.") from exc
        if sprocket_count < 0:
            raise ValueError("film_border.sprocket_count must be non-negative.")
    return FilmBorderConfig(
        enabled=bool(config.enabled),
        format_key=format_key,
        sprocket_rows=sprocket_rows,
        rebate_height_ratio=rebate,
        sprocket_count=sprocket_count,
        imprint_style=imprint_style,
        imprint_side=imprint_side,
        imprint_text=str(config.imprint_text or ""),
        imprint_time=str(config.imprint_time or ""),
    )


def resolve_film_border_layout(
    config: FilmBorderConfig,
    image_shape: tuple[int, ...],
) -> FilmBorderLayout | None:
    """Return rebate/sprocket/imprint geometry for a formed image gate.

    A disabled config returns None. The layout never expands or rewrites the
    developed medium; callers must not treat it as an applied optical field.
    """
    validated = validate_film_border_config(config)
    if not validated.enabled:
        return None
    if len(image_shape) < 2:
        raise ValueError("film border layout requires an image height and width.")
    height = int(image_shape[0])
    width = int(image_shape[1])
    if height <= 0 or width <= 0:
        raise ValueError("film border layout requires a positive image size.")

    rebate = max(1, int(round(height * float(validated.rebate_height_ratio))))
    canvas_h = height + rebate * 2
    canvas_w = width
    image_rect = (0, rebate, width, height)
    holes = _sprocket_holes(validated, canvas_w, canvas_h, rebate)
    imprint = _imprint_spec(validated, canvas_w, canvas_h, rebate)
    return FilmBorderLayout(
        format_key=validated.format_key,
        canvas_size=(canvas_w, canvas_h),
        image_rect=image_rect,
        sprocket_holes=holes,
        imprint=imprint,
        pipeline_status=FILM_BORDER_PIPELINE_STATUS,
    )


def _sprocket_holes(
    config: FilmBorderConfig,
    canvas_w: int,
    canvas_h: int,
    rebate: int,
) -> tuple[SprocketHoleSpec, ...]:
    rows = str(config.sprocket_rows)
    if rows == "none":
        return ()
    count = config.sprocket_count
    if count is None:
        count = int(_DEFAULT_SPROCKET_COUNTS.get(config.format_key, 8))
    if count <= 0:
        return ()
    hole_w = max(2, int(round(canvas_w / (count * 2.35))))
    hole_h = max(2, int(round(rebate * 0.62)))
    margin_x = hole_w * 0.55
    usable = max(float(canvas_w) - 2.0 * margin_x - float(hole_w), 1.0)
    specs: list[SprocketHoleSpec] = []
    row_ys: list[float] = []
    if rows in {"top", "both"}:
        row_ys.append((rebate - hole_h) * 0.5)
    if rows in {"bottom", "both"}:
        row_ys.append(float(canvas_h - rebate) + (rebate - hole_h) * 0.5)
    for top in row_ys:
        for index in range(count):
            left = margin_x + usable * (float(index) / float(max(count - 1, 1)))
            specs.append(
                SprocketHoleSpec(
                    normalized_rect=(
                        float(left / canvas_w),
                        float(top / canvas_h),
                        float((left + hole_w) / canvas_w),
                        float((top + hole_h) / canvas_h),
                    )
                )
            )
    return tuple(specs)


def _imprint_spec(
    config: FilmBorderConfig,
    canvas_w: int,
    canvas_h: int,
    rebate: int,
) -> FilmBorderImprintSpec | None:
    style = str(config.imprint_style)
    if style == "none":
        return None
    side = str(config.imprint_side)
    height_ratio = min(0.12, max(0.018, float(rebate) / float(max(canvas_h, 1)) * 0.55))
    x = 0.08
    if side == "top":
        y = (float(rebate) / float(canvas_h)) * 0.42
    else:
        y = 1.0 - (float(rebate) / float(canvas_h)) * 0.58
    return FilmBorderImprintSpec(
        style=style,
        side=side,
        text=str(config.imprint_text),
        time=str(config.imprint_time),
        origin_xy=(x, y),
        height_ratio=height_ratio,
    )


def edge_imprint_label(config: FilmBorderConfig) -> str:
    text = str(config.imprint_text or "").strip()
    time = str(config.imprint_time or "").strip()
    return "  ".join(part for part in (text, time) if part)


def edge_imprint_is_active(config: FilmBorderConfig) -> bool:
    style = str(config.imprint_style or "none").strip().lower()
    return style != "none" and bool(edge_imprint_label(config))


def apply_edge_imprint_to_linear(
    image_linear: np.ndarray,
    border_config: FilmBorderConfig,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Add a film-plane LED/lab flash as extra linear exposure.

    This is formation-side light, not an RGB watermark or scan overlay.
    Empty text and time, or style ``none``, keep a strict identity.
    """
    image_linear = np.asarray(image_linear, dtype=np.float32)
    validated = validate_film_border_config(border_config)
    if not edge_imprint_is_active(validated):
        return image_linear, None
    height, width = image_linear.shape[:2]
    if height <= 0 or width <= 0:
        return image_linear, None
    mask = _render_imprint_mask((height, width), validated)
    if mask is None or float(np.max(mask)) <= 1e-8:
        return image_linear, None
    color, strength = _EDGE_IMPRINT_EXPOSURE[str(validated.imprint_style)]
    flashed = image_linear.copy()
    flashed += mask[..., None] * (color * float(strength))
    return flashed, mask


def _render_imprint_mask(
    shape: tuple[int, int],
    config: FilmBorderConfig,
) -> np.ndarray | None:
    height, width = shape
    label = edge_imprint_label(config)
    if not label:
        return None
    font_size = max(8, int(round(min(height, width) * 0.045)))
    font = _imprint_font(font_size)
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = max(1, int(bbox[2] - bbox[0]))
    text_h = max(1, int(bbox[3] - bbox[1]))
    x = int(round(width * 0.045)) - int(bbox[0])
    margin_y = max(2, int(round(height * 0.014)))
    if str(config.imprint_side) == "top":
        y = margin_y - int(bbox[1])
    else:
        y = height - margin_y - text_h - int(bbox[1])
    y = int(np.clip(y, -int(bbox[1]), max(0, height - text_h)))
    style = str(config.imprint_style)
    draw.text((x, y), label, font=font, fill=255)
    if style == "lab_edge":
        underline_y = int(np.clip(y + int(bbox[3]) + 1, 0, height - 1))
        draw.line((x, underline_y, x + text_w, underline_y), fill=200, width=1)
    glyph = np.asarray(canvas, dtype=np.float32)
    glyph *= np.float32(1.0 / 255.0)
    if style == "laser_dot":
        mask = _laser_dot_mask(glyph, font_size)
    elif style == "optical_amber":
        sigma = max(0.65, float(font_size) / 16.0)
        mask = cv2.GaussianBlur(glyph, (0, 0), sigmaX=sigma, sigmaY=sigma)
    else:
        mask = glyph
    np.clip(mask, 0.0, 1.0, out=mask)
    return mask.astype(np.float32, copy=False)


def _laser_dot_mask(glyph: np.ndarray, font_size: int) -> np.ndarray:
    height, width = glyph.shape
    block = max(2, int(round(font_size / 6.0)))
    yy = np.arange(block // 2, height, block, dtype=np.int32)
    xx = np.arange(block // 2, width, block, dtype=np.int32)
    if yy.size == 0 or xx.size == 0:
        return glyph
    sampled = glyph[np.ix_(yy, xx)]
    mask = np.zeros_like(glyph, dtype=np.float32)
    on = sampled > 0.08
    if not np.any(on):
        return glyph
    ys, xs = np.nonzero(on)
    mask[yy[ys], xx[xs]] = sampled[ys, xs]
    kernel = np.ones((2, 2), dtype=np.uint8)
    return cv2.dilate(mask, kernel, iterations=1)


def _imprint_font(size: int):
    for path in _IMPRINT_FONT_CANDIDATES:
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size=int(size))
        except OSError:
            continue
    return ImageFont.load_default()

