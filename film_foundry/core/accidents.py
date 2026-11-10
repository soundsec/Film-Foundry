"""冲洗事故。

漏光在曝光阶段。灰尘、毛发、飞虫、碎屑挡在潜影前。
污染、不匀、网裂和染料损伤在密度形成之后。扫描不在这里。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np

from film_foundry.core.derived_cache import pseudoinverse_3x3

from film_foundry.core.development import build_effective_development
from film_foundry.core.incident_identity import incident_rng
from film_foundry.core.io_utils import cv_imread_unicode
from film_foundry.model.config import DevelopRecipeConfig, FilmStockConfig


@dataclass(frozen=True, slots=True)
class DensityAccidentComponents:
    """Post-process accident output with explicit optical-component semantics.

    ``density_cmy`` is the portable compatibility master.  The remaining
    fields describe how the same accidents must enter the authoritative RGB
    optical master without pretending that every deposit is image dye.
    Scalar maps are retained instead of extra full RGB frames so large-image
    callers can compose the contributions a row tile at a time.
    """

    density_cmy: np.ndarray
    maps: dict[str, np.ndarray]
    chemical_stain_layer_scale: tuple[float, float, float] | None = None
    surface_silver_density_scale: float = 0.0
    surface_silver_layer_scale: tuple[float, float, float] | None = None
    reticulation_density_scale: float = 0.0
    reticulation_layer_scale: tuple[float, float, float] | None = None
    compatibility_density_deferred: bool = False


@dataclass(frozen=True, slots=True)
class LightLeakPatternSpec:
    """Reusable structural identity for one reduced light-leak source.

    The spec deliberately contains no image pixels.  Reusing it across a
    batch keeps the entry side, breadth, spectral tendency, and low-frequency
    texture identity stable; a future batch policy can apply bounded per-frame
    placement/strength drift without turning the leak into an RGB overlay.
    """

    template_key: str
    side_weights: tuple[float, float, float, float]
    edge_widths: tuple[float, float, float, float]
    base_side_mix: float
    texture_seed: int
    center_xy: tuple[float, float] = (0.5, 0.5)
    rotation_degrees: float = 0.0
    breadth: float = 0.35
    falloff: float = 0.55


_MAX_RANDOM_FIELD_SIGMA = 32.0
_LIGHT_LEAK_CONTROL_LONG_EDGE = 1600
_EXPOSURE_OCCLUSION_CONTROL_LONG_EDGE = 1600
_RETICULATION_CONTROL_LONG_EDGE = 2400
EXPOSURE_OCCLUSION_AMOUNT_FIELDS = (
    "exposure_occlusion_dust",
    "exposure_occlusion_hair",
    "exposure_occlusion_insect",
    "exposure_occlusion_debris",
)
EXPOSURE_OCCLUSION_FRAME_AMOUNT_FIELDS = (
    "exposure_occlusion_dust",
    "exposure_occlusion_debris",
)
EXPOSURE_OCCLUSION_IDENTITY_AMOUNT_FIELDS = (
    "exposure_occlusion_hair",
    "exposure_occlusion_insect",
)
EXPOSURE_OCCLUSION_FIXED_IDENTITY_SEED = 0x51F11D01
_HAIR_IDENTITY_SALT = 0xA11F11D0
_INSECT_IDENTITY_SALT = 0x1B5EC701
_DUST_IDENTITY_SALT = 0xD0570101
_DEBRIS_IDENTITY_SALT = 0xDEB20101
_INCIDENT_IDENTITY_LONG_EDGE = 900
LIGHT_LEAK_TEMPLATE_KEYS = (
    "local_edge",
    "perimeter",
    "half_frame",
    "cone",
)
LIGHT_LEAK_CONFIG_TEMPLATE_KEYS = ("auto",) + LIGHT_LEAK_TEMPLATE_KEYS
LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS = ("none",) + LIGHT_LEAK_TEMPLATE_KEYS
LIGHT_LEAK_MIX_KEYS = ("max", "add", "replace")
LIGHT_LEAK_PLACEMENT_KEYS = ("sampled", "manual")
LIGHT_LEAK_ENTRY_SIDE_KEYS = ("auto", "left", "right", "top", "bottom")
_LIGHT_LEAK_ENTRY_SIDE_INDEX = {
    "left": 0,
    "right": 1,
    "top": 2,
    "bottom": 3,
}


def _scaled_shape(
    shape: tuple[int, int],
    scale_y: float,
    scale_x: float,
) -> tuple[int, int]:
    height, width = shape
    return (
        max(1, int(round(height / max(scale_y, 1.0)))),
        max(1, int(round(width / max(scale_x, 1.0)))),
    )


def _bounded_control_shape(
    shape: tuple[int, int],
    *,
    long_edge: int,
) -> tuple[int, int]:
    height, width = shape
    maximum = max(height, width)
    if maximum <= int(long_edge):
        return shape
    scale = float(long_edge) / float(maximum)
    return (
        max(1, int(round(height * scale))),
        max(1, int(round(width * scale))),
    )


def _incident_control_shape(shape,recipe,legacy_edge):
    if recipe.incident_identity_seed is None:
        return _bounded_control_shape(shape,long_edge=legacy_edge)
    # One normalized gate grid fixes object counts and positions across a
    # preview and a full-resolution frame of the same format/aspect ratio.
    scale=_INCIDENT_IDENTITY_LONG_EDGE/float(max(shape))
    return tuple(max(1,int(round(length*scale))) for length in shape)


def sample_light_leak_pattern(
    rng: np.random.Generator,
    template_key: str = "local_edge",
) -> LightLeakPatternSpec:
    """Sample one reusable leak identity independently of raster size.

    The default branch intentionally keeps the historical random-call order so
    ``auto`` presets retain their previous local-edge result for a fixed seed.
    """
    template_key = str(template_key).strip().lower()
    if template_key not in LIGHT_LEAK_TEMPLATE_KEYS:
        raise ValueError(f"Unsupported light-leak template: {template_key}")
    side_weights = np.zeros(4, dtype=np.float32)
    if template_key == "local_edge":
        active_count = 1 if float(rng.random()) < 0.76 else 2
        active_sides = rng.choice(4, size=active_count, replace=False)
        side_weights[active_sides] = rng.uniform(0.55, 1.0, active_count).astype(np.float32)
        return LightLeakPatternSpec(
            template_key="local_edge",
            side_weights=tuple(float(value) for value in side_weights),
            edge_widths=(
                float(rng.uniform(0.025, 0.18)),
                float(rng.uniform(0.025, 0.18)),
                float(rng.uniform(0.020, 0.14)),
                float(rng.uniform(0.020, 0.14)),
            ),
            base_side_mix=float(rng.uniform(0.30, 1.0)),
            texture_seed=int(rng.integers(0, 2**32, dtype=np.uint32)),
        )
    if template_key == "perimeter":
        side_weights[:] = rng.uniform(0.62, 1.0, 4).astype(np.float32)
        return LightLeakPatternSpec(
            template_key="perimeter",
            side_weights=tuple(float(value) for value in side_weights),
            edge_widths=tuple(float(rng.uniform(0.025, 0.11)) for _ in range(4)),
            base_side_mix=float(rng.uniform(0.45, 1.0)),
            texture_seed=int(rng.integers(0, 2**32, dtype=np.uint32)),
        )

    side = int(rng.integers(0, 4))
    side_weights[side] = float(rng.uniform(0.72, 1.0))
    along_edge = float(rng.uniform(0.14, 0.86))
    if side == 0:
        center_xy = (0.0, along_edge)
        rotation_degrees = 0.0
    elif side == 1:
        center_xy = (1.0, along_edge)
        rotation_degrees = 180.0
    elif side == 2:
        center_xy = (along_edge, 0.0)
        rotation_degrees = 90.0
    else:
        center_xy = (along_edge, 1.0)
        rotation_degrees = -90.0
    rotation_degrees += float(rng.uniform(-14.0, 14.0))
    breadth = float(
        rng.uniform(0.62, 0.95)
        if template_key == "half_frame"
        else rng.uniform(0.16, 0.38)
    )
    return LightLeakPatternSpec(
        template_key=template_key,
        side_weights=tuple(float(value) for value in side_weights),
        edge_widths=(0.08, 0.08, 0.08, 0.08),
        base_side_mix=float(rng.uniform(0.30, 1.0)),
        texture_seed=int(rng.integers(0, 2**32, dtype=np.uint32)),
        center_xy=center_xy,
        rotation_degrees=rotation_degrees,
        breadth=breadth,
        falloff=float(rng.uniform(0.42, 0.82)),
    )


def resolved_light_leak_template(recipe: DevelopRecipeConfig) -> str:
    configured = str(getattr(recipe, "light_leak_template", "auto")).strip().lower()
    return "local_edge" if configured == "auto" else configured


def resolve_light_leak_pattern(
    pattern: LightLeakPatternSpec,
    recipe: DevelopRecipeConfig,
) -> LightLeakPatternSpec:
    """Apply optional manual placement without resampling texture identity."""
    placement = str(getattr(recipe, "light_leak_placement", "sampled")).strip().lower()
    if placement in {"", "sampled"}:
        return pattern
    if placement != "manual":
        raise ValueError(
            "develop.light_leak_placement must be one of: "
            + ", ".join(LIGHT_LEAK_PLACEMENT_KEYS)
        )
    side = str(getattr(recipe, "light_leak_entry_side", "auto")).strip().lower()
    if side not in LIGHT_LEAK_ENTRY_SIDE_KEYS:
        raise ValueError(
            "develop.light_leak_entry_side must be one of: "
            + ", ".join(LIGHT_LEAK_ENTRY_SIDE_KEYS)
        )
    center_x = float(np.clip(float(getattr(recipe, "light_leak_center_x", 0.5)), 0.0, 1.0))
    center_y = float(np.clip(float(getattr(recipe, "light_leak_center_y", 0.5)), 0.0, 1.0))
    rotation = float(getattr(recipe, "light_leak_rotation_degrees", 0.0))
    if not np.isfinite(rotation):
        raise ValueError("develop.light_leak_rotation_degrees must be finite.")
    side_weights = tuple(float(value) for value in pattern.side_weights)
    center_xy = (center_x, center_y)
    rotation_degrees = rotation
    if side in _LIGHT_LEAK_ENTRY_SIDE_INDEX:
        weights = [0.0, 0.0, 0.0, 0.0]
        weights[_LIGHT_LEAK_ENTRY_SIDE_INDEX[side]] = 1.0
        side_weights = tuple(weights)
        if pattern.template_key in {"half_frame", "cone"}:
            if side == "left":
                center_xy = (0.0, center_y)
                rotation_degrees = 0.0 + rotation
            elif side == "right":
                center_xy = (1.0, center_y)
                rotation_degrees = 180.0 + rotation
            elif side == "top":
                center_xy = (center_x, 0.0)
                rotation_degrees = 90.0 + rotation
            else:
                center_xy = (center_x, 1.0)
                rotation_degrees = -90.0 + rotation
    return replace(
        pattern,
        side_weights=side_weights,
        center_xy=center_xy,
        rotation_degrees=rotation_degrees,
    )


def _peak_normalize_coverage(values: np.ndarray) -> np.ndarray:
    peak = float(np.max(values))
    if peak <= 1e-8:
        return np.asarray(values, dtype=np.float32)
    normalized = np.asarray(values, dtype=np.float32, copy=True)
    normalized *= np.float32(1.0 / peak)
    return normalized


def prepare_light_leak_mask_field(
    image: np.ndarray,
    shape: tuple[int, int],
) -> np.ndarray:
    """Turn an external image into a peak-normalized coverage field."""
    image = np.asarray(image)
    if image.ndim == 3:
        coverage = np.max(image[..., : min(3, image.shape[-1])], axis=2)
    else:
        coverage = np.asarray(image)
    coverage = np.asarray(coverage, dtype=np.float32)
    if coverage.size == 0:
        return np.zeros(shape, dtype=np.float32)
    if np.issubdtype(image.dtype, np.integer):
        coverage /= np.float32(np.iinfo(image.dtype).max)
    elif float(np.max(coverage)) > 1.5:
        coverage /= np.float32(255.0)
    np.clip(coverage, 0.0, 1.0, out=coverage)
    coverage = _resize_work_map(coverage, shape)
    return _peak_normalize_coverage(coverage)


def load_light_leak_mask_field(path: str | Path, shape: tuple[int, int]) -> np.ndarray:
    image = cv_imread_unicode(path, cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"develop.light_leak_mask_path could not be read: {path}")
    return prepare_light_leak_mask_field(image, shape)


def compose_light_leak_coverage(
    left: np.ndarray,
    right: np.ndarray,
    mix: str,
) -> np.ndarray:
    mix_key = str(mix).strip().lower()
    if mix_key == "max":
        return np.maximum(left, right).astype(np.float32, copy=False)
    if mix_key == "add":
        composed = np.add(left, right, dtype=np.float32)
        np.clip(composed, 0.0, 1.0, out=composed)
        return composed
    raise ValueError(
        "develop.light_leak_mix must be one of: " + ", ".join(LIGHT_LEAK_MIX_KEYS)
    )


def _light_leak_coverage_field(
    pattern: LightLeakPatternSpec,
    work_shape: tuple[int, int],
    xx: np.ndarray,
    yy: np.ndarray,
    field_rng: np.random.Generator,
) -> np.ndarray:
    template_key = str(pattern.template_key).strip().lower()
    if template_key not in LIGHT_LEAK_TEMPLATE_KEYS:
        raise ValueError(f"Unsupported light-leak template: {pattern.template_key}")
    side_weights = np.asarray(pattern.side_weights, dtype=np.float32)
    if side_weights.shape != (4,) or not np.all(np.isfinite(side_weights)) or np.any(side_weights < 0.0):
        raise ValueError("Light-leak side weights must contain four finite non-negative values.")
    if not any(float(value) > 0.0 for value in side_weights):
        raise ValueError("Light-leak pattern must activate at least one entry side.")
    if len(pattern.edge_widths) != 4 or not np.all(np.isfinite(pattern.edge_widths)):
        raise ValueError("Light-leak edge widths must contain four finite values.")
    if np.any(np.asarray(pattern.edge_widths, dtype=np.float32) <= 0.0):
        raise ValueError("Light-leak edge widths must be positive.")
    center_xy = np.asarray(pattern.center_xy, dtype=np.float32)
    if (
        center_xy.shape != (2,)
        or not np.all(np.isfinite(center_xy))
        or np.any(center_xy < 0.0)
        or np.any(center_xy > 1.0)
    ):
        raise ValueError("Light-leak center must contain two normalized finite coordinates.")
    if not np.isfinite(float(pattern.rotation_degrees)):
        raise ValueError("Light-leak rotation must be finite.")
    if not np.isfinite(float(pattern.breadth)) or float(pattern.breadth) <= 0.0:
        raise ValueError("Light-leak breadth must be positive and finite.")
    if not np.isfinite(float(pattern.falloff)) or float(pattern.falloff) <= 0.0:
        raise ValueError("Light-leak falloff must be positive and finite.")

    blob_sigma = max(3.0, float(max(work_shape)) * 0.18)
    blobs = _low_frequency_noise(work_shape, field_rng, blob_sigma)
    if template_key in {"local_edge", "perimeter"}:
        # Keep the historical local-edge expression intact. ``perimeter`` uses
        # the same physical edge-entry family with all four seals active.
        smooth_edge = np.zeros(work_shape, dtype=np.float32)
        candidate = np.power(1.0 - xx, 4.5).astype(np.float32)
        candidate *= side_weights[0]
        np.maximum(smooth_edge, candidate, out=smooth_edge)
        candidate = np.power(xx, 4.5).astype(np.float32)
        candidate *= side_weights[1]
        np.maximum(smooth_edge, candidate, out=smooth_edge)
        candidate = np.power(1.0 - yy, 4.5).astype(np.float32)
        candidate *= side_weights[2]
        np.maximum(smooth_edge, candidate, out=smooth_edge)
        candidate = np.power(yy, 4.5).astype(np.float32)
        candidate *= side_weights[3]
        np.maximum(smooth_edge, candidate, out=smooth_edge)

        corner = np.power((1.0 - xx) * (1.0 - yy), 2.2).astype(np.float32)
        corner *= max(side_weights[0], side_weights[2])
        candidate = np.power(xx * (1.0 - yy), 2.2).astype(np.float32)
        candidate *= max(side_weights[1], side_weights[2])
        np.maximum(corner, candidate, out=corner)
        candidate = np.power((1.0 - xx) * yy, 2.2).astype(np.float32)
        candidate *= max(side_weights[0], side_weights[3])
        np.maximum(corner, candidate, out=corner)
        candidate = np.power(xx * yy, 2.2).astype(np.float32)
        candidate *= max(side_weights[1], side_weights[3])
        np.maximum(corner, candidate, out=corner)

        ragged_edge = _edge_leak_mask(
            work_shape,
            xx,
            yy,
            field_rng,
            side_weights,
            pattern.edge_widths,
        )
        leak_map = np.clip(
            np.maximum(smooth_edge * 0.48, ragged_edge * 0.86)
            + corner * 0.36
            + blobs * 0.18,
            0.0,
            1.0,
        )
    else:
        # Broad half-frame and cone templates are normalized geometric exposure
        # envelopes modulated by the same band-limited physical texture family.
        # They remain scalar masks until the existing base/emulsion spectral
        # tendency turns them into additional RGB exposure below.
        angle = np.deg2rad(float(pattern.rotation_degrees))
        direction_x = np.float32(np.cos(angle))
        direction_y = np.float32(np.sin(angle))
        delta_x = xx - np.float32(center_xy[0])
        delta_y = yy - np.float32(center_xy[1])
        forward = delta_x * direction_x + delta_y * direction_y
        perpendicular = -delta_x * direction_y + delta_y * direction_x
        entry_gate = _soft_threshold(forward, -0.035, 0.030)
        depth = np.maximum(forward, 0.0).astype(np.float32, copy=False)
        depth /= np.float32(float(pattern.falloff))
        depth *= -1.0
        np.exp(depth, out=depth)
        if template_key == "half_frame":
            lateral = np.abs(perpendicular).astype(np.float32, copy=False)
            lateral /= np.float32(float(pattern.breadth))
            np.square(lateral, out=lateral)
            np.square(lateral, out=lateral)
            lateral *= -1.0
            np.exp(lateral, out=lateral)
        else:
            cone_width = np.maximum(forward, 0.0).astype(np.float32, copy=True)
            cone_width += np.float32(0.045)
            cone_width *= np.float32(float(pattern.breadth))
            lateral = _soft_threshold(
                cone_width - np.abs(perpendicular),
                -0.025,
                0.040,
            )
        envelope = entry_gate * depth
        envelope *= lateral
        envelope *= np.float32(float(np.max(side_weights)))
        rough = _stain_deposit_map(
            work_shape,
            field_rng,
            max(2.0, float(min(work_shape)) * 0.085),
        )
        rough *= 0.36
        rough += 0.74
        leak_map = envelope * rough
        blobs *= envelope
        blobs *= 0.12
        leak_map += blobs
        np.clip(leak_map, 0.0, 1.0, out=leak_map)
    leak_map = cv2.GaussianBlur(leak_map, (0, 0), sigmaX=blob_sigma * 0.045, sigmaY=blob_sigma * 0.045)
    if template_key != "local_edge":
        # Explicit templates use shape to control spatial coverage and the
        # strength slider to control peak added exposure.  Normalise only the
        # bounded control field: total energy remains different for a narrow
        # cone, a half-frame wash, and a perimeter leak.  The historical
        # local-edge path is deliberately excluded to preserve old presets.
        leak_map = _peak_normalize_coverage(leak_map)
    return np.clip(leak_map, 0.0, 1.0).astype(np.float32, copy=False)


def _low_frequency_noise(
    shape: tuple[int, int],
    rng: np.random.Generator,
    sigma: float,
) -> np.ndarray:
    height, width = shape
    sigma = max(float(sigma), 0.0)
    scale = max(1.0, sigma / _MAX_RANDOM_FIELD_SIGMA)
    work_shape = _scaled_shape(shape, scale, scale)
    work_sigma = sigma / scale
    noise = rng.normal(0.0, 1.0, work_shape).astype(np.float32)
    # ``noise`` is a private random work buffer.  Let OpenCV reuse it for the
    # blur instead of retaining an equally sized source and destination until
    # the function returns.  This does not change the field, random stream, or
    # full-resolution observation semantics.
    cv2.GaussianBlur(
        noise,
        (0, 0),
        sigmaX=work_sigma,
        sigmaY=work_sigma,
        dst=noise,
    )
    noise -= float(np.min(noise))
    peak = float(np.max(noise))
    if peak <= 1e-6:
        return np.zeros((height, width), dtype=np.float32)
    noise /= peak
    if work_shape != shape:
        noise = cv2.resize(noise, (width, height), interpolation=cv2.INTER_LINEAR)
    return np.asarray(noise, dtype=np.float32)


def _normalize01(values: np.ndarray) -> np.ndarray:
    # Preserve the helper's non-mutating contract while avoiding separate
    # subtraction and division result frames.
    values = np.array(values, dtype=np.float32, copy=True)
    values -= float(np.min(values))
    peak = float(np.max(values))
    if peak <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    values /= peak
    return values


def _soft_threshold(values: np.ndarray, edge0: float, edge1: float) -> np.ndarray:
    edge0_values = np.asarray(edge0, dtype=np.float32)
    edge1_values = np.asarray(edge1, dtype=np.float32)
    x = np.subtract(values, edge0_values, dtype=np.float32)
    denominator = np.subtract(edge1_values, edge0_values, dtype=np.float32)
    if np.ndim(denominator) == 0:
        x /= max(float(denominator), 1e-6)
    else:
        np.maximum(denominator, 1e-6, out=denominator)
        x /= denominator
    np.clip(x, 0.0, 1.0, out=x)
    curve = np.multiply(x, x, dtype=np.float32)
    x *= -2.0
    x += 3.0
    curve *= x
    return curve


def _stain_deposit_map(
    shape: tuple[int, int],
    rng: np.random.Generator,
    sigma: float,
) -> np.ndarray:
    target_shape = shape
    fine_sigma = max(0.8, sigma * 0.055)
    # Deposits are a band-limited physical control field.  Keep at least four
    # work samples per smallest correlation radius and resize only the final
    # scalar map; this avoids materializing every intermediate at 12–40MP.
    scale = max(1.0, fine_sigma / 4.0)
    shape = _scaled_shape(target_shape, scale, scale)
    sigma /= scale
    base = _low_frequency_noise(shape, rng, sigma)
    mid = _low_frequency_noise(shape, rng, max(1.5 / scale, sigma * 0.22))
    fine = _low_frequency_noise(shape, rng, max(0.8 / scale, sigma * 0.055))

    edge = cv2.Laplacian(base, cv2.CV_32F, ksize=3)
    edge = _normalize01(np.abs(edge))
    deposits = _soft_threshold(mid, 0.62, 0.88) * (0.65 + 0.35 * fine)
    voids = _soft_threshold(1.0 - mid, 0.72, 0.92) * 0.22
    ragged = 0.82 + 0.30 * (mid - 0.5) + 0.16 * (fine - 0.5)

    stain = base * ragged + deposits * 0.30 + edge * 0.12 - voids
    stain = cv2.GaussianBlur(stain, (0, 0), sigmaX=max(0.45, sigma * 0.012), sigmaY=max(0.45, sigma * 0.012))
    stain = _normalize01(stain)
    if shape != target_shape:
        stain = cv2.resize(
            stain,
            (target_shape[1], target_shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )
    return np.asarray(stain, dtype=np.float32)


def _axis_streaks(
    shape: tuple[int, int],
    rng: np.random.Generator,
    *,
    along_y: bool,
    long_sigma: float,
    short_sigma: float,
) -> np.ndarray:
    sigma_x = short_sigma if along_y else long_sigma
    sigma_y = long_sigma if along_y else short_sigma
    scale_x = max(1.0, float(sigma_x) / _MAX_RANDOM_FIELD_SIGMA)
    scale_y = max(1.0, float(sigma_y) / _MAX_RANDOM_FIELD_SIGMA)
    work_shape = _scaled_shape(shape, scale_y, scale_x)
    noise = rng.normal(0.0, 1.0, work_shape).astype(np.float32)
    cv2.GaussianBlur(
        noise,
        (0, 0),
        sigmaX=float(sigma_x) / scale_x,
        sigmaY=float(sigma_y) / scale_y,
        dst=noise,
    )
    if work_shape != shape:
        noise = cv2.resize(
            noise,
            (shape[1], shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )
    streaks = _normalize01(noise)
    streaks -= 0.5
    streaks *= 2.0
    return streaks


def _uneven_development_map(
    shape: tuple[int, int],
    rng: np.random.Generator,
    sigma: float,
    agitation_deficit: float,
) -> np.ndarray:
    target_shape = shape
    target_short_sigma = max(0.8, sigma * 0.035)
    scale = max(1.0, target_short_sigma / 4.0)
    shape = _scaled_shape(target_shape, scale, scale)
    sigma /= scale
    base = _low_frequency_noise(shape, rng, sigma)
    base = (base - 0.5) * 2.0

    long_sigma = max(4.0, sigma * (0.70 + 0.45 * agitation_deficit))
    short_sigma = max(0.8, sigma * 0.035)
    # One bath/frame has one dominant drainage or agitation-flow direction at
    # this reduced level.  Mixing independent vertical and horizontal streak
    # fields produced a conspicuous woven grid that could be mistaken for
    # overlapping row tiles even though no tiling was active.  Keep one
    # randomly oriented flow field and let the isotropic base/mottle terms
    # provide irregularity instead of inventing a perpendicular second flow.
    along_y = bool(rng.integers(0, 2))
    streaks = _axis_streaks(
        shape,
        rng,
        along_y=along_y,
        long_sigma=long_sigma,
        short_sigma=short_sigma,
    )

    mottles = _stain_deposit_map(shape, rng, max(2.0, sigma * 0.42))
    mottles = (mottles - 0.5) * 2.0

    uneven = base * 0.68 + streaks * (0.14 + 0.18 * agitation_deficit) + mottles * 0.16
    uneven = np.clip(uneven, -1.0, 1.0).astype(np.float32)
    if shape != target_shape:
        uneven = cv2.resize(
            uneven,
            (target_shape[1], target_shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )
    return np.asarray(uneven, dtype=np.float32)


def _edge_leak_mask(
    shape: tuple[int, int],
    xx: np.ndarray,
    yy: np.ndarray,
    rng: np.random.Generator,
    side_weights: np.ndarray,
    edge_widths: tuple[float, float, float, float],
) -> np.ndarray:
    height, width = shape
    softness = 0.035 + 0.055 * _low_frequency_noise(shape, rng, max(2.0, min(height, width) * 0.035))
    rough = _stain_deposit_map(shape, rng, max(2.0, min(height, width) * 0.10))

    left_width, right_width, top_width, bottom_width = edge_widths
    edge = _soft_threshold(left_width * (0.70 + 0.75 * rough) - xx, -softness, softness)
    edge *= side_weights[0]
    candidate = _soft_threshold(right_width * (0.70 + 0.75 * rough) - (1.0 - xx), -softness, softness)
    candidate *= side_weights[1]
    np.maximum(edge, candidate, out=edge)
    candidate = _soft_threshold(top_width * (0.70 + 0.75 * rough) - yy, -softness, softness)
    candidate *= side_weights[2]
    np.maximum(edge, candidate, out=edge)
    candidate = _soft_threshold(bottom_width * (0.70 + 0.75 * rough) - (1.0 - yy), -softness, softness)
    candidate *= side_weights[3]
    np.maximum(edge, candidate, out=edge)

    dominant_side = int(np.argmax(side_weights))
    channel = _axis_streaks(
        shape,
        rng,
        along_y=dominant_side in (0, 1),
        long_sigma=max(5.0, (height if dominant_side in (0, 1) else width) * 0.22),
        short_sigma=max(0.8, (width if dominant_side in (0, 1) else height) * 0.006),
    )
    channel *= 0.24
    channel += 0.5
    np.clip(channel, 0.0, 1.0, out=channel)
    channel *= 0.55
    channel += 0.72
    edge *= channel
    return np.clip(edge, 0.0, 1.0, out=edge).astype(np.float32, copy=False)


def _resize_work_map(map_data: np.ndarray, target_shape: tuple[int, int]) -> np.ndarray:
    height, width = target_shape
    if map_data.shape == (height, width):
        return map_data.astype(np.float32, copy=False)
    return cv2.resize(map_data, (width, height), interpolation=cv2.INTER_LINEAR).astype(np.float32)


def _bounded_pathology(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def _reticulation_texture_map(
    shape: tuple[int, int],
    rng: np.random.Generator,
) -> np.ndarray:
    """Return a bounded, near-zero-mean gelatin wrinkle/network proxy.

    Reticulation is represented as broad-band emulsion path-length/scatter
    variation, not as created silver/dye and not as scanner noise.  A blurred
    random topology is converted into narrow contour bands, giving irregular
    connected ridges without allocating explicit particles or a Voronoi
    diagram.  The field is centered so the accident mainly redistributes local
    optical thickness instead of adding a uniform fog veil.
    """
    longest = float(max(shape))
    topology = _low_frequency_noise(
        shape,
        rng,
        max(2.0, longest * 0.014),
    )
    topology -= 0.5
    topology /= np.float32(0.085)
    np.square(topology, out=topology)
    topology *= -1.0
    np.exp(topology, out=topology)

    modulation = _low_frequency_noise(
        shape,
        rng,
        max(2.0, longest * 0.006),
    )
    modulation *= 0.35
    modulation += 0.72
    topology *= modulation
    topology -= np.float32(np.mean(topology, dtype=np.float64))
    amplitude = float(np.max(np.abs(topology)))
    if amplitude > 1e-8:
        topology /= np.float32(amplitude)
    return np.clip(topology, -1.0, 1.0, out=topology).astype(
        np.float32,
        copy=False,
    )


def uneven_development_rate_field(
    image_linear: np.ndarray | None,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    fast: bool = False,
    work_long_edge: int | None = None,
    frame_shape: tuple[int, int] | None = None,
) -> np.ndarray | None:
    """Build a bounded spatial developer-rate field for process operators.

    This field is deliberately not multiplied into exposure.  Uneven chemical
    activity changes silver/dye conversion after the material latent state has
    been frozen; only a real light leak belongs on the exposure side.
    """
    state = build_effective_development(recipe)
    strength = float(state.uneven_development)
    if strength <= 1e-6:
        return None

    agitation_deficit = _bounded_pathology(state.agitation_deficit)
    underdevelopment = _bounded_pathology(state.underdevelopment)
    exhaustion = _bounded_pathology(state.exhaustion)
    concentration_stress = _bounded_pathology(state.concentration_stress)
    temperature_stress = _bounded_pathology(state.temperature_stress)
    pathology = (
        (1.0 + 0.70 * agitation_deficit)
        * (1.0 + 0.35 * underdevelopment)
        * (1.0 + 0.30 * exhaustion)
        * (1.0 + 0.22 * concentration_stress)
        * (1.0 + 0.22 * temperature_stress)
    )
    effective_strength = float(np.clip(strength * pathology, 0.0, 1.0))

    if frame_shape is None:
        if image_linear is None:
            raise ValueError("uneven development needs a frame extent")
        frame_shape = np.asarray(image_linear).shape[:2]
    height, width = frame_shape
    if height <= 0 or width <= 0:
        raise ValueError("uneven development needs a non-empty frame extent")
    if work_long_edge is None and fast:
        work_long_edge = 1200
    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
    else:
        work_shape = (height, width)

    rng = incident_rng(recipe,rng or np.random.default_rng(),"uneven_development")
    uneven_sigma = max(2.0, float(max(work_shape)) * 0.16 * (1.0 + 0.24 * agitation_deficit))
    uneven = _resize_work_map(
        _uneven_development_map(work_shape, rng, uneven_sigma, agitation_deficit),
        (height, width),
    )
    # ±0.45 stop is the severe-control limit for the reduced local reaction
    # rate. It is not an exposure-equivalent latent-state modification.
    # ``uneven`` has reached its only consumer.  Convert it in place from the
    # centered accident field into the rate multiplier, avoiding one extra
    # full-frame scalar allocation when the accident is enabled.
    uneven *= float(0.45 * effective_strength)
    np.exp2(uneven, out=uneven)
    return uneven


def apply_light_leak_to_exposure(
    image_linear: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    pattern: LightLeakPatternSpec | None = None,
    secondary_pattern: LightLeakPatternSpec | None = None,
    mask: np.ndarray | None = None,
    exposure_ceiling: float | None = 4.0,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Add light-leak exposure before H-D density formation."""
    state = build_effective_development(recipe)
    strength = float(state.light_leak_strength)
    if strength <= 1e-6:
        return np.asarray(image_linear, dtype=np.float32), None

    rng = rng or np.random.default_rng()
    identity_seed = getattr(recipe, "light_leak_identity_seed", None)
    sample_rng = (
        incident_rng(recipe,rng,"light_leak")
        if identity_seed is None
        else np.random.default_rng(int(identity_seed))
    )
    if pattern is None:
        pattern = sample_light_leak_pattern(
            sample_rng,
            template_key=resolved_light_leak_template(recipe),
        )
    pattern = resolve_light_leak_pattern(pattern, recipe)
    mix = str(getattr(recipe, "light_leak_mix", "max")).strip().lower()
    if mix not in LIGHT_LEAK_MIX_KEYS:
        raise ValueError(
            "develop.light_leak_mix must be one of: " + ", ".join(LIGHT_LEAK_MIX_KEYS)
        )
    secondary_key = str(
        getattr(recipe, "light_leak_secondary_template", "none")
    ).strip().lower()
    if secondary_key in {"", "none"}:
        secondary_pattern = None
    else:
        if secondary_key not in LIGHT_LEAK_TEMPLATE_KEYS:
            raise ValueError(
                "develop.light_leak_secondary_template must be one of: "
                + ", ".join(LIGHT_LEAK_SECONDARY_TEMPLATE_KEYS)
            )
        if secondary_pattern is None:
            secondary_pattern = sample_light_leak_pattern(
                sample_rng,
                template_key=secondary_key,
            )
    image_linear = np.asarray(image_linear, dtype=np.float32)
    height, width = image_linear.shape[:2]
    work_shape = _incident_control_shape((height,width),recipe,_LIGHT_LEAK_CONTROL_LONG_EDGE)
    work_height, work_width = work_shape
    xx = np.linspace(0.0, 1.0, work_width, dtype=np.float32)[None, :]
    yy = np.linspace(0.0, 1.0, work_height, dtype=np.float32)[:, None]
    mask_path = str(getattr(recipe, "light_leak_mask_path", "")).strip()
    if mask is None and mask_path:
        mask = load_light_leak_mask_field(mask_path, work_shape)
    elif mask is not None:
        mask = prepare_light_leak_mask_field(mask, work_shape)
    use_mask = mask is not None and float(np.max(mask)) > 1e-8
    if mix == "replace" and not use_mask:
        raise ValueError("develop.light_leak_mix='replace' requires a light-leak mask.")

    field_rng = np.random.default_rng(int(pattern.texture_seed))
    if mix == "replace":
        leak_map = np.asarray(mask, dtype=np.float32)
    else:
        leak_map = _light_leak_coverage_field(
            pattern,
            work_shape,
            xx,
            yy,
            field_rng,
        )
        if secondary_pattern is not None:
            secondary_rng = np.random.default_rng(int(secondary_pattern.texture_seed))
            secondary_field = _light_leak_coverage_field(
                secondary_pattern,
                work_shape,
                xx,
                yy,
                secondary_rng,
            )
            leak_map = compose_light_leak_coverage(leak_map, secondary_field, mix)
        if use_mask:
            leak_map = compose_light_leak_coverage(leak_map, mask, mix)
    leak_map = np.clip(leak_map * strength, 0.0, 1.0).astype(np.float32)
    if work_shape != (height, width):
        leak_map = cv2.resize(
            leak_map,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)

    # Base-side entry tends toward red/orange; emulsion-side entry can be much
    # less red. Randomly interpolate those reduced spectral tendencies instead
    # of claiming that every leak has the same colour.
    base_side_mix = float(np.clip(pattern.base_side_mix, 0.0, 1.0))
    leak_color = (
        np.asarray((1.0, 0.92, 0.78), dtype=np.float32) * (1.0 - base_side_mix)
        + np.asarray((1.0, 0.42, 0.15), dtype=np.float32) * base_side_mix
    )
    color_variation = _low_frequency_noise(
        work_shape,
        field_rng,
        max(2.0, float(max(work_shape)) * 0.12),
    )
    if work_shape != (height, width):
        color_variation = cv2.resize(
            color_variation,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)

    # Apply the scalar exposure contribution channel-by-channel.  This keeps
    # one output RGB frame plus scalar maps instead of a second full RGB color
    # field, while preserving the exposure-side semantics.
    leaked = image_linear.copy()
    contribution = np.empty((height, width), dtype=np.float32)
    np.multiply(leak_map, 0.85 + 0.35 * color_variation, out=contribution)
    contribution *= float(leak_color[0] * 1.25)
    leaked[..., 0] += contribution
    np.multiply(leak_map, 1.08 - 0.16 * color_variation, out=contribution)
    contribution *= float(leak_color[1] * 1.25)
    leaked[..., 1] += contribution
    np.multiply(leak_map, 0.78 + 0.22 * color_variation, out=contribution)
    contribution *= float(leak_color[2] * 1.25)
    leaked[..., 2] += contribution
    return np.clip(leaked, 0.0, exposure_ceiling, out=leaked).astype(np.float32, copy=False), leak_map
    if work_shape != (height, width):
        leak_map = cv2.resize(
            leak_map,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)

    # Base-side entry tends toward red/orange; emulsion-side entry can be much
    # less red. Randomly interpolate those reduced spectral tendencies instead
    # of claiming that every leak has the same colour.
    base_side_mix = float(np.clip(pattern.base_side_mix, 0.0, 1.0))
    leak_color = (
        np.asarray((1.0, 0.92, 0.78), dtype=np.float32) * (1.0 - base_side_mix)
        + np.asarray((1.0, 0.42, 0.15), dtype=np.float32) * base_side_mix
    )
    color_variation = _low_frequency_noise(
        work_shape,
        field_rng,
        max(2.0, float(max(work_shape)) * 0.12),
    )
    if work_shape != (height, width):
        color_variation = cv2.resize(
            color_variation,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32)

    # Apply the scalar exposure contribution channel-by-channel.  This keeps
    # one output RGB frame plus scalar maps instead of a second full RGB color
    # field, while preserving the exposure-side semantics.
    leaked = image_linear.copy()
    contribution = np.empty((height, width), dtype=np.float32)
    np.multiply(leak_map, 0.85 + 0.35 * color_variation, out=contribution)
    contribution *= float(leak_color[0] * 1.25)
    leaked[..., 0] += contribution
    np.multiply(leak_map, 1.08 - 0.16 * color_variation, out=contribution)
    contribution *= float(leak_color[1] * 1.25)
    leaked[..., 1] += contribution
    np.multiply(leak_map, 0.78 + 0.22 * color_variation, out=contribution)
    contribution *= float(leak_color[2] * 1.25)
    leaked[..., 2] += contribution
    return np.clip(leaked, 0.0, exposure_ceiling, out=leaked).astype(np.float32, copy=False), leak_map


