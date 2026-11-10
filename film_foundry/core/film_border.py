"""Film-edge geometry and independent in-picture film-plane imprint exposure.

Actual rebate formation lives in formed_film_border; scanner reference borders
remain a separate observation-canvas option.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from film_foundry.model.config import FilmBorderConfig
from film_foundry.contracts.edge_marks import normalize_edge_pattern


FILM_BORDER_PIPELINE_STATUS = "formed_rebate_reference_v1"
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
    "laser_dot": (np.array([1.00, 0.16, 0.05], dtype=np.float32), 9.0),
    "optical_amber": (np.array([1.00, 0.55, 0.12], dtype=np.float32), 4.5),
    "lab_edge": (np.array([0.55, 1.00, 0.48], dtype=np.float32), 6.0),
}
_IMPRINT_LATIN_FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\consola.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
)
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
    """Legacy layout slot; actual in-picture exposure uses its own image gate.

    Retained for old layout consumers, never rasterized into factory rebates.
    """

    style: str
    side: str
    text: str
    time: str
    origin_xy: tuple[float, float]
    height_ratio: float


@dataclass(frozen=True, slots=True)
class FactoryEdgeSpec:
    """Factory-stencil slot outside the perforations."""

    side: str
    normalized_rect: tuple[float, float, float, float]
    pattern_code: str
    flip: bool


@dataclass(frozen=True, slots=True)
class FilmBorderLayout:
    """Resolved rebate geometry consumed by the formation-side rasterizer."""

    format_key: str
    canvas_size: tuple[int, int]
    image_rect: tuple[int, int, int, int]
    sprocket_holes: tuple[SprocketHoleSpec, ...]
    imprint: FilmBorderImprintSpec | None
    pipeline_status: str = FILM_BORDER_PIPELINE_STATUS
    factory_edges: tuple[FactoryEdgeSpec, ...] = ()


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
    if config.factory_mark_type not in {"hard", "laser"}:
        raise ValueError("film_border.factory_mark_type must be hard or laser.")
    spread = float(config.factory_mark_spread)
    if config.factory_layout not in {'legacy','text_code','combined','text_only','code_text','cinema','compact','roll_markings'}:
        raise ValueError('Unknown factory lettering layout')
    if config.factory_barcode not in {'legacy','code128c','none','blocks','staggered_blocks','paired_blocks'}:
        raise ValueError('Unknown factory barcode')
    if isinstance(config.factory_barcode_seed,bool) or not isinstance(config.factory_barcode_seed,int) or not 0<=config.factory_barcode_seed<=2147483647:
        raise ValueError('Factory barcode seed must be an integer within 0–2147483647')
    if not math.isfinite(config.loading_offset) or not -.5<=config.loading_offset<=.5:
        raise ValueError('Loading offset must be within -0.5 to 0.5 frames')
    if config.advance_marker_style not in {'legacy','arrow','double_arrow','chevron','triangle','outline_triangle','none'}:
        raise ValueError('Unknown advance marker style')
    positions={}
    for name in ('factory_offset_top','factory_offset_bottom','advance_marker_offset'):
        value=tuple(float(v) for v in getattr(config,name))
        if len(value)!=2 or any(not math.isfinite(v) or not -1<=v<=1 for v in value):
            raise ValueError(name+' requires two offsets within -1 to 1')
        positions[name]=value
    if not math.isfinite(config.factory_exposure_ev) or not -4<=config.factory_exposure_ev<=4:
        raise ValueError('Factory exposure must be within -4 to +4 EV')
    if not math.isfinite(config.factory_exposure_variation) or not 0<=config.factory_exposure_variation<=1:
        raise ValueError('Factory exposure variation must be within 0–1')
    if config.advance_direction not in {'left','right'}:
        raise ValueError('Advance direction must be left or right')
    for value,limit,name in ((config.factory_batch,4095,'factory_batch'),(config.frame_number,9999,'frame_number')):
        if value is not None and (isinstance(value,bool) or int(value)!=value or not 0<=value<=limit):
            raise ValueError('Invalid film_border.'+name)
    if config.imprint_font not in {'legacy','seven_segment','dot_matrix'}:
        raise ValueError('Unknown imprint font')
    position=config.imprint_position_xy
    if position is not None:
        position=tuple(float(v) for v in position)
        if len(position)!=2 or any(not math.isfinite(v) or not 0<=v<=1 for v in position):
            raise ValueError('Imprint position requires two normalized coordinates')
    if not math.isfinite(config.imprint_scale) or not .25<=config.imprint_scale<=3:
        raise ValueError('Imprint scale must be within .25–3')
    if not math.isfinite(spread) or not 0 <= spread <= 3:
        raise ValueError("film_border.factory_mark_spread must be between 0 and 3.")
    return FilmBorderConfig(
        enabled=bool(config.enabled),
        format_key=format_key,
        sprocket_rows=sprocket_rows,
        rebate_height_ratio=rebate,
        sprocket_count=sprocket_count,
        factory_marks=bool(config.factory_marks),
        stock_label=str(config.stock_label),
        factory_layout=config.factory_layout,
        factory_batch=int(config.factory_batch),
        factory_barcode=config.factory_barcode,
        factory_barcode_seed=config.factory_barcode_seed,
        loading_offset=float(config.loading_offset),
        advance_marker_style=config.advance_marker_style,
        **positions,
        factory_exposure_ev=float(config.factory_exposure_ev),
        factory_exposure_variation=float(config.factory_exposure_variation),
        frame_number=None if config.frame_number is None else int(config.frame_number),
        advance_direction=config.advance_direction,
        factory_pattern_top=normalize_edge_pattern(config.factory_pattern_top),
        factory_pattern_bottom=normalize_edge_pattern(config.factory_pattern_bottom),
        factory_pattern_linked=bool(config.factory_pattern_linked),
        factory_flip_top=bool(config.factory_flip_top),
        factory_flip_bottom=bool(config.factory_flip_bottom),
        factory_mark_type=config.factory_mark_type,
        factory_mark_spread=spread,
        imprint_style=imprint_style,
        imprint_side=imprint_side,
        imprint_text=str(config.imprint_text or ""),
        imprint_time=str(config.imprint_time or ""),
        imprint_font=config.imprint_font,
        imprint_position_xy=position,
        imprint_scale=float(config.imprint_scale),
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
        factory_edges=_factory_edges(validated, canvas_w, canvas_h, rebate),
    )


def _factory_edges(config, width, height, rebate):
    if not config.factory_marks:
        return ()
    inner, outer = (0.12, 0.84) if config.format_key == "120" else (0.72, 0.96)
    top = config.factory_pattern_top
    bottom = top if config.factory_pattern_linked else config.factory_pattern_bottom
    return (
        FactoryEdgeSpec("top", (0.04, rebate * (1 - outer) / height, 0.82, rebate * (1 - inner) / height), top, config.factory_flip_top),
        FactoryEdgeSpec("bottom", (0.04, (height - rebate + rebate * inner) / height, 0.82, (height - rebate + rebate * outer) / height), bottom, config.factory_flip_bottom),
    )


def _sprocket_holes(
    config: FilmBorderConfig,
    canvas_w: int,
    canvas_h: int,
    rebate: int,
) -> tuple[SprocketHoleSpec, ...]:
    rows = str(config.sprocket_rows)
    if rows == "none" or config.format_key == "120":
        return ()
    count = config.sprocket_count
    if count is None:
        count = int(_DEFAULT_SPROCKET_COUNTS.get(config.format_key, 8))
    if count <= 0:
        return ()
    hole_w = max(2, int(round(canvas_w / (count * 2.35))))
    hole_h = max(1, int(round(rebate * 0.50)))
    margin_x = hole_w * 0.55
    usable = max(float(canvas_w) - 2.0 * margin_x - float(hole_w), 1.0)
    specs: list[SprocketHoleSpec] = []
    row_ys: list[float] = []
    if rows in {"top", "both"}:
        row_ys.append(rebate * 0.38)
    if rows in {"bottom", "both"}:
        row_ys.append(float(canvas_h - rebate) + rebate * 0.12)
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
        y = (float(rebate) / float(canvas_h)) * 0.16
    else:
        y = 1.0 - (float(rebate) / float(canvas_h)) * 0.16
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
    font_size = max(8, int(round(min(height, width) * 0.045 * config.imprint_scale)))
    style = str(config.imprint_style)
    scale=4
    font = _imprint_font(font_size*scale,label)
    bbox=font.getbbox(label)
    available=max(1,int(width*.91))
    if bbox[2]-bbox[0] > available*scale:
        font_size=max(4,int(font_size*available*scale/(bbox[2]-bbox[0])))
        font=_imprint_font(font_size*scale,label);bbox=font.getbbox(label)
    text_w=max(1,int(math.ceil((bbox[2]-bbox[0])/scale)))
    text_h=max(1,int(math.ceil((bbox[3]-bbox[1])/scale)))
    # Supersample only the small text strip, never a full frame at 4x.
    strip=Image.new("L",(min(available,text_w)*scale,text_h*scale),0)
    ImageDraw.Draw(strip).text((-bbox[0],-bbox[1]),label,font=font,fill=255)
    high=np.asarray(strip,dtype=np.uint8)
    if config.imprint_font != 'legacy':
        from film_foundry.core.imprint_glyphs import render_display_text
        high=render_display_text(label,config.imprint_font,font_size*scale,available*scale)
        text_h=max(1,int(math.ceil(high.shape[0]/scale)))
        strip=Image.fromarray(high)
    if style == "optical_amber":
        # Quarter-pixel thinning keeps counters open without erasing strokes.
        high=cv2.erode(high,np.ones((2,2),np.uint8))
    ink=cv2.resize(high,(strip.width//scale,text_h),interpolation=cv2.INTER_AREA).astype(np.float32)/255.
    x=int(round(width*.045))
    text_w=ink.shape[1]
    margin_y = max(2, int(round(height * 0.014)))
    if str(config.imprint_side) == "top":
        y = margin_y
    else:
        y = height - margin_y - text_h
    y = int(np.clip(y,0,max(0,height-text_h)))
    if config.imprint_position_xy is not None:
        x=round(config.imprint_position_xy[0]*max(0,width-text_w))
        y=round(config.imprint_position_xy[1]*max(0,height-text_h))
    glyph=np.zeros((height,width),np.float32)
    used_h=min(text_h,height-y);used_w=min(text_w,width-x)
    if used_h > 0 and used_w > 0:glyph[y:y+used_h,x:x+used_w]=ink[:used_h,:used_w]
    if style == "lab_edge":
        underline_y=min(height-1,y+text_h+1)
        glyph[underline_y,x:min(width,x+text_w)]=.45
    if style == "laser_dot":
        mask = _laser_dot_mask(glyph, font_size)
    elif style == "optical_amber":
        sigma = max(0.30, float(font_size) / 80.0)
        mask = cv2.GaussianBlur(glyph, (0, 0), sigmaX=sigma, sigmaY=sigma)
    else:
        mask = glyph
    np.clip(mask, 0.0, 1.0, out=mask)
    return mask.astype(np.float32, copy=False)


def _laser_dot_mask(glyph: np.ndarray, font_size: int) -> np.ndarray:
    height, width = glyph.shape
    block = max(2, int(round(font_size / 11.0)))
    rows,columns=int(math.ceil(height/block)),int(math.ceil(width/block))
    if rows == 0 or columns == 0:
        return glyph
    padded=np.zeros((rows*block,columns*block),np.float32)
    padded[:height,:width]=glyph
    # Area occupancy catches fine strokes between dot centres. Point samples
    # previously lost those strokes and even whole digits at preview sizes.
    coverage=padded.reshape(rows,block,columns,block).mean(axis=(1,3))
    mask = np.zeros_like(glyph, dtype=np.float32)
    on = coverage > .10
    if not np.any(on):
        return glyph
    ys, xs = np.nonzero(on)
    radius=max(0, int(round(block*.28)))
    for row,column in zip(ys,xs):
        cv2.circle(mask,(int(column*block+block//2),int(row*block+block//2)),radius,1.,-1)
    return mask


def _imprint_font(size: int,label: str = ""):
    candidates=_IMPRINT_FONT_CANDIDATES if any(ord(char)>127 for char in label) else _IMPRINT_LATIN_FONT_CANDIDATES+_IMPRINT_FONT_CANDIDATES
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size=int(size))
        except OSError:
            continue
    return ImageFont.load_default()

