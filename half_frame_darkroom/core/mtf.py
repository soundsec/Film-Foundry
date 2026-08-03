"""乳剂解析力、空间频率和数字锐化伪影抑制。"""

from __future__ import annotations

import cv2
import numpy as np

from half_frame_darkroom.core.color import luminance
from half_frame_darkroom.core.execution_topology import reference_execution_enabled
from half_frame_darkroom.core.halation import radius_to_sigma
from half_frame_darkroom.model.config import FilmStockConfig


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
    cv2.magnitude(grad_x, grad_y, grad_x)
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


def apply_emulsion_mtf(
    image: np.ndarray,
    film: FilmStockConfig,
    *,
    consume_input: bool = False,
) -> np.ndarray:
    """模拟胶片乳剂/银盐颗粒系统的有限空间截止频率。

    这一步不是普通柔焦。它主要作用于数码输入中超过胶片颗粒承载能力的
    高频边缘和锐化振铃，让细节先进入乳剂解析力限制，再由后续颗粒显影出来。
    """
    image = np.asarray(image, dtype=np.float32)
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

    if (
        not reference_execution_enabled()
        and int(image.shape[0]) * int(image.shape[1]) >= 1_000_000
    ):
        # Gaussian filtering is channel-separable. On large frames, keeping
        # both three-channel blur results beside the source is the dominant
        # whole-pipeline peak. Compute the same two OpenCV blurs one channel at
        # a time and finish that channel only after both source-dependent
        # filters have completed. The public path still writes an independent
        # RGB result; the engine's private input may be consumed after its
        # channel has had its final read.
        emulsion_channel = np.empty(image.shape[:2], dtype=np.float32)
        stronger_channel = np.empty(image.shape[:2], dtype=np.float32)
        blend = np.empty(image.shape[:2], dtype=np.float32)
        out = image if can_consume else np.empty_like(image, dtype=np.float32)
        for channel in range(3):
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
        np.clip(out, 0.0, 4.0, out=out)
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
    np.clip(out, 0.0, 4.0, out=out)
    return out