def apply_density_accident_components(
    density_cmy: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    film: FilmStockConfig | None = None,
    fast: bool = False,
    work_long_edge: int | None = None,
    defer_compatibility_density: bool = False,
) -> DensityAccidentComponents:
    """Build compatibility density plus component-specific optical metadata."""
    state = build_effective_development(recipe)
    underdevelopment = _bounded_pathology(state.underdevelopment)
    agitation_deficit = _bounded_pathology(state.agitation_deficit)
    clearing_failure = _bounded_pathology(state.clearing_failure)
    residue_factor = _bounded_pathology(state.residue_factor / 3.0)

    stain_pathology = (
        (1.0 + 0.35 * underdevelopment)
        * (1.0 + 0.40 * clearing_failure)
        * (1.0 + 0.35 * residue_factor)
        * (1.0 + 0.20 * agitation_deficit)
    )
    stain_strength = float(np.clip(state.chemical_stain * stain_pathology, 0.0, 1.0))
    plating_strength = float(np.clip(state.silvering_factor, 0.0, 1.5))
    reticulation_strength = float(np.clip(state.reticulation_strength, 0.0, 1.0))
    if (
        stain_strength <= 1e-6
        and plating_strength <= 1e-6
        and reticulation_strength <= 1e-6
    ):
        return DensityAccidentComponents(
            density_cmy=np.asarray(density_cmy, dtype=np.float32),
            maps={},
        )

    rng = rng or np.random.default_rng()
    density_cmy = np.asarray(density_cmy, dtype=np.float32)
    height, width = density_cmy.shape[:2]
    if work_long_edge is None and fast:
        work_long_edge = 1200
    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
    else:
        work_shape = (height, width)

    maps: dict[str, np.ndarray] = {}
    result = density_cmy if defer_compatibility_density else density_cmy.copy()
    scalar_work = (
        None
        if defer_compatibility_density
        else np.empty((height, width), dtype=np.float32)
    )
    stain_scale: tuple[float, float, float] | None = None
    plating_scale: tuple[float, float, float] | None = None
    reticulation_scale: tuple[float, float, float] | None = None

    if stain_strength > 1e-6:
        stain_sigma = max(2.0, float(max(work_shape)) * 0.10 * (1.0 + 0.18 * residue_factor))
        stain_map = _resize_work_map(_stain_deposit_map(work_shape, incident_rng(recipe,rng,"chemical_stain"), stain_sigma), (height, width))
        stain_map = np.clip(0.34 + stain_map * 0.86, 0.0, 1.0).astype(np.float32)
        # CMY density bias: murky retained chemistry leans yellow/green-brown
        # after scanning, while still being stored as a physical negative stain.
        stain_bias = np.asarray((0.08, 0.18, 0.26), dtype=np.float32)
        stain_layer_scale = stain_bias * stain_strength
        if scalar_work is not None:
            for channel, scale_value in enumerate(stain_layer_scale):
                np.multiply(stain_map, float(scale_value), out=scalar_work)
                result[..., channel] += scalar_work
        stain_scale = tuple(float(value) for value in stain_layer_scale)
        maps["chemical_stain"] = stain_map

    if plating_strength > 1e-6:
        plating_rng=incident_rng(recipe,rng,"silver_plating")
        deposit_sigma = max(2.0, float(max(work_shape)) * 0.075)
        deposit = _stain_deposit_map(work_shape, plating_rng, deposit_sigma)
        along_y = bool(plating_rng.integers(0, 2))
        streaks = _axis_streaks(
            work_shape,
            plating_rng,
            along_y=along_y,
            long_sigma=max(5.0, float(max(work_shape)) * 0.20),
            short_sigma=max(0.8, float(min(work_shape)) * 0.006),
        )
        plating_map = np.clip(0.12 + 0.72 * deposit + 0.22 * np.maximum(streaks, 0.0), 0.0, 1.0)
        plating_map = _resize_work_map(plating_map.astype(np.float32), (height, width))
        # Deposited metallic silver is a broad-band RGB optical-density
        # component. On color material, equal layer density is not necessarily
        # optically neutral after the dye-absorption matrix, so solve the layer
        # proxy that observes as equal RGB density. Monochrome/legacy callers
        # can keep the equal-channel fallback.
        if film is not None:
            inverse = pseudoinverse_3x3(film.dye_absorption_matrix)
            plating_layer_scale = np.asarray(inverse, dtype=np.float32) @ np.ones(
                3,
                dtype=np.float32,
            )
            plating_layer_scale *= float(0.20 * plating_strength)
            np.maximum(plating_layer_scale, 0.0, out=plating_layer_scale)
        else:
            plating_layer_scale = np.full(
                3,
                float(0.20 * plating_strength),
                dtype=np.float32,
            )
        if scalar_work is not None:
            for channel, scale_value in enumerate(plating_layer_scale):
                np.multiply(plating_map, float(scale_value), out=scalar_work)
                result[..., channel] += scalar_work
        plating_scale = tuple(float(value) for value in plating_layer_scale)
        maps["silver_plating"] = plating_map.astype(np.float32, copy=False)

    reticulation_optical_scale = 0.0
    if reticulation_strength > 1e-6:
        reticulation_shape = _bounded_control_shape(
            (height, width),
            long_edge=(
                _RETICULATION_CONTROL_LONG_EDGE
                if work_long_edge is None
                else min(int(work_long_edge), _RETICULATION_CONTROL_LONG_EDGE)
            ),
        )
        reticulation_map = _reticulation_texture_map(reticulation_shape, incident_rng(recipe,rng,"reticulation"))
        reticulation_map = _resize_work_map(
            reticulation_map,
            (height, width),
        )
        # Reduced gelatin topography is observed as a near-neutral broadband
        # optical-density perturbation. It does not modify the silver/dye pools
        # and leaves the clear support/base anchor untouched.
        reticulation_optical_scale = float(0.075 * reticulation_strength)
        if film is not None:
            inverse = pseudoinverse_3x3(film.dye_absorption_matrix)
            reticulation_layer_scale = (
                np.asarray(inverse, dtype=np.float32)
                @ np.ones(3, dtype=np.float32)
            )
            reticulation_layer_scale *= reticulation_optical_scale
            np.maximum(
                reticulation_layer_scale,
                0.0,
                out=reticulation_layer_scale,
            )
        else:
            reticulation_layer_scale = np.full(
                3,
                reticulation_optical_scale,
                dtype=np.float32,
            )
        if scalar_work is not None:
            for channel, scale_value in enumerate(reticulation_layer_scale):
                np.multiply(
                    reticulation_map,
                    float(scale_value),
                    out=scalar_work,
                )
                result[..., channel] += scalar_work
        reticulation_scale = tuple(
            float(value) for value in reticulation_layer_scale
        )
        maps["reticulation"] = reticulation_map

    return DensityAccidentComponents(
        density_cmy=(
            result
            if defer_compatibility_density
            else np.maximum(result, 0.0, out=result).astype(np.float32, copy=False)
        ),
        maps=maps,
        chemical_stain_layer_scale=stain_scale,
        surface_silver_density_scale=(0.20 * plating_strength),
        surface_silver_layer_scale=plating_scale,
        reticulation_density_scale=reticulation_optical_scale,
        reticulation_layer_scale=reticulation_scale,
        compatibility_density_deferred=bool(
            defer_compatibility_density and maps
        ),
    )


