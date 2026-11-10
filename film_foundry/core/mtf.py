"""乳剂解析力、空间频率和数字锐化伪影抑制。"""

from __future__ import annotations

import cv2
import numpy as np

from film_foundry.core.color import luminance
from film_foundry.core.execution_topology import reference_execution_enabled
from film_foundry.core.halation import radius_to_sigma
from film_foundry.model.config import FilmStockConfig


FULL_EMULSION_MTF_MODEL = "adaptive_dual_psf_v1"
REDUCED_EMULSION_MTF_MODEL = "moment_matched_single_psf_v1"


def _robust_normalize(values: np.ndarray, percentile: float = 96.0) -> np.ndarray:
    """用高百分位做归一化，避免少数极端边缘把遮罩压扁。"""
    scale = float(np.percentile(values, percentile))
    if scale <= 1e-6:
        return np.zeros_like(values, dtype=np.float32)
    return np.clip(values / scale, 0.0, 1.0).astype(np.float32)


def _above_threshold_mask(values: np.ndarray, threshold: float, width: float) -> np.ndarray:
    """只让阈值以上的区域逐渐进入遮罩，平坦区域保持 0。"""
    x = np.clip((values - threshold) / max(width, 1e-6), 0.0, 1.0)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def local_frequency_mask(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """估计局部高空间频率区域，用于调制乳剂低通强度。"""
    y = luminance(image)
    sigma = radius_to_sigma(film.emulsion_blur_radius, image.shape)
    low = cv2.GaussianBlur(y, (0, 0), sigmaX=sigma, sigmaY=sigma)

    # 高频残差捕捉单像素锐边和 ISP 锐化产生的局部振铃。
    np.subtract(y, low, out=low)
    np.abs(low, out=low)

    # 梯度用于识别接近阶跃函数的边缘。Sobel 比简单差分更稳一点。
    grad_x = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    # Both Sobel inputs are private. Reuse grad_x for magnitude and low for
    # the combined high-frequency field instead of retaining highpass,
    # gradient, combined, normalized, and threshold intermediates together.
    # OpenCV's SIMD magnitude kernel can differ by one float32 ULP according
    # to allocation alignment.  NumPy's float32 hypot is stable across those
    # layouts and may safely reuse the first private gradient buffer.
    np.hypot(grad_x, grad_y, out=grad_x)
    grad_x *= np.float32(0.35)
    low += grad_x
    del y, grad_y

    scale = float(np.percentile(low, 96.0))
    if scale <= 1e-6:
        grad_x.fill(0.0)
        return grad_x
    np.divide(low, scale, out=low)
    np.clip(low, 0.0, 1.0, out=low)

    np.subtract(low, np.float32(film.high_frequency_threshold), out=low)
    np.divide(low, np.float32(0.32), out=low)
    np.clip(low, 0.0, 1.0, out=low)
    # smoothstep(x) = x^2 * (3 - 2x). grad_x is no longer needed after the
    # magnitude was accumulated, so it becomes the returned mask buffer.
    np.multiply(low, low, out=grad_x)
    np.multiply(low, np.float32(-2.0), out=low)
    low += np.float32(3.0)
    grad_x *= low
    return grad_x


def _reduced_effective_sigma(
    sigma: float,
    strength: float,
    artifact_strength: float,
) -> float:
    """Match the peak-response second moment of the canonical two PSFs."""
    artifact_blend = float(artifact_strength) * 0.55
    primary_weight = float(strength) * (1.0 - artifact_blend)
    artifact_weight = artifact_blend
    total_weight = primary_weight + artifact_weight
    if total_weight <= 1e-8:
        return float(sigma)
    variance_scale = (
        primary_weight + artifact_weight * (1.8 * 1.8)
    ) / total_weight
    return float(sigma) * float(np.sqrt(max(variance_scale, 1e-8)))


def _apply_reduced_emulsion_mtf(
    image: np.ndarray,
    frequency_mask: np.ndarray,
    sigma: float,
    strength: float,
    artifact_strength: float,
    *,
    can_consume: bool,
    exposure_ceiling: float | None = 4.0,
) -> np.ndarray:
    """Approximate the two canonical spatial responses with one matched PSF.

    The canonical response composes an emulsion-limit blend ``m*s`` and a
    stronger digital-artifact blend ``m*a``.  Their combined source removal is
    retained exactly as ``1 - (1-m*s)(1-m*a)``; only the two blurred images are
    replaced by one peak-response second-moment-matched Gaussian.
    """
    artifact_blend = float(artifact_strength) * 0.55
    effective_sigma = _reduced_effective_sigma(
        sigma,
        strength,
        artifact_strength,
    )

    # total = m*(s+a) - m^2*s*a. ``frequency_mask`` is private and has no
    # consumer after this point, so reuse it for the quadratic term and then
    # for the inverse blend instead of allocating a third scalar frame.
    blend = np.empty_like(frequency_mask, dtype=np.float32)
    np.multiply(
        frequency_mask,
        np.float32(strength + artifact_blend),
        out=blend,
    )
    np.multiply(frequency_mask, frequency_mask, out=frequency_mask)
    frequency_mask *= np.float32(strength * artifact_blend)
    blend -= frequency_mask
    np.clip(blend, 0.0, 1.0, out=blend)
    np.subtract(1.0, blend, out=frequency_mask)
    inverse_blend = frequency_mask

    if int(image.shape[0]) * int(image.shape[1]) >= 1_000_000:
        blurred_channel = np.empty(image.shape[:2], dtype=np.float32)
        out = image if can_consume else np.empty_like(image, dtype=np.float32)
        for channel in range(3):
            source_channel = image[..., channel]
            cv2.GaussianBlur(
                source_channel,
                (0, 0),
                sigmaX=effective_sigma,
                sigmaY=effective_sigma,
                dst=blurred_channel,
            )
            blurred_channel *= blend
            output_channel = out[..., channel]
            np.multiply(source_channel, inverse_blend, out=output_channel)
            output_channel += blurred_channel
        np.clip(out, 0.0, exposure_ceiling, out=out)
        return out

    blurred = cv2.GaussianBlur(
        image,
        (0, 0),
        sigmaX=effective_sigma,
        sigmaY=effective_sigma,
    )
    blurred *= blend[..., None]
    out = image if can_consume else np.empty_like(image, dtype=np.float32)
    np.multiply(image, inverse_blend[..., None], out=out)
    out += blurred
    np.clip(out, 0.0, exposure_ceiling, out=out)
    return out


def apply_emulsion_mtf(
    image: np.ndarray,
    film: FilmStockConfig,
    *,
    consume_input: bool = False,
    model: str = FULL_EMULSION_MTF_MODEL,
    workers: int = 1,
    exposure_ceiling: float | None = 4.0,
) -> np.ndarray:
    """模拟胶片乳剂/银盐颗粒系统的有限空间截止频率。

    这一步不是普通柔焦。它主要作用于数码输入中超过胶片颗粒承载能力的
    高频边缘和锐化振铃，让细节先进入乳剂解析力限制，再由后续颗粒显影出来。
    """
    image = np.asarray(image, dtype=np.float32)
    model = str(model)
    if model not in {FULL_EMULSION_MTF_MODEL, REDUCED_EMULSION_MTF_MODEL}:
        raise ValueError(f"unsupported emulsion MTF model: {model}")
    strength = float(np.clip(film.emulsion_mtf_strength, 0.0, 1.0))
    artifact_strength = float(np.clip(film.digital_artifact_suppression, 0.0, 1.0))
    if strength <= 0.0 and artifact_strength <= 0.0:
        return image

    # Build the scalar mask before the two RGB blurs. Previously both RGB
    # images stayed live throughout Sobel/percentile work, which dominated the
    # full-resolution peak while contributing nothing to the mask itself.
    frequency_mask = local_frequency_mask(image, film)
    sigma = radius_to_sigma(film.emulsion_blur_radius, image.shape)
    can_consume = bool(
        consume_input
        and image.dtype == np.float32
        and image.flags.writeable
    )

    if model == REDUCED_EMULSION_MTF_MODEL:
        return _apply_reduced_emulsion_mtf(
            image,
            frequency_mask,
            sigma,
            strength,
            artifact_strength,
            can_consume=can_consume,
            exposure_ceiling=exposure_ceiling,
        )

    if (
        not reference_execution_enabled()
        and int(image.shape[0]) * int(image.shape[1]) >= 1_000_000
    ):
        # Gaussian filtering is channel-separable. On large frames, keeping
        # both three-channel blur results beside the source is the dominant
        # whole-pipeline peak. Compute the same two OpenCV blurs per channel,
        # with concurrency bounded by the caller's resource policy, and finish
        # each channel only after both source-dependent filters complete.
        # The public path still writes an independent
        # RGB result; the engine's private input may be consumed after its
        # channel has had its final read.
        out = image if can_consume else np.empty_like(image, dtype=np.float32)
        def process_channel(channel):
            emulsion_channel = np.empty(image.shape[:2], dtype=np.float32)
            stronger_channel = np.empty(image.shape[:2], dtype=np.float32)
            blend = np.empty(image.shape[:2], dtype=np.float32)
            source_channel = image[..., channel]
            cv2.GaussianBlur(
                source_channel,
                (0, 0),
                sigmaX=sigma,
                sigmaY=sigma,
                dst=emulsion_channel,
            )
            cv2.GaussianBlur(
                source_channel,
                (0, 0),
                sigmaX=sigma * 1.8,
                sigmaY=sigma * 1.8,
                dst=stronger_channel,
            )

            np.multiply(
                frequency_mask,
                np.float32(strength),
                out=blend,
            )
            np.clip(blend, 0.0, 1.0, out=blend)
            emulsion_channel *= blend
            np.subtract(1.0, blend, out=blend)
            output_channel = out[..., channel]
            np.multiply(source_channel, blend, out=output_channel)
            output_channel += emulsion_channel

            np.multiply(
                frequency_mask,
                np.float32(artifact_strength),
                out=blend,
            )
            blend *= np.float32(0.55)
            np.clip(blend, 0.0, 1.0, out=blend)
            stronger_channel *= blend
            np.subtract(1.0, blend, out=blend)
            output_channel *= blend
            output_channel += stronger_channel
        from .material_parallel import ordered_results
        with ordered_results(range(3), process_channel, workers=min(workers,3)) as (_,results):
            for _ in results:pass
        np.clip(out, 0.0, exposure_ceiling, out=out)
        return out

    emulsion_blur = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma, sigmaY=sigma)
    stronger_blur = cv2.GaussianBlur(image, (0, 0), sigmaX=sigma * 1.8, sigmaY=sigma * 1.8)

    # 高频区域按乳剂解析力做低通；极端锐化伪影再额外靠向稍强的模糊结果。
    blend = np.multiply(frequency_mask, np.float32(strength), dtype=np.float32)
    np.clip(blend, 0.0, 1.0, out=blend)
    emulsion_blur *= blend[..., None]
    np.subtract(1.0, blend, out=blend)
    out = image if can_consume else np.empty_like(image, dtype=np.float32)
    np.multiply(image, blend[..., None], out=out)
    out += emulsion_blur
    del emulsion_blur

    np.multiply(
        frequency_mask,
        np.float32(artifact_strength),
        out=blend,
    )
    blend *= np.float32(0.55)
    np.clip(blend, 0.0, 1.0, out=blend)
    stronger_blur *= blend[..., None]
    np.subtract(1.0, blend, out=blend)
    out *= blend[..., None]
    out += stronger_blur
    np.clip(out, 0.0, exposure_ceiling, out=out)
    return out
