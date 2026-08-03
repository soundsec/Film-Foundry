"""Intentional darkroom accident effects.

These helpers model expert-mode accidents as part of negative formation. Dirty
chemistry and uneven development are modulated by EffectiveDevelopmentState, so
bad process conditions amplify the base accident tendency. They are deliberately
bounded so playful controls can be pulled hard without making the numeric
pipeline unstable.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from half_frame_darkroom.core.derived_cache import pseudoinverse_3x3

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.model.config import DevelopRecipeConfig, FilmStockConfig


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


_MAX_RANDOM_FIELD_SIGMA = 32.0
_LIGHT_LEAK_CONTROL_LONG_EDGE = 1600


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


def sample_light_leak_pattern(
    rng: np.random.Generator,
) -> LightLeakPatternSpec:
    """Sample one local-entry leak identity independently of raster size."""
    side_weights = np.zeros(4, dtype=np.float32)
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


def uneven_development_rate_field(
    image_linear: np.ndarray,
    recipe: DevelopRecipeConfig,
    rng: np.random.Generator | None = None,
    *,
    fast: bool = False,
    work_long_edge: int | None = None,
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

    image_linear = np.asarray(image_linear, dtype=np.float32)
    height, width = image_linear.shape[:2]
    if work_long_edge is None and fast:
        work_long_edge = 1200
    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
    else:
        work_shape = (height, width)

    rng = rng or np.random.default_rng()
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
) -> tuple[np.ndarray, np.ndarray | None]:
    """Add light-leak exposure before H-D density formation."""
    state = build_effective_development(recipe)
    strength = float(state.light_leak_strength)
    if strength <= 1e-6:
        return np.asarray(image_linear, dtype=np.float32), None

    rng = rng or np.random.default_rng()
    pattern = pattern or sample_light_leak_pattern(rng)
    if pattern.template_key != "local_edge":
        raise ValueError(f"Unsupported light-leak template: {pattern.template_key}")
    side_weights = np.asarray(pattern.side_weights, dtype=np.float32)
    if side_weights.shape != (4,) or not np.all(np.isfinite(side_weights)) or np.any(side_weights < 0.0):
        raise ValueError("Light-leak side weights must contain four finite non-negative values.")
    if not any(float(value) > 0.0 for value in side_weights):
        raise ValueError("Light-leak pattern must activate at least one entry side.")
    if len(pattern.edge_widths) != 4 or not np.all(np.isfinite(pattern.edge_widths)):
        raise ValueError("Light-leak edge widths must contain four finite values.")
    field_rng = np.random.default_rng(int(pattern.texture_seed))
    image_linear = np.asarray(image_linear, dtype=np.float32)
    height, width = image_linear.shape[:2]
    work_shape = _bounded_control_shape(
        (height, width),
        long_edge=_LIGHT_LEAK_CONTROL_LONG_EDGE,
    )
    work_height, work_width = work_shape
    xx = np.linspace(0.0, 1.0, work_width, dtype=np.float32)[None, :]
    yy = np.linspace(0.0, 1.0, work_height, dtype=np.float32)[:, None]

    # A real leak normally has a local entry path.  The structural identity is
    # sampled independently of raster size; only its band-limited scalar field
    # is evaluated on the bounded global control grid.
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

    blob_sigma = max(3.0, float(max(work_shape)) * 0.18)
    blobs = _low_frequency_noise(work_shape, field_rng, blob_sigma)
    ragged_edge = _edge_leak_mask(
        work_shape,
        xx,
        yy,
        field_rng,
        side_weights,
        pattern.edge_widths,
    )
    leak_map = np.clip(
        np.maximum(smooth_edge * 0.48, ragged_edge * 0.86) + corner * 0.36 + blobs * 0.18,
        0.0,
        1.0,
    )
    leak_map = cv2.GaussianBlur(leak_map, (0, 0), sigmaX=blob_sigma * 0.045, sigmaY=blob_sigma * 0.045)
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
    return np.clip(leaked, 0.0, 4.0, out=leaked).astype(np.float32, copy=False), leak_map


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
    if stain_strength <= 1e-6 and plating_strength <= 1e-6:
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

    if stain_strength > 1e-6:
        stain_sigma = max(2.0, float(max(work_shape)) * 0.10 * (1.0 + 0.18 * residue_factor))
        stain_map = _resize_work_map(_stain_deposit_map(work_shape, rng, stain_sigma), (height, width))
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
        deposit_sigma = max(2.0, float(max(work_shape)) * 0.075)
        deposit = _stain_deposit_map(work_shape, rng, deposit_sigma)
        along_y = bool(rng.integers(0, 2))
        streaks = _axis_streaks(
            work_shape,
            rng,
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
) -> np.ndarray:
    """Compose the portable layer master after expensive emulsion grain.

    The channel order intentionally matches the historical accident helper:
    formed layers, then chemical stain, then deposited-silver layer proxy.
    Callers may delay this allocation, but may not reorder these additions.
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
