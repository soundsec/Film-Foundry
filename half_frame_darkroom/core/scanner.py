"""扫描 / 数字化阶段。"""

from __future__ import annotations

import numpy as np

from half_frame_darkroom.core.color import luminance
from half_frame_darkroom.core.execution_topology import reference_execution_enabled
from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


_IDENTITY_RGB_MATRIX = np.eye(3, dtype=np.float32)
_IDENTITY_RGB_MATRIX.setflags(write=False)
_UNIT_RGB_GAMMA = np.ones((1, 1, 3), dtype=np.float32)
_UNIT_RGB_GAMMA.setflags(write=False)


def _is_identity_rgb_matrix(matrix: np.ndarray) -> bool:
    return bool(np.array_equal(matrix, _IDENTITY_RGB_MATRIX))


def layer_density_to_optical_density_rgb(
    density_layers: np.ndarray,
    film: FilmStockConfig,
    *,
    base_density_rgb: np.ndarray | tuple[float, float, float] | None = None,
    consume_input: bool = False,
) -> np.ndarray:
    """Observe image-dye layer density through a specified final film base.

    Uniform base density remains an exactly known anchor.  For a coloured
    base, broad response bands change the effective projection of overlapping
    dye absorptions even after that anchor is divided out.  This three-band
    reduction preserves the legacy result for a neutral base or zero
    interaction strength. ``consume_input`` is an internal ownership transfer:
    the coloured-base path may overwrite the caller's writable layer array
    after its final observation in order to bound one tile-sized temporary.
    """
    density = np.asarray(density_layers, dtype=np.float32)
    if density.ndim != 3 or density.shape[-1] != 3:
        raise ValueError(f"density_layers must have HxWx3 shape, got {density.shape}")
    if consume_input and not bool(density.flags.writeable):
        raise ValueError("consume_input requires a writable private density_layers array")
    reference_topology = reference_execution_enabled()
    if reference_topology:
        consume_input = False
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32).reshape(3, 3)
    base_density = np.clip(
        np.asarray(
            film.film_base_density_rgb if base_density_rgb is None else base_density_rgb,
            dtype=np.float32,
        ).reshape(3),
        0.0,
        None,
    )
    strength = float(
        np.clip(getattr(film, "base_dye_interaction_strength", 0.0), 0.0, 1.0)
    )
    legacy_additive = strength <= 1e-7 or float(np.ptp(base_density)) <= 1e-7

    if not legacy_additive:
        overlap = np.clip(
            np.asarray(film.base_dye_interaction_matrix, dtype=np.float32).reshape(3, 3),
            0.0,
            None,
        )
        row_sums = np.sum(overlap, axis=1, keepdims=True)
        overlap = overlap / np.maximum(row_sums, 1e-6)
        base_t = np.power(10.0, -base_density).astype(np.float32, copy=False)
        base_response = np.maximum(overlap @ base_t, 1e-6)
        base_weighted_overlap = overlap * base_t.reshape(1, 3)

    height, width = density.shape[:2]
    output = np.empty((height, width, 3), dtype=np.float32)
    # Bound temporary spectral-proxy arrays to roughly two megapixels even
    # when the outer scan path has not switched to its larger-stage tiler.
    tile_rows = height
    if height * width > 2_000_000:
        tile_rows = max(1, min(height, int(2_000_000 / max(width, 1))))
    for start in range(0, height, tile_rows):
        stop = min(start + tile_rows, height)
        if legacy_additive:
            dye_tile = output[start:stop]
            np.einsum(
                "...l,rl->...r",
                density[start:stop],
                absorption,
                out=dye_tile,
            )
            dye_tile += base_density.reshape(1, 1, 3)
            np.maximum(dye_tile, 0.0, out=dye_tile)
            continue

        source_tile = density[start:stop]
        dye_tile = output[start:stop]
        np.einsum(
            "...l,rl->...r",
            source_tile,
            absorption,
            out=dye_tile,
        )
        np.maximum(dye_tile, 0.0, out=dye_tile)
        dye_tile *= -1.0
        np.power(10.0, dye_tile, out=dye_tile)  # reuse as dye transmittance
        # Resolve all three observation bands together. The reduction order
        # inside each row is identical to the former per-channel einsums, but
        # two vectorized contractions replace six scalar contractions. A
        # formation adapter may explicitly transfer its no-longer-observed
        # layer tile so it can hold the temporary reference response.
        reference = (
            source_tile
            if consume_input
            else np.empty_like(source_tile, dtype=np.float32)
        )
        np.einsum("...j,rj->...r", dye_tile, overlap, out=reference)
        coupled = np.einsum(
            "...j,rj->...r",
            dye_tile,
            base_weighted_overlap,
        ).astype(np.float32, copy=False)
        coupled /= base_response.reshape(1, 1, 3)
        np.maximum(reference, 1e-6, out=reference)
        np.divide(coupled, reference, out=coupled)
        np.clip(coupled, 1e-4, 1e4, out=coupled)
        np.power(coupled, strength, out=coupled)
        if reference_topology:
            coupled *= dye_tile
            np.clip(coupled, 1e-6, 1.0, out=coupled)
            np.log10(coupled, out=coupled)
            coupled *= -1.0
            coupled += base_density.reshape(1, 1, 3)
            np.copyto(dye_tile, coupled)
        else:
            # This is the final consumer of dye transmittance.  Let the owned
            # output tile receive the same pointwise product and continue the
            # unchanged clip/log/base sequence there, instead of finishing in
            # a second RGB tile and copying every value back.
            np.multiply(coupled, dye_tile, out=dye_tile)
            np.clip(dye_tile, 1e-6, 1.0, out=dye_tile)
            np.log10(dye_tile, out=dye_tile)
            dye_tile *= -1.0
            dye_tile += base_density.reshape(1, 1, 3)
    np.maximum(output, 0.0, out=output)
    return output