def compose_density_accident_master(
    density_cmy: np.ndarray,
    maps: dict[str, np.ndarray],
    *,
    chemical_stain_layer_scale: tuple[float, float, float] | None = None,
    surface_silver_layer_scale: tuple[float, float, float] | None = None,
    reticulation_layer_scale: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """Compose the portable layer master after expensive emulsion grain.

    The first additions intentionally preserve the historical accident helper:
    formed layers, chemical stain, then deposited-silver layer proxy. The new
    reticulation proxy follows them. Callers may delay this allocation, but may
    not reorder these additions.
    """
    source = np.asarray(density_cmy, dtype=np.float32)
    result = source.copy()
    scalar_work = np.empty(source.shape[:2], dtype=np.float32)
    stain_map = maps.get("chemical_stain")
    if stain_map is not None and chemical_stain_layer_scale is not None:
        for channel, scale_value in enumerate(chemical_stain_layer_scale):
            np.multiply(stain_map, float(scale_value), out=scalar_work)
            result[..., channel] += scalar_work
    plating_map = maps.get("silver_plating")
    if plating_map is not None and surface_silver_layer_scale is not None:
        for channel, scale_value in enumerate(surface_silver_layer_scale):
            np.multiply(plating_map, float(scale_value), out=scalar_work)
            result[..., channel] += scalar_work
    reticulation_map = maps.get("reticulation")
    if reticulation_map is not None and reticulation_layer_scale is not None:
        for channel, scale_value in enumerate(reticulation_layer_scale):
            np.multiply(reticulation_map, float(scale_value), out=scalar_work)
            result[..., channel] += scalar_work
    return np.maximum(result, 0.0, out=result).astype(np.float32, copy=False)


def apply_density_accidents(
    density_cmy: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    film: FilmStockConfig | None = None,
    fast: bool = False,
    work_long_edge: int | None = None,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Backward-compatible two-value wrapper for density/layer consumers."""
    result = apply_density_accident_components(
        density_cmy,
        recipe,
        rng=rng,
        film=film,
        fast=fast,
        work_long_edge=work_long_edge,
    )
    return result.density_cmy, result.maps


def exposure_occlusion_is_active(recipe: DevelopRecipeConfig) -> bool:
    return any(
        float(getattr(recipe, key, 0.0)) > 1e-6
        for key in EXPOSURE_OCCLUSION_AMOUNT_FIELDS
    )


def apply_exposure_occlusion_to_linear(
    image_linear: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Multiply linearized scene light by optical occlusions before development.

    Dust, hair, insects and debris block incoming exposure after inverse gamma.
    They are not scan dirt, developed-density overlays, or RGB textures.
    An explicit roll/batch identity fixes all four gate obstructions. Legacy
    recipes without it retain fixed hair/insects and per-frame dust/debris.
    """
    image_linear = np.asarray(image_linear, dtype=np.float32)
    if not exposure_occlusion_is_active(recipe):
        return image_linear, None

    height, width = image_linear.shape[:2]
    work_shape = _incident_control_shape((height,width),recipe,_EXPOSURE_OCCLUSION_CONTROL_LONG_EDGE)
    occlusion = np.zeros(work_shape, dtype=np.float32)
    amounts = {
        key: float(np.clip(getattr(recipe, key, 0.0), 0.0, 1.0))
        for key in EXPOSURE_OCCLUSION_AMOUNT_FIELDS
    }
    fixed_gate = recipe.incident_identity_seed is not None or recipe.exposure_occlusion_identity_seed is not None
    needs_frame_rng = not fixed_gate and any(
        amounts[key] > 1e-6 for key in EXPOSURE_OCCLUSION_FRAME_AMOUNT_FIELDS
    )
    if needs_frame_rng:
        rng = rng or np.random.default_rng()
    if amounts["exposure_occlusion_dust"] > 1e-6:
        np.maximum(
            occlusion,
            _dust_occlusion_field(work_shape, amounts["exposure_occlusion_dust"], _occlusion_identity_rng(recipe,_DUST_IDENTITY_SALT) if fixed_gate else rng),
            out=occlusion,
        )
    if amounts["exposure_occlusion_hair"] > 1e-6:
        np.maximum(
            occlusion,
            _hair_occlusion_field(
                work_shape,
                amounts["exposure_occlusion_hair"],
                _occlusion_identity_rng(recipe, _HAIR_IDENTITY_SALT),
            ),
            out=occlusion,
        )
    if amounts["exposure_occlusion_insect"] > 1e-6:
        np.maximum(
            occlusion,
            _insect_occlusion_field(
                work_shape,
                amounts["exposure_occlusion_insect"],
                _occlusion_identity_rng(recipe, _INSECT_IDENTITY_SALT),
            ),
            out=occlusion,
        )
    if amounts["exposure_occlusion_debris"] > 1e-6:
        np.maximum(
            occlusion,
            _debris_occlusion_field(work_shape, amounts["exposure_occlusion_debris"], _occlusion_identity_rng(recipe,_DEBRIS_IDENTITY_SALT) if fixed_gate else rng),
            out=occlusion,
        )
    if float(np.max(occlusion)) > 1e-8:
        occlusion = cv2.GaussianBlur(occlusion, (0, 0), sigmaX=0.55, sigmaY=0.55)
        np.clip(occlusion, 0.0, 1.0, out=occlusion)
    if work_shape != (height, width):
        occlusion = cv2.resize(
            occlusion,
            (width, height),
            interpolation=cv2.INTER_LINEAR,
        ).astype(np.float32, copy=False)
        np.clip(occlusion, 0.0, 1.0, out=occlusion)

    occluded = image_linear.copy()
    occluded *= (1.0 - occlusion)[..., None]
    return occluded, occlusion


def _occlusion_identity_seed(recipe: DevelopRecipeConfig) -> int:
    seed = getattr(recipe, "exposure_occlusion_identity_seed", None)
    if seed is None or str(seed).strip() == "":
        return EXPOSURE_OCCLUSION_FIXED_IDENTITY_SEED if recipe.incident_identity_seed is None else int(recipe.incident_identity_seed)
    return int(seed)


def _occlusion_identity_rng(recipe: DevelopRecipeConfig, salt: int) -> np.random.Generator:
    return np.random.default_rng(int(_occlusion_identity_seed(recipe)) ^ int(salt))


def _dust_occlusion_field(
    shape: tuple[int, int],
    amount: float,
    rng: np.random.Generator,
) -> np.ndarray:
    height, width = shape
    field = np.zeros(shape, dtype=np.float32)
    area = float(height * width)
    count = max(1, int(round(amount * (4.2 + area / 12000.0))))
    for _ in range(count):
        cx = float(rng.uniform(0.0, width - 1.0))
        cy = float(rng.uniform(0.0, height - 1.0))
        radius = float(rng.uniform(0.4, 2.1))
        opacity = float(rng.uniform(0.30, 0.70)) * amount
        cv2.circle(
            field,
            (int(round(cx)), int(round(cy))),
            max(1, int(round(radius))),
            float(opacity),
            thickness=-1,
            lineType=cv2.LINE_AA,
        )
        if float(rng.random()) < 0.45:
            cv2.circle(
                field,
                (
                    int(round(cx + float(rng.uniform(-1.2, 1.2)))),
                    int(round(cy + float(rng.uniform(-1.2, 1.2)))),
                ),
                max(1, int(round(radius * float(rng.uniform(0.45, 0.9))))),
                float(opacity * 0.7),
                thickness=-1,
                lineType=cv2.LINE_AA,
            )
    return field


def _hair_occlusion_field(
    shape: tuple[int, int],
    amount: float,
    rng: np.random.Generator,
) -> np.ndarray:
    height, width = shape
    field = np.zeros(shape, dtype=np.float32)
    count = max(1, int(round(amount * 2.2)))
    short_edge = float(min(height, width))
    for _ in range(count):
        length = float(rng.uniform(0.14, 0.46)) * short_edge
        n_points = int(rng.integers(8, 22))
        points = _hair_polyline(rng, width, height, length, n_points)
        if len(points) < 2:
            continue
        thickness = max(1, int(round(float(rng.uniform(0.7, 1.7)))))
        opacity = float(rng.uniform(0.35, 0.72)) * amount
        cv2.polylines(
            field,
            [points],
            False,
            float(opacity),
            thickness=thickness,
            lineType=cv2.LINE_AA,
        )
    return field


def _hair_polyline(
    rng: np.random.Generator,
    width: int,
    height: int,
    length: float,
    n_points: int,
) -> np.ndarray:
    if float(rng.random()) < 0.74:
        side = int(rng.integers(0, 4))
        if side == 0:
            x = 0.0
            y = float(rng.uniform(0.0, height - 1.0))
            heading = float(rng.uniform(-0.55, 0.55))
        elif side == 1:
            x = float(width - 1)
            y = float(rng.uniform(0.0, height - 1.0))
            heading = float(np.pi + rng.uniform(-0.55, 0.55))
        elif side == 2:
            x = float(rng.uniform(0.0, width - 1.0))
            y = 0.0
            heading = float(np.pi * 0.5 + rng.uniform(-0.55, 0.55))
        else:
            x = float(rng.uniform(0.0, width - 1.0))
            y = float(height - 1)
            heading = float(-np.pi * 0.5 + rng.uniform(-0.55, 0.55))
    else:
        x = float(rng.uniform(0.08, 0.92) * (width - 1.0))
        y = float(rng.uniform(0.08, 0.92) * (height - 1.0))
        heading = float(rng.uniform(0.0, 2.0 * np.pi))
    step = float(length) / float(max(n_points - 1, 1))
    points: list[tuple[int, int]] = []
    for _ in range(int(n_points)):
        points.append(
            (
                int(np.clip(round(x), 0, width - 1)),
                int(np.clip(round(y), 0, height - 1)),
            )
        )
        heading += float(rng.normal(0.0, 0.24))
        x += step * float(np.cos(heading))
        y += step * float(np.sin(heading))
    unique = [points[0]]
    for point in points[1:]:
        if point != unique[-1]:
            unique.append(point)
    return np.asarray(unique, dtype=np.int32)


def _insect_occlusion_field(
    shape: tuple[int, int],
    amount: float,
    rng: np.random.Generator,
) -> np.ndarray:
    height, width = shape
    field = np.zeros(shape, dtype=np.float32)
    count = max(1, int(np.floor(amount * 1.5 + 1e-6)))
    scale = max(0.65, float(min(height, width)) / 420.0)
    for _ in range(count):
        cx = int(round(float(rng.uniform(0.12, 0.88) * (width - 1.0))))
        cy = int(round(float(rng.uniform(0.12, 0.88) * (height - 1.0))))
        body_len = max(3, int(round(float(rng.uniform(4.2, 10.0)) * scale)))
        body_w = max(1, int(round(body_len * float(rng.uniform(0.28, 0.42)))))
        angle = float(rng.uniform(0.0, 180.0))
        opacity = float(rng.uniform(0.4, 0.72)) * amount
        cv2.ellipse(
            field,
            (cx, cy),
            (body_len, body_w),
            angle,
            0,
            360,
            float(opacity),
            thickness=-1,
            lineType=cv2.LINE_AA,
        )
        wing_len = max(2, int(round(body_len * 0.7)))
        wing_w = max(1, int(round(body_w * 1.15)))
        rad = np.deg2rad(angle)
        dx = int(round(np.cos(rad + np.pi * 0.5) * body_w * 1.1))
        dy = int(round(np.sin(rad + np.pi * 0.5) * body_w * 1.1))
        for sign in (-1, 1):
            cv2.ellipse(
                field,
                (int(np.clip(cx + sign * dx, 0, width - 1)), int(np.clip(cy + sign * dy, 0, height - 1))),
                (wing_len, wing_w),
                angle + float(sign) * 18.0,
                0,
                360,
                float(opacity * 0.42),
                thickness=-1,
                lineType=cv2.LINE_AA,
            )
    return field


def _debris_occlusion_field(
    shape: tuple[int, int],
    amount: float,
    rng: np.random.Generator,
) -> np.ndarray:
    height, width = shape
    field = np.zeros(shape, dtype=np.float32)
    count = max(1, int(round(amount * 7.0)))
    scale = max(0.7, float(min(height, width)) / 380.0)
    for _ in range(count):
        cx = float(rng.uniform(0.04, 0.96) * (width - 1.0))
        cy = float(rng.uniform(0.04, 0.96) * (height - 1.0))
        radius = float(rng.uniform(1.6, 5.8)) * scale
        n_verts = int(rng.integers(4, 8))
        angles = np.sort(rng.uniform(0.0, 2.0 * np.pi, size=n_verts))
        radii = radius * rng.uniform(0.45, 1.15, size=n_verts)
        xs = np.clip(np.round(cx + radii * np.cos(angles)), 0, width - 1).astype(np.int32)
        ys = np.clip(np.round(cy + radii * np.sin(angles)), 0, height - 1).astype(np.int32)
        polygon = np.stack((xs, ys), axis=1)
        opacity = float(rng.uniform(0.28, 0.66)) * amount
        cv2.fillPoly(field, [polygon], float(opacity), lineType=cv2.LINE_AA)
    return field

