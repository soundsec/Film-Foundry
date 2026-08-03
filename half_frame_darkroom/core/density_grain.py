"""遵循密度统计关系的随机颗粒核心。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.development import build_effective_development
from half_frame_darkroom.core.execution_topology import reference_execution_enabled
from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.core.random_fields import (
    fill_standard_normal_float32,
    standard_normal_float32,
)
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


DEFAULT_DENSITY_GRAIN_RADIUS = 0.0014
LAYER_SHARED_GRAIN_MIX = 0.32

try:
    # NumPy 2.x accepts an already computed mean and avoids repeating the
    # reduction inside std().  Keep the established NumPy 1.x formula as a
    # compatibility fallback rather than raising the package requirement.
    np.std(
        np.zeros(1, dtype=np.float32),
        mean=np.float32(0.0),
    )
except TypeError:  # pragma: no cover - exercised by supported NumPy 1.x
    _STD_ACCEPTS_PRECOMPUTED_MEAN = False
else:
    _STD_ACCEPTS_PRECOMPUTED_MEAN = True


def _grain_scales_for_film(film: FilmStockConfig, radius_factor: float) -> tuple[np.ndarray, np.ndarray]:
    scales = np.asarray(getattr(film, "grain_scales", ()), dtype=np.float32)
    weights = np.asarray(getattr(film, "grain_scale_weights", ()), dtype=np.float32)
    if scales.size == 0 or weights.size == 0:
        scales = np.asarray((film.grain_density_correlation_radius,), dtype=np.float32)
        weights = np.asarray((1.0,), dtype=np.float32)
    count = int(min(scales.size, weights.size))
    scales = scales[:count]
    weights = weights[:count]
    valid = (scales > 0.0) & (weights > 0.0)
    if not bool(np.any(valid)):
        scales = np.asarray((film.grain_density_correlation_radius,), dtype=np.float32)
        weights = np.asarray((1.0,), dtype=np.float32)
    else:
        scales = scales[valid]
        weights = weights[valid]

    radius_gain = float(film.grain_density_correlation_radius) / DEFAULT_DENSITY_GRAIN_RADIUS
    scales = np.clip(scales * radius_gain * float(radius_factor), 1e-5, 0.08).astype(
        np.float32,
        copy=False,
    )
    weights = weights / max(float(np.sqrt(np.sum(weights * weights))), 1e-6)
    return scales, weights.astype(np.float32, copy=False)


def _smoothstep(values: np.ndarray, edge0: float, edge1: float) -> np.ndarray:
    x = np.subtract(values, float(edge0), dtype=np.float32)
    x /= max(float(edge1) - float(edge0), 1e-6)
    np.clip(x, 0.0, 1.0, out=x)
    curve = np.multiply(x, x, dtype=np.float32)
    x *= -2.0
    x += 3.0
    curve *= x
    return curve


def _density_response_level(
    density_layers: np.ndarray,
    d_min: np.ndarray,
    d_max: np.ndarray,
) -> np.ndarray:
    """Return the established mean normalized layer-density response.

    The optimized topology evaluates one layer at a time with one reusable
    scalar scratch.  This preserves the exact float32 subtract/divide/clip and
    left-to-right three-layer mean order without retaining an otherwise
    unobserved HxWx3 normalized field.  The developer reference topology keeps
    the former vector expression executable for same-version A/B audits.
    """
    density = np.asarray(density_layers, dtype=np.float32)
    lower = np.asarray(d_min, dtype=np.float32).reshape(3)
    upper = np.asarray(d_max, dtype=np.float32).reshape(3)
    density_range = np.maximum(upper - lower, 1e-6)
    if reference_execution_enabled():
        normalized = np.subtract(density, lower, dtype=np.float32)
        normalized /= density_range
        np.clip(normalized, 0.0, 1.0, out=normalized)
        return np.mean(normalized, axis=-1)

    level = np.subtract(density[..., 0], lower[0], dtype=np.float32)
    level /= density_range[0]
    np.clip(level, 0.0, 1.0, out=level)
    scratch = np.empty_like(level, dtype=np.float32)
    for channel in range(1, 3):
        np.subtract(density[..., channel], lower[channel], out=scratch)
        scratch /= density_range[channel]
        np.clip(scratch, 0.0, 1.0, out=scratch)
        level += scratch
    level /= np.float32(3.0)
    return level


def _apply_shadow_noise_scale(
    shadow_noise: np.ndarray,
    shadow_weight: np.ndarray,
    sigma_base: np.ndarray,
    grain_factor: float,
) -> None:
    """Apply the established chromatic coarse-shadow grain amplitude.

    The amplitude is separable into one scalar spatial weight and three
    channel constants.  Optimized execution therefore reuses one scalar
    scratch per channel; reference execution retains the former expanded RGB
    amplitude field for same-version allocation and exact-value A/B.
    """
    shadow_chroma = np.asarray(
        (1.18, 0.92, 1.10),
        dtype=np.float32,
    ).reshape(3)
    if reference_execution_enabled():
        shadow_sigma = (
            np.asarray(sigma_base, dtype=np.float32).reshape(1, 1, 3)
            * float(grain_factor)
            * shadow_chroma.reshape(1, 1, 3)
            * np.asarray(shadow_weight, dtype=np.float32)[..., None]
        )
        shadow_sigma *= 0.42
        shadow_noise *= shadow_sigma
        return

    channel_scale = np.asarray(sigma_base, dtype=np.float32).reshape(3).copy()
    channel_scale *= float(grain_factor)
    channel_scale *= shadow_chroma
    scalar_scale = np.empty_like(shadow_weight, dtype=np.float32)
    for channel in range(3):
        np.multiply(shadow_weight, channel_scale[channel], out=scalar_scale)
        scalar_scale *= 0.42
        shadow_noise[..., channel] *= scalar_scale


def _normalize_unit_field(field: np.ndarray) -> np.ndarray:
    field = np.asarray(field, dtype=np.float32)
    # All callers pass freshly allocated random/blur/mix buffers.  Normalize
    # those buffers in place so a work-grid-sized subtraction and division do
    # not each create another temporary array.
    mean_value = np.mean(field)
    if _STD_ACCEPTS_PRECOMPUTED_MEAN:
        std_value = np.std(field, mean=mean_value)
        field -= float(mean_value)
    else:
        field -= float(mean_value)
        std_value = np.std(field)
    field /= max(float(std_value), 1e-6)
    return field


def _subpixel_shift(
    field: np.ndarray,
    dx: float,
    dy: float,
    *,
    out: np.ndarray | None = None,
) -> np.ndarray:
    matrix = np.asarray(((1.0, 0.0, float(dx)), (0.0, 1.0, float(dy))), dtype=np.float32)
    destination = None if out is None else np.asarray(out, dtype=np.float32)
    return cv2.warpAffine(
        np.asarray(field, dtype=np.float32),
        matrix,
        (field.shape[1], field.shape[0]),
        dst=destination,
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT_101,
    ).astype(np.float32, copy=False)


def _blurred_multilayer_field(
    rng: np.random.Generator,
    work_shape: tuple[int, int],
    scale: float,
    shared_mix: float = LAYER_SHARED_GRAIN_MIX,
    *,
    out: np.ndarray | None = None,
) -> np.ndarray:
    blur_sigma = radius_to_sigma(float(scale), (*work_shape, 3))
    max_shift = float(np.clip(blur_sigma * 0.42, 0.35, 2.25))

    # Generator.normal(0, 1) and standard_normal consume the same bit stream
    # and produce the same float64 values. The latter avoids redundant
    # location/scale handling before the required float32 storage conversion.
    shared = standard_normal_float32(rng, work_shape)
    cv2.GaussianBlur(
        shared,
        (0, 0),
        sigmaX=blur_sigma,
        sigmaY=blur_sigma,
        dst=shared,
    )
    shared = _normalize_unit_field(shared)

    # Compose one channel at a time.  Keeping separate work-grid-sized shared
    # and independent RGB stacks used to add two redundant 3-channel buffers
    # per scale, even though neither stack was observed independently.
    if out is None:
        mixed_channels = np.empty((*work_shape, 3), dtype=np.float32)
    else:
        mixed_channels = np.asarray(out)
        if (
            mixed_channels.shape != (*work_shape, 3)
            or mixed_channels.dtype != np.float32
            or not mixed_channels.flags.c_contiguous
            or not mixed_channels.flags.writeable
        ):
            raise ValueError(
                "grain field output must be writable C-contiguous float32 "
                f"with shape {(*work_shape, 3)}"
            )
    shifted = np.empty(work_shape, dtype=np.float32)
    shared_mix = float(np.clip(shared_mix, 0.0, 1.0))
    layer_mix = float(np.sqrt(max(1.0 - shared_mix * shared_mix, 0.0)))
    layer: np.ndarray | None = None
    for channel in range(3):
        dx, dy = rng.uniform(-max_shift, max_shift, 2)
        _subpixel_shift(
            shared,
            dx=float(dx),
            dy=float(dy),
            out=shifted,
        )
        shifted *= shared_mix
        mixed_channel = mixed_channels[..., channel]
        np.copyto(mixed_channel, shifted)

        if layer is None:
            layer = standard_normal_float32(rng, work_shape)
        else:
            fill_standard_normal_float32(rng, layer)
        cv2.GaussianBlur(
            layer,
            (0, 0),
            sigmaX=blur_sigma,
            sigmaY=blur_sigma,
            dst=layer,
        )
        dx, dy = rng.uniform(-max_shift, max_shift, 2)
        _subpixel_shift(
            _normalize_unit_field(layer),
            dx=float(dx),
            dy=float(dy),
            out=shifted,
        )
        # The shared contribution is already frozen in its destination
        # channel, so the same scalar shift buffer can now hold the independent
        # layer.  The multiply-then-add order is unchanged from the two-buffer
        # expression, preserving the seeded grain field exactly.
        shifted *= layer_mix
        mixed_channel += shifted

        # ``warpAffine`` overwrites the shift buffer and the next random
        # conversion overwrites ``layer`` on the next channel.
    del shared, shifted, layer
    return _normalize_unit_field(mixed_channels)


def apply_density_grain(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    chemistry: ChemistryConfig,
    rng: np.random.Generator | None = None,
    fast: bool = False,
    work_long_edge: int | None = None,
    image_polarity: str = "negative",
    component_scope: str = "combined_legacy",
    consume_input_as_effective_delta: bool = False,
) -> np.ndarray:
    """在密度域加入颗粒扰动，sigma_D 与 sqrt(D) 绑定。

    ``component_scope='emulsion'`` excludes fixer/stain/surface-silver residue
    terms. The authoritative RGB path uses that scope and composes those
    post-process materials separately; ``combined_legacy`` preserves the old
    standalone helper behaviour.
    """
    source = np.asarray(density_cmy)
    if consume_input_as_effective_delta and (
        source.dtype != np.float32
        or not source.flags.c_contiguous
        or not source.flags.writeable
    ):
        raise ValueError(
            "consumed grain input must be writable C-contiguous float32"
        )
    density_cmy = np.asarray(source, dtype=np.float32)
    rng = rng or np.random.default_rng()
    height, width = density_cmy.shape[:2]

    if work_long_edge is None and fast:
        work_long_edge = 1600

    if work_long_edge is not None and int(work_long_edge) > 0 and max(height, width) > int(work_long_edge):
        scale = float(work_long_edge) / float(max(height, width))
        work_shape = (max(1, int(round(height * scale))), max(1, int(round(width * scale))))
        small_density = cv2.resize(density_cmy, (work_shape[1], work_shape[0]), interpolation=cv2.INTER_AREA)
    else:
        work_shape = (height, width)
        small_density = density_cmy

    development = build_effective_development(chemistry)
    scope = str(component_scope).strip().lower()
    if scope not in {"combined_legacy", "emulsion"}:
        raise ValueError(f"unsupported grain component_scope: {component_scope}")
    d_min = np.asarray(film.density_min, dtype=np.float32) + development.d_min_shift
    d_max = np.asarray(film.density_max, dtype=np.float32)
    degradation = float(np.clip(getattr(film, "material_degradation", 0.0), 0.0, 1.0))
    sigma_base = np.asarray(film.granularity_sigma, dtype=np.float32) * (1.0 + 0.35 * degradation)
    density_level = _density_response_level(small_density, d_min, d_max)

    grain_factor = float(development.grain_factor)
    grain_radius_factor = float(development.grain_radius_factor)
    if scope == "emulsion":
        # These controls describe material left on top of, or alongside, the
        # already formed emulsion. They must not masquerade as dye-layer grain.
        fixer_exhaustion = float(np.clip(chemistry.fixer_exhaustion, 0.0, 1.0))
        silver_retention = float(np.clip(chemistry.silver_retention, 0.0, 1.0))
        chemical_stain = float(np.clip(chemistry.chemical_stain, 0.0, 1.0))
        post_process_gain = (
            (1.0 + 0.30 * fixer_exhaustion + 0.26 * silver_retention)
            * (1.0 + 0.85 * chemical_stain)
        )
        grain_factor /= max(post_process_gain, 1e-6)
        grain_radius_factor /= max(1.0 + 0.16 * silver_retention, 1e-6)
    # Granularity rises out of D-min but must not grow unchecked after the
    # material approaches its finite density capacity. Keep the established
    # density trend through the useful range, then taper stochastic amplitude
    # near D-max. Positive materials use the stronger taper because their dense
    # endpoint is the blocked shadow and should be fine/tight rather than a
    # coarse noisy plateau.
    positive_medium = str(image_polarity).strip().lower() == "positive"
    endpoint_taper = 0.55 if positive_medium else 0.20
    endpoint_weight = _smoothstep(density_level, 0.70, 1.0)
    endpoint_weight *= endpoint_taper
    np.subtract(1.0, endpoint_weight, out=endpoint_weight)
    defer_sigma_until_after_main_scales = bool(
        not reference_execution_enabled()
        and positive_medium
        and work_shape != (height, width)
    )
    sigma: np.ndarray | None
    if defer_sigma_until_after_main_scales:
        # ``density_level`` has completed the first resized-density read.
        # Recreate that deterministic INTER_AREA field only after the main
        # grain scales finish, then overwrite their dead RGB scratch with the
        # amplitude. This removes one work-grid RGB field from the stochastic
        # construction peak without changing a random call or density formula.
        sigma = None
        del small_density
    else:
        sigma = np.subtract(small_density, d_min, dtype=np.float32)
        np.maximum(sigma, 0.0, out=sigma)
        np.sqrt(sigma, out=sigma)
        sigma *= (sigma_base * grain_factor).reshape(1, 1, 3)
        sigma *= endpoint_weight[..., None]
        del small_density
    unit_noise: np.ndarray | None = None
    reusable_field: np.ndarray | None = None
    grain_scales, grain_weights = _grain_scales_for_film(film, grain_radius_factor)
    for scale, weight in zip(grain_scales, grain_weights, strict=False):
        field = _blurred_multilayer_field(
            rng,
            work_shape,
            float(scale),
            out=reusable_field,
        )
        field *= float(weight)
        if unit_noise is None:
            # The first weighted field is already a private accumulator.
            # Adding it to a newly allocated all-zero RGB field only burns one
            # complete memory pass and cannot change the result.
            unit_noise = field
        else:
            unit_noise += field
            # Once accumulated, this complete RGB field has no observer and
            # can be overwritten by the next scale rather than released and
            # reallocated.
            reusable_field = field
        del field
    if unit_noise is None:
        # _grain_scales_for_film always supplies one valid scale; retain an
        # explicit defensive boundary for malformed third-party film objects.
        unit_noise = np.zeros((*work_shape, 3), dtype=np.float32)
    if sigma is None:
        if reusable_field is None:
            reusable_field = np.empty((*work_shape, 3), dtype=np.float32)
        cv2.resize(
            density_cmy,
            (work_shape[1], work_shape[0]),
            dst=reusable_field,
            interpolation=cv2.INTER_AREA,
        )
        sigma = reusable_field
        sigma -= d_min
        np.maximum(sigma, 0.0, out=sigma)
        np.sqrt(sigma, out=sigma)
        sigma *= (sigma_base * grain_factor).reshape(1, 1, 3)
        sigma *= endpoint_weight[..., None]
    del endpoint_weight
    unit_noise *= sigma
    del sigma
    noise = unit_noise

    # The coarse low-density component represents negative shadow clumping.
    # In a dye positive, low density is the highlight, so retaining this term
    # would incorrectly put coarse negative-shadow grain into slide highlights.
    shadow_weight = None
    if not positive_medium:
        shadow_weight = _smoothstep(density_level, 0.10, 0.46)
        np.subtract(1.0, shadow_weight, out=shadow_weight)
    del density_level
    if shadow_weight is not None and float(np.max(shadow_weight)) > 1e-6:
        shadow_noise: np.ndarray | None = None
        coarse_scales = np.clip(grain_scales * 2.8, 1e-5, 0.10)
        coarse_weights = grain_weights / max(float(np.sqrt(np.sum(grain_weights * grain_weights))), 1e-6)
        for scale, weight in zip(coarse_scales, coarse_weights, strict=False):
            field = _blurred_multilayer_field(
                rng,
                work_shape,
                float(scale),
                shared_mix=0.46,
                out=reusable_field,
            )
            field *= float(weight)
            if shadow_noise is None:
                shadow_noise = field
                # The former main-scale scratch now owns the shadow
                # accumulator; the next coarse scale requires a distinct
                # scratch because the accumulator must remain intact.
                reusable_field = None
            else:
                shadow_noise += field
                reusable_field = field
            del field
        if shadow_noise is None:
            shadow_noise = np.zeros((*work_shape, 3), dtype=np.float32)
        if not reference_execution_enabled():
            # The last completed coarse scale is retained only as reusable
            # scratch. No later scale exists, so release that RGB field before
            # applying the shadow accumulator's amplitude.
            reusable_field = None
        _apply_shadow_noise_scale(
            shadow_noise,
            shadow_weight,
            sigma_base,
            grain_factor,
        )
        del shadow_weight
        noise += shadow_noise
        del shadow_noise
    del reusable_field

    if scope == "combined_legacy" and development.residue_factor > 1e-6:
        residue_sigma = radius_to_sigma(0.010 * development.grain_radius_factor, (*work_shape, 3))
        residue = standard_normal_float32(rng, work_shape)
        cv2.GaussianBlur(
            residue,
            (0, 0),
            sigmaX=residue_sigma,
            sigmaY=residue_sigma,
            dst=residue,
        )
        residue -= float(np.min(residue))
        residue /= max(float(np.max(residue)), 1e-6)
        residue -= 0.35
        np.clip(residue, 0.0, 1.0, out=residue)
        residue_strength = 0.018 * float(development.residue_factor)
        residue *= residue_strength
        noise += residue[..., None]
        del residue

    if consume_input_as_effective_delta:
        # Immediate optical-only formation has no layer-master consumer after
        # the grain contribution is observed. Reuse that private formed-layer
        # buffer as the effective grain delta:
        #
        #   max(formed + resized_noise, 0) - formed
        #
        # This is exactly the delta computed by the ordinary caller after
        # ``apply_density_grain`` returns. At reduced work size, resize one
        # channel at a time so a scalar full-frame scratch replaces a second
        # full RGB master. OpenCV's linear resize is channel-separable; tests
        # require byte-for-byte equality with the ordinary RGB resize path.
        if work_shape != (height, width):
            resized_channel = np.empty((height, width), dtype=np.float32)
            for channel in range(3):
                cv2.resize(
                    noise[..., channel],
                    (width, height),
                    dst=resized_channel,
                    interpolation=cv2.INTER_LINEAR,
                )
                formed_channel = density_cmy[..., channel]
                resized_channel += formed_channel
                np.maximum(resized_channel, 0.0, out=resized_channel)
                resized_channel -= formed_channel
                np.copyto(formed_channel, resized_channel)
            del resized_channel
        else:
            noise += density_cmy
            np.maximum(noise, 0.0, out=noise)
            noise -= density_cmy
            np.copyto(density_cmy, noise)
        return density_cmy

    if work_shape != (height, width):
        # Resize directly into the only full-resolution result buffer.  The
        # previous resize-then-add sequence briefly retained both an enlarged
        # noise frame and a second full-sized result frame (about 458 MiB each
        # at 40 MP RGB float32).
        result = np.empty_like(density_cmy, dtype=np.float32)
        cv2.resize(
            noise,
            (width, height),
            dst=result,
            interpolation=cv2.INTER_LINEAR,
        )
    else:
        # ``noise`` is already a private buffer when no resize is required.
        result = noise

    result += density_cmy
    np.maximum(result, 0.0, out=result)
    return result