def negative_total_density_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """Compatibility CMY master + configured base -> RGB optical density."""
    return layer_density_to_optical_density_rgb(density_cmy, film)


def negative_transmittance_rgb(density_cmy: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """底片总密度 -> 透过率 T = 10^-D。"""
    return np.power(10.0, -negative_total_density_rgb(density_cmy, film)).astype(
        np.float32,
        copy=False,
    )


def capture_transmitted_medium(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
    *,
    illuminant_rgb: np.ndarray | tuple[float, float, float] | None = None,
) -> np.ndarray:
    """Capture a transmissive medium before positive/negative interpretation.

    This shared physical stage observes immutable developed-medium density
    through an illuminant and sensor response. It does not remove a mask,
    invert a negative, or write back to the developed medium.
    """
    total_density_rgb = negative_total_density_rgb(density_cmy, film)
    return capture_optical_density(
        total_density_rgb,
        scanner,
        illuminant_rgb=illuminant_rgb,
    )


def capture_optical_density(
    total_density_rgb: np.ndarray,
    scanner: ScannerConfig | None = None,
    *,
    illuminant_rgb: np.ndarray | tuple[float, float, float] | None = None,
    _out: np.ndarray | None = None,
) -> np.ndarray:
    """Capture an already formed RGB optical-density field.

    This is the shared physical sampling stage used when a caller also needs
    to retain the exact density field.  It avoids reconstructing the final
    medium twice and does not perform mask removal or polarity interpretation.
    """
    scanner = scanner or ScannerConfig()
    total_density_rgb = np.asarray(total_density_rgb, dtype=np.float32)
    # The negated-density field is a private work buffer. Reuse it first for
    # transmittance and then for illuminated transmittance; this preserves the
    # physical sampling formula while avoiding two full RGB temporaries.
    if _out is None:
        transmittance = np.negative(total_density_rgb, dtype=np.float32)
    else:
        transmittance = np.asarray(_out)
        if (
            transmittance.shape != total_density_rgb.shape
            or transmittance.dtype != np.float32
            or not transmittance.flags.writeable
        ):
            raise ValueError(
                "capture output must be a writable float32 array matching total density"
            )
        np.negative(total_density_rgb, out=transmittance)
    np.power(10.0, transmittance, out=transmittance)
    if illuminant_rgb is None:
        illuminant_rgb = scanner.scanner_light_color
    light = np.asarray(illuminant_rgb, dtype=np.float32).reshape(1, 1, 3)
    response = np.asarray(scanner.scanner_response_matrix, dtype=np.float32).reshape(3, 3)
    transmittance *= light
    if _is_identity_rgb_matrix(response):
        # The illuminated transmittance is already a private sampling buffer.
        # Multiplication by an exact identity matrix is value-preserving, so
        # keep the same buffer rather than performing a full RGB contraction.
        raw = transmittance
    else:
        raw = np.einsum("...c,rc->...r", transmittance, response).astype(
            np.float32,
            copy=False,
        )
    # scanner_raw is a normalized sensor signal. Saturation belongs here so
    # the image and its clear-base reference clip under the same exposure.
    np.clip(raw, 1e-6, 1.0, out=raw)
    return raw


def render_negative_image(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """Capture a negative transparency through the calibrated backlight."""
    scanner = scanner or ScannerConfig()
    return capture_transmitted_medium(
        density_cmy,
        film,
        scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(scanner),
    )


def render_transparency_image(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """Capture a positive transparency through its light-table illuminant."""
    scanner = scanner or ScannerConfig()
    return capture_transmitted_medium(
        density_cmy,
        film,
        scanner,
        illuminant_rgb=light_table_illuminant_rgb(scanner),
    )


def color_temperature_rgb(temperature_k: float) -> np.ndarray:
    """Approximate normalized RGB illuminant for a Kelvin color temperature."""
    temp = float(np.clip(float(temperature_k), 1000.0, 40000.0)) / 100.0
    if temp <= 66.0:
        red = 255.0
        green = 99.4708025861 * np.log(max(temp, 1e-6)) - 161.1195681661
        blue = 0.0 if temp <= 19.0 else 138.5177312231 * np.log(max(temp - 10.0, 1e-6)) - 305.0447927307
    else:
        red = 329.698727446 * ((temp - 60.0) ** -0.1332047592)
        green = 288.1221695283 * ((temp - 60.0) ** -0.0755148492)
        blue = 255.0
    rgb = np.asarray((red, green, blue), dtype=np.float32)
    rgb = np.clip(rgb, 0.0, 255.0) / 255.0
    return (rgb / max(float(np.max(rgb)), 1e-6)).astype(np.float32, copy=False)


def light_table_illuminant_rgb(scanner: ScannerConfig | None = None) -> np.ndarray:
    """Return positive-transparency light-table RGB including EV and color temperature."""
    scanner = scanner or ScannerConfig()
    kelvin_rgb = color_temperature_rgb(float(scanner.light_table_temperature_k))
    residual_rgb = np.asarray(scanner.scanner_light_color, dtype=np.float32)
    brightness = 2.0 ** float(scanner.light_table_ev)
    return np.clip(kelvin_rgb * residual_rgb * brightness, 1e-6, None).astype(
        np.float32,
        copy=False,
    )


def negative_backlight_illuminant_rgb(scanner: ScannerConfig | None = None) -> np.ndarray:
    """Return the calibrated transmission illuminant used to capture negatives."""
    scanner = scanner or ScannerConfig()
    kelvin_rgb = color_temperature_rgb(float(scanner.negative_backlight_temperature_k))
    residual_rgb = np.asarray(scanner.scanner_light_color, dtype=np.float32)
    brightness = 2.0 ** float(scanner.negative_backlight_ev)
    return np.clip(kelvin_rgb * residual_rgb * brightness, 1e-6, None).astype(
        np.float32,
        copy=False,
    )


def transmission_illuminant_rgb(scanner: ScannerConfig | None = None) -> np.ndarray:
    """Return the one physical illuminant shared by all transmissive captures.

    New configurations use ``transmission_light_*``. Older scanner presets did
    not have those fields, so ``None`` deliberately falls back to the former
    negative-backlight or positive-light-table bank according to the explicit
    inversion control. The fallback changes no legacy numerical result.
    """
    scanner = scanner or ScannerConfig()
    ev = scanner.transmission_light_ev
    temperature_k = scanner.transmission_light_temperature_k
    legacy_positive = (
        str(scanner.interpretation_mode or "auto").strip().lower() in {"", "auto"}
        and str(scanner.interpreter_key).strip().lower()
        == "positive_transparency_scan"
    )
    use_negative_legacy_bank = (
        bool(scanner.invert_transmission) and not legacy_positive
    )
    if ev is None:
        ev = (
            scanner.negative_backlight_ev
            if use_negative_legacy_bank
            else scanner.light_table_ev
        )
    if temperature_k is None:
        temperature_k = (
            scanner.negative_backlight_temperature_k
            if use_negative_legacy_bank
            else scanner.light_table_temperature_k
        )
    kelvin_rgb = color_temperature_rgb(float(temperature_k))
    residual_rgb = np.asarray(scanner.scanner_light_color, dtype=np.float32)
    brightness = 2.0 ** float(ev)
    return np.clip(kelvin_rgb * residual_rgb * brightness, 1e-6, None).astype(
        np.float32,
        copy=False,
    )


def scan_negative_raw(
    density_cmy: np.ndarray,
    film: FilmStockConfig,
    scanner: ScannerConfig | None = None,
) -> np.ndarray:
    """兼容旧命名：返回 scanner raw / negative linear。"""
    return render_negative_image(density_cmy, film, scanner)


def estimate_negative_base_transmittance(
    scanner_raw: np.ndarray,
    base_percentile: float = 99.5,
) -> np.ndarray:
    """Estimate a missing clear-base/mask anchor from scanner-domain data.

    This is deliberately a fallback observation, not a material fact.  Each
    channel uses its robust upper transmission envelope because an orange mask
    has very different clear-base transmission in R, G, and B.  Callers must
    prefer a process-aware known base or explicit clear-border samples when
    either exists.
    """
    raw = np.asarray(scanner_raw, dtype=np.float32)
    if raw.ndim != 3 or raw.shape[-1] != 3 or raw.size == 0:
        raise ValueError("scanner_raw must be a non-empty HxWx3 array")
    percentile = float(base_percentile)
    if not np.isfinite(percentile) or not 0.0 <= percentile <= 100.0:
        raise ValueError("base_percentile must be finite and between 0 and 100")
    minimum = float(np.min(raw))
    maximum = float(np.max(raw))
    if not np.isfinite(minimum) or not np.isfinite(maximum):
        raise ValueError("scanner_raw contains non-finite values")
    return np.percentile(raw, percentile, axis=(0, 1)).astype(
        np.float32,
        copy=False,
    )


def balance_negative_base(
    negative_linear: np.ndarray,
    base_percentile: float = 99.5,
    base_samples: np.ndarray | None = None,
    known_base_transmittance_rgb: np.ndarray | None = None,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """去除片基/橙罩颜色；有边框样本时优先使用边框。"""
    negative_linear = np.asarray(negative_linear, dtype=np.float32)
    if known_base_transmittance_rgb is not None:
        known = np.asarray(known_base_transmittance_rgb, dtype=np.float32)
        if known.size != 3 or not np.all(np.isfinite(known)):
            raise ValueError("known_base_transmittance_rgb must contain three finite values")
        base = np.clip(known.reshape(3), 1e-6, 1.0)
    elif base_samples is None:
        base = estimate_negative_base_transmittance(
            negative_linear,
            base_percentile,
        )
    else:
        samples = np.asarray(base_samples, dtype=np.float32)
        if samples.size == 0 or samples.size % 3 != 0 or not np.all(np.isfinite(samples)):
            raise ValueError("base_samples must contain finite RGB samples")
        samples = np.clip(samples, 1e-6, 1.0).reshape(-1, 3)
        base = np.percentile(samples, 50.0, axis=0).astype(np.float32, copy=False)
    denominator = np.maximum(base, 1e-6).reshape(1, 1, 3)
    if (
        consume_input
        and negative_linear.dtype == np.float32
        and negative_linear.flags.writeable
    ):
        negative_linear /= denominator
        np.clip(negative_linear, 1e-6, 1.0, out=negative_linear)
        return negative_linear
    balanced = negative_linear / denominator
    return np.clip(balanced, 1e-6, 1.0).astype(np.float32, copy=False)


def invert_negative_image(
    base_balanced_negative: np.ndarray,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """base-balanced negative -> raw positive density。"""
    image = np.asarray(base_balanced_negative, dtype=np.float32)
    if consume_input and image.dtype == np.float32 and image.flags.writeable:
        np.clip(image, 1e-6, 1.0, out=image)
        np.log10(image, out=image)
        image *= -1.0
        return image
    return (-np.log10(np.clip(base_balanced_negative, 1e-6, 1.0))).astype(
        np.float32,
        copy=False,
    )


def reconstruct_negative_channels(
    scene_density_rgb: np.ndarray,
    scanner: ScannerConfig | None = None,
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, float, float], ...] | None = None,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """Apply reduced-order scanner channel reconstruction in density space.

    Base-mask removal happens first. The matrix approximates channel crosstalk;
    the per-channel exponents approximate remaining nonlinear response,
    including the extra green/blue treatment masked color negatives can need.
    Identity defaults preserve legacy scans and monochrome neutrality.

    ``dye_absorption_matrix`` remains accepted for source compatibility but is
    deliberately not used. Once the final medium has become total RGB optical
    density, scanner interpretation must not try to separate dye from silver,
    salts, or auxiliary deposits again.
    """
    scanner = scanner or ScannerConfig()
    density = np.asarray(scene_density_rgb, dtype=np.float32)
    can_consume = bool(
        consume_input
        and density.dtype == np.float32
        and density.flags.writeable
    )
    density_is_private = False
    if bool(scanner.negative_channel_compensation_enabled):
        compensation_matrix = negative_scanner_compensation_matrix(
            strength=scanner.negative_channel_compensation_strength,
        )
        if _is_identity_rgb_matrix(compensation_matrix):
            if not can_consume:
                density = density.copy()
                density_is_private = True
        else:
            density = np.einsum("...c,rc->...r", density, compensation_matrix)
            density_is_private = True
        np.maximum(density, 0.0, out=density)
    matrix = np.asarray(scanner.negative_channel_matrix, dtype=np.float32).reshape(3, 3)
    if _is_identity_rgb_matrix(matrix):
        # Public reconstruction has always returned an independent array.
        # Keep that ownership contract while replacing identity einsum with
        # the cheaper exact copy.
        reconstructed = (
            density
            if can_consume or density_is_private
            else density.copy()
        )
    else:
        reconstructed = np.einsum("...c,rc->...r", density, matrix)
    np.maximum(reconstructed, 0.0, out=reconstructed)
    gamma = np.asarray(scanner.negative_channel_gamma, dtype=np.float32).reshape(1, 1, 3)
    np.maximum(reconstructed, 1e-8, out=reconstructed)
    bounded_gamma = np.maximum(gamma, 1e-4)
    if not np.array_equal(bounded_gamma, _UNIT_RGB_GAMMA):
        np.power(reconstructed, bounded_gamma, out=reconstructed)
    return reconstructed.astype(np.float32, copy=False)


def negative_scanner_compensation_matrix(
    *,
    strength: float = 0.35,
) -> np.ndarray:
    """Return a bounded scanner-side blue/green channel correction.

    This is a reduced device/interpretation response, not an inverse of the
    material dye matrix. The optical master has already combined all material
    components, so a material inverse here would incorrectly recolour neutral
    silver and retained salts.
    """
    full = np.asarray(
        (
            (1.00, 0.00, 0.00),
            (-0.035, 1.035, 0.00),
            (-0.055, -0.025, 1.080),
        ),
        dtype=np.float32,
    )
    mix = float(np.clip(strength, 0.0, 1.0))
    identity = np.eye(3, dtype=np.float32)
    return (identity + mix * (full - identity)).astype(np.float32, copy=False)


def negative_material_compensation_matrix(
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, float, float], ...] | None,
    *,
    strength: float = 0.35,
) -> np.ndarray:
    """Compatibility alias for the former material-aware public helper.

    The matrix argument is intentionally ignored; callers now receive the
    scanner-side correction used by the authoritative RGB optical path.
    """
    del dye_absorption_matrix
    return negative_scanner_compensation_matrix(strength=strength)


def _smoothstep(values: np.ndarray, edge0: float, edge1: float) -> np.ndarray:
    source = np.asarray(values)
    if source.dtype != np.float32:
        x = np.clip((source - edge0) / max(edge1 - edge0, 1e-6), 0.0, 1.0)
        return (x * x * (3.0 - 2.0 * x)).astype(np.float32, copy=False)
    x = np.subtract(source, edge0, dtype=np.float32)
    x /= max(edge1 - edge0, 1e-6)
    np.clip(x, 0.0, 1.0, out=x)
    curve = np.multiply(x, x, dtype=np.float32)
    x *= -2.0
    x += 3.0
    curve *= x
    return curve


def _luma_preserving_saturation(
    image: np.ndarray,
    saturation: float,
    *,
    consume_input: bool = False,
    _known_luma: np.ndarray | None = None,
) -> np.ndarray:
    """扫描输出阶段的色彩浓度调节，保留亮度关系，不回写底片材料。"""
    saturation = float(max(saturation, 0.0))
    if abs(saturation - 1.0) < 1e-6:
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            return image
        return image.astype(np.float32)
    if _known_luma is None:
        luma = luminance(image)[..., None]
    else:
        known_luma = np.asarray(_known_luma, dtype=np.float32)
        if known_luma.shape != image.shape[:2]:
            raise ValueError("known luma must match the image plane")
        luma = known_luma[..., None]
    if consume_input and image.dtype == np.float32 and image.flags.writeable:
        # Preserve the historical ``luma + (image - luma) * saturation``
        # operation order while reusing this private render buffer.
        image -= luma
        image *= saturation
        image += luma
        np.clip(image, 0.0, 1.0, out=image)
        return image
    return np.clip(luma + (image - luma) * saturation, 0.0, 1.0).astype(
        np.float32,
        copy=False,
    )


def _soft_white_rolloff(
    image: np.ndarray,
    softness: float,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    softness = float(np.clip(softness, 0.0, 0.95))
    if softness <= 0.0:
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            return image
        return image.astype(np.float32)
    image = np.asarray(image, dtype=np.float32)
    knee = 1.0 - softness
    luma = luminance(image)[..., None]
    # Evaluate the established high branch in a private scalar copy, then
    # restore the unchanged low branch with the same mask. This avoids the
    # eager pair of full branches created by np.where.
    rolled = luma.copy()
    rolled -= knee
    rolled *= -1.0
    rolled /= max(1.0 - knee, 1e-6)
    np.exp(rolled, out=rolled)
    rolled *= -1.0
    rolled += 1.0
    rolled *= 1.0 - knee
    rolled += knee
    np.copyto(rolled, luma, where=luma <= knee)
    np.maximum(luma, 1e-6, out=luma)
    np.divide(rolled, luma, out=rolled)
    scale = rolled
    if consume_input and image.dtype == np.float32 and image.flags.writeable:
        image *= scale
        np.clip(image, 0.0, 1.0, out=image)
        return image
    output = np.multiply(image, scale, dtype=np.float32)
    np.clip(output, 0.0, 1.0, out=output)
    return output.astype(np.float32, copy=False)


def _black_adaptation_lift(
    image: np.ndarray,
    strength: float,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    strength = float(np.clip(strength, 0.0, 0.95))
    if strength <= 0.0:
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            return image
        return image.astype(np.float32)
    image = np.asarray(image, dtype=np.float32)
    luma = luminance(image)[..., None]
    # Blend the luminance towards a square-root shadow curve, then preserve
    # the original RGB ratios.  The former additive lift approached a fixed
    # grey floor as luma approached zero, so distinct dense transparency
    # values could collapse into an apparently blocked shadow.  This mapping
    # keeps 0 -> 0, is monotonic, and remains an observation-only adjustment.
    lifted_luma = np.clip(luma, 0.0, None)
    np.sqrt(lifted_luma, out=lifted_luma)
    lifted_luma -= luma
    lifted_luma *= strength
    lifted_luma += luma
    scale = np.divide(
        lifted_luma,
        luma,
        out=np.zeros_like(lifted_luma),
        where=luma > 1e-8,
    )
    if consume_input and image.dtype == np.float32 and image.flags.writeable:
        image *= scale
        np.clip(image, 0.0, 1.0, out=image)
        return image
    output = np.multiply(image, scale, dtype=np.float32)
    np.clip(output, 0.0, 1.0, out=output)
    return output.astype(np.float32, copy=False)


def render_positive_scan(
    positive_raw_density: np.ndarray,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
    paper_black: float = 0.0,
    paper_white: float = 1.0,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """raw positive density -> 渲染后的正像线性 RGB。"""
    scanner = scanner or ScannerConfig()
    positive_raw_density = np.asarray(positive_raw_density, dtype=np.float32)
    reference = np.asarray(scanner.print_reference_density, dtype=np.float32)
    color_shift = np.asarray(scanner.print_color_shift, dtype=np.float32)
    gamma = float(scanner.print_gamma) * max(float(print_contrast), 0.01)

    if (
        consume_input
        and positive_raw_density.dtype == np.float32
        and positive_raw_density.flags.writeable
    ):
        # The tiled output-only scanner owns this reconstructed-channel tile
        # exclusively. Follow the public formula in the same float32 operation
        # order while transferring that buffer through log density, print
        # mapping, highlight response, saturation, and paper range.
        mapped = positive_raw_density
        mapped -= reference
        mapped *= gamma
        mapped += color_shift
        np.power(10.0, mapped, out=mapped)
        mapped *= 2.0 ** float(print_exposure_ev)

        if str(scanner.print_mapping_mode).lower() == "sigmoid":
            denominator = mapped + 1.0
            np.divide(mapped, denominator, out=mapped)
            del denominator
        else:
            mapped *= -1.0
            np.exp(mapped, out=mapped)
            mapped *= -1.0
            mapped += 1.0
        mapped *= np.asarray(
            scanner.print_color_bias,
            dtype=np.float32,
        ).reshape(1, 1, 3)

        luma = luminance(mapped)
        highlight_weight = _smoothstep(
            luma,
            scanner.highlight_bias_threshold,
            scanner.highlight_bias_threshold + scanner.highlight_bias_softness,
        )
        highlight_bias = np.asarray(scanner.highlight_color_bias, dtype=np.float32)
        factor = np.empty_like(highlight_weight, dtype=np.float32)
        for channel in range(3):
            np.multiply(
                highlight_weight,
                np.float32(highlight_bias[channel] - 1.0),
                out=factor,
            )
            factor += 1.0
            mapped[..., channel] *= factor
            if channel == 0:
                np.multiply(mapped[..., channel], 0.2126, out=luma)
            elif channel == 1:
                np.multiply(mapped[..., channel], 0.7152, out=factor)
                luma += factor
            else:
                np.multiply(mapped[..., channel], 0.0722, out=factor)
                luma += factor
        del factor, highlight_weight

        mapped = _luma_preserving_saturation(
            mapped,
            scanner.scan_saturation,
            consume_input=True,
            _known_luma=luma,
        )
        np.clip(mapped, 0.0, 1.0, out=mapped)
        mapped *= float(paper_white) - float(paper_black)
        mapped += float(paper_black)
        return mapped

    log_positive = (positive_raw_density - reference) * gamma + color_shift
    positive = np.power(10.0, log_positive) * (2.0 ** float(print_exposure_ev))

    if str(scanner.print_mapping_mode).lower() == "sigmoid":
        mapped = positive / (1.0 + positive)
    else:
        mapped = 1.0 - np.exp(-positive)
    mapped = mapped * np.asarray(scanner.print_color_bias, dtype=np.float32)[None, None, :]

    luma = luminance(mapped)
    highlight_weight = _smoothstep(
        luma,
        scanner.highlight_bias_threshold,
        scanner.highlight_bias_threshold + scanner.highlight_bias_softness,
    )[..., None]
    highlight_bias = np.asarray(scanner.highlight_color_bias, dtype=np.float32)[None, None, :]
    mapped = mapped * (1.0 + highlight_weight * (highlight_bias - 1.0))
    mapped = _luma_preserving_saturation(
        mapped,
        scanner.scan_saturation,
        consume_input=True,
    )

    # ``mapped`` is private regardless of the caller's input ownership. Apply
    # the established paper range in place instead of returning a second tile.
    np.clip(mapped, 0.0, 1.0, out=mapped)
    mapped *= float(paper_white) - float(paper_black)
    mapped += float(paper_black)
    return mapped


def scanner_raw_to_positive_rgb(
    scanner_raw: np.ndarray,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
    paper_black: float = 0.0,
    paper_white: float = 1.0,
    base_percentile: float = 99.5,
    base_samples: np.ndarray | None = None,
    known_base_transmittance_rgb: np.ndarray | None = None,
    known_base_density_rgb: np.ndarray | None = None,
    dye_absorption_matrix: np.ndarray | tuple[tuple[float, float, float], ...] | None = None,
) -> np.ndarray:
    """scanner raw -> 去片基 -> 反相 -> 正像渲染。"""
    scanner = scanner or ScannerConfig()
    if known_base_transmittance_rgb is not None and known_base_density_rgb is not None:
        raise ValueError(
            "provide only one of known_base_transmittance_rgb and known_base_density_rgb"
        )
    known_base = known_base_transmittance_rgb
    if known_base_density_rgb is not None:
        density = np.asarray(known_base_density_rgb, dtype=np.float32)
        if density.size != 3 or not np.all(np.isfinite(density)) or np.any(density < 0.0):
            raise ValueError("known_base_density_rgb must contain three finite nonnegative values")
        known_base = capture_optical_density(
            density.reshape(1, 1, 3),
            scanner,
            illuminant_rgb=transmission_illuminant_rgb(scanner),
        ).reshape(3)
    balanced = balance_negative_base(
        scanner_raw,
        base_percentile=base_percentile,
        base_samples=base_samples,
        known_base_transmittance_rgb=known_base,
    )
    raw_positive = invert_negative_image(balanced)
    reconstructed = reconstruct_negative_channels(
        raw_positive,
        scanner,
        dye_absorption_matrix=dye_absorption_matrix,
    )
    return render_positive_scan(
        reconstructed,
        scanner,
        print_contrast=print_contrast,
        print_exposure_ev=print_exposure_ev,
        paper_black=paper_black,
        paper_white=paper_white,
    )


def render_positive_transparency_scan(
    transparency_rgb: np.ndarray,
    scanner: ScannerConfig | None = None,
    print_contrast: float = 1.0,
    print_exposure_ev: float = 0.0,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """Render a positive transparency/light-table scan without negative inversion."""
    scanner = scanner or ScannerConfig()
    image = np.asarray(transparency_rgb, dtype=np.float32)
    can_consume = bool(
        consume_input
        and image.dtype == np.float32
        and image.flags.writeable
    )
    exposure = 2.0 ** float(print_exposure_ev)
    if can_consume:
        image *= exposure
        np.maximum(image, 0.0, out=image)
    else:
        image = np.clip(image * exposure, 0.0, None)
    color_strength = float(np.clip(scanner.positive_scan_color_control_strength, 0.0, 1.0))

    requested_contrast = max(
        float(scanner.print_gamma) * max(float(print_contrast), 0.01),
        0.01,
    )
    contrast = 1.0 + (requested_contrast - 1.0) * color_strength
    if can_consume:
        image -= 0.5
        image *= contrast
        image += 0.5
        np.maximum(image, 0.0, out=image)
    else:
        image = np.clip((image - 0.5) * contrast + 0.5, 0.0, None)
    color_shift = np.power(
        10.0,
        np.asarray(scanner.print_color_shift, dtype=np.float32) * color_strength,
    ).reshape(1, 1, 3)
    if can_consume:
        image *= color_shift
    else:
        image = image * color_shift
    color_bias = 1.0 + (np.asarray(scanner.print_color_bias, dtype=np.float32) - 1.0) * color_strength
    if can_consume:
        image *= color_bias.reshape(1, 1, 3)
    else:
        image = image * color_bias[None, None, :]

    luma = luminance(image)
    highlight_weight = _smoothstep(
        luma,
        scanner.highlight_bias_threshold,
        scanner.highlight_bias_threshold + scanner.highlight_bias_softness,
    )[..., None]
    # This is already an explicitly highlight-gated device response. Applying
    # the general positive color-control limiter a second time made the GUI
    # control nearly inert (for the neutral light table, only ~12% remained).
    # Preset defaults stay close to unity; an explicit user edit is therefore
    # respected at its declared strength without expanding scanner ownership.
    highlight_bias = np.asarray(scanner.highlight_color_bias, dtype=np.float32)
    highlight_bias = highlight_bias[None, None, :]
    if can_consume:
        factor = np.empty_like(highlight_weight, dtype=np.float32)
        for channel in range(3):
            np.multiply(
                highlight_weight,
                np.float32(highlight_bias[0, 0, channel] - 1.0),
                out=factor,
            )
            factor += 1.0
            image[..., channel] *= factor[..., 0]
        del factor
    else:
        image = image * (1.0 + highlight_weight * (highlight_bias - 1.0))
    image = _black_adaptation_lift(
        image,
        scanner.projection_black_adaptation,
        consume_input=can_consume,
    )
    image = _soft_white_rolloff(
        image,
        scanner.projection_white_softness,
        consume_input=can_consume,
    )
    saturation = 1.0 + (float(scanner.scan_saturation) - 1.0) * color_strength
    return _luma_preserving_saturation(
        image,
        saturation,
        # At this point the non-consuming branch also owns a private buffer
        # created by its exposure/contrast path, so both branches can finish
        # saturation without another RGB allocation.
        consume_input=True,
    )


def normalize_scan_rgb(
    image: np.ndarray,
    black_percentile: float = 0.3,
    white_percentile: float = 99.7,
    strength: float = 1.0,
    mode: str = "luma",
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """扫描软件后期定黑白点。luma 模式保留色偏，rgb 模式更像自动白平衡。"""
    image = np.asarray(image, dtype=np.float32)
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            np.clip(image, 0.0, 1.0, out=image)
            return image
        return np.clip(image, 0.0, 1.0).astype(np.float32, copy=False)

    black, white = scan_normalization_range(
        image,
        black_percentile=black_percentile,
        white_percentile=white_percentile,
        mode=mode,
    )
    return apply_scan_normalization_range(
        image,
        black,
        white,
        strength=strength,
        mode=mode,
        consume_input=consume_input,
    )


def scan_normalization_range(
    image: np.ndarray,
    black_percentile: float = 0.3,
    white_percentile: float = 99.7,
    mode: str = "luma",
) -> tuple[np.ndarray, np.ndarray]:
    """Measure one global black/white range without changing scan pixels."""
    image = np.asarray(image, dtype=np.float32)

    def percentile_pair(values: np.ndarray) -> tuple[float, float]:
        # ``values`` is a private contiguous scalar work field. Preserve the
        # historical two independent percentile calls (a combined q vector
        # changes interpolation by ~1 ULP), but let NumPy partition this
        # private field in place instead of copying it twice internally.
        black_value = float(
            np.percentile(
                values,
                float(black_percentile),
                overwrite_input=True,
            )
        )
        white_value = float(
            np.percentile(
                values,
                float(white_percentile),
                overwrite_input=True,
            )
        )
        return black_value, white_value

    if str(mode).lower() == "rgb":
        # Process channels independently with one reusable scalar field so
        # percentile selection never needs an HxWx3 work array.
        black_values: list[float] = []
        white_values: list[float] = []
        reference_topology = reference_execution_enabled()
        channel_work = (
            None
            if reference_topology
            else np.empty(image.shape[:2], dtype=np.float32)
        )
        for channel in range(3):
            if reference_topology:
                channel_work = np.ascontiguousarray(image[..., channel])
            else:
                np.copyto(channel_work, image[..., channel])
            black_value, white_value = percentile_pair(channel_work)
            black_values.append(black_value)
            white_values.append(white_value)
        del channel_work
        black = np.asarray(black_values, dtype=np.float32)
        white = np.asarray(white_values, dtype=np.float32)
    else:
        height, width = image.shape[:2]
        luma = np.empty((height, width), dtype=np.float32)
        rows = max(1, min(height, int(2_000_000 / max(width, 1))))
        if reference_execution_enabled():
            for start in range(0, height, rows):
                stop = min(start + rows, height)
                tile = image[start:stop]
                # Frozen allocation topology used for same-version A/B.
                luma[start:stop] = luminance(tile)
        else:
            work = np.empty((rows, width), dtype=np.float32)
            for start in range(0, height, rows):
                stop = min(start + rows, height)
                tile = image[start:stop]
                # Write the established left-to-right float32 expression
                # directly into the final global statistics field and reuse
                # one bounded scalar tile for the G/B contribution.
                luminance(
                    tile,
                    _out=luma[start:stop],
                    _work=work[: stop - start],
                )
            del work
        black_value, white_value = percentile_pair(luma)
        black = np.asarray((black_value, black_value, black_value), dtype=np.float32)
        white = np.asarray((white_value, white_value, white_value), dtype=np.float32)
    return black, white


def apply_scan_normalization_range(
    image: np.ndarray,
    black: np.ndarray,
    white: np.ndarray,
    *,
    strength: float = 1.0,
    mode: str = "luma",
    consume_input: bool = False,
) -> np.ndarray:
    """Apply one frozen global range without changing tile calibration.

    ``luma`` changes only luminance: all three channels of each pixel receive
    the same multiplier, so a film cast or light-table chromaticity survives.
    ``rgb`` retains the explicit per-channel normalization used as an
    auto-white-balance-like option.
    """
    image = np.asarray(image, dtype=np.float32)
    strength = float(np.clip(strength, 0.0, 1.0))
    if strength <= 0.0:
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            np.clip(image, 0.0, 1.0, out=image)
            return image
        return np.clip(image, 0.0, 1.0).astype(np.float32, copy=False)
    black = np.asarray(black, dtype=np.float32).reshape(3)
    white = np.asarray(white, dtype=np.float32).reshape(3)

    scale = white - black
    if float(np.max(scale)) <= 1e-6:
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            np.clip(image, 0.0, 1.0, out=image)
            return image
        return np.clip(image, 0.0, 1.0).astype(np.float32, copy=False)

    if str(mode).lower() != "rgb":
        # The range was measured from this same Rec.709 luma definition.
        # Remap that scalar, turn it into one per-pixel gain, then blend the
        # gain with identity.  This keeps RGB ratios intact except where the
        # final display gamut itself clips a channel.
        luma = luminance(image)
        gain = luma.copy()
        gain -= float(black[0])
        gain /= max(float(scale[0]), 1e-6)
        np.clip(gain, 0.0, 1.0, out=gain)
        # ``gain`` is already zero wherever the target luma is black. Clamp
        # the private denominator in place to avoid two full-tile boolean
        # masks at large resolutions.
        np.maximum(luma, 1e-8, out=luma)
        np.divide(gain, luma, out=gain)
        del luma
        # ``image * ((1-s) + s*gain)`` preserves chromaticity and needs only
        # one scalar work field, which matters for 30--60 MP tiled scans.
        gain -= 1.0
        gain *= strength
        gain += 1.0
        if consume_input and image.dtype == np.float32 and image.flags.writeable:
            image *= gain[..., None]
            np.clip(image, 0.0, 1.0, out=image)
            return image
        normalized = image * gain[..., None]
        return np.clip(normalized, 0.0, 1.0).astype(np.float32, copy=False)

    if (
        consume_input
        and image.dtype == np.float32
        and image.flags.writeable
        and not reference_execution_enabled()
    ):
        denominator = np.maximum(scale, 1e-6)
        normalized_channel = np.empty(image.shape[:2], dtype=np.float32)
        for channel in range(3):
            np.subtract(
                image[..., channel],
                black[channel],
                out=normalized_channel,
            )
            normalized_channel /= denominator[channel]
            np.clip(normalized_channel, 0.0, 1.0, out=normalized_channel)
            image[..., channel] *= 1.0 - strength
            normalized_channel *= strength
            image[..., channel] += normalized_channel
        np.clip(image, 0.0, 1.0, out=image)
        return image

    normalized = np.clip((image - black) / np.maximum(scale, 1e-6), 0.0, 1.0)
    if consume_input and image.dtype == np.float32 and image.flags.writeable:
        image *= 1.0 - strength
        normalized *= strength
        image += normalized
        np.clip(image, 0.0, 1.0, out=image)
        return image
    blended = image * (1.0 - strength) + normalized * strength
    return np.clip(blended, 0.0, 1.0).astype(np.float32, copy=False)
