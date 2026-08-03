"""Film Foundry 的颜色空间工具。

输入图像被视为显示编码的“场景代理”。这里的 sRGB 解码只用于建立近似线性
工作空间，方便后续做光学扩散、响应曲线和颗粒叠加；它不代表还原真实场景辐照度。
"""

from __future__ import annotations

import numpy as np


Array = np.ndarray


def srgb_to_linear(image: Array) -> Array:
    """把归一化 sRGB 转成近似线性工作空间。"""
    image = np.asarray(image, dtype=np.float32)
    image = np.clip(image, 0.0, 1.0)
    low = image <= 0.04045
    # ``image`` is already a private clipped copy. Evaluate each established
    # branch once, then select with the one mask; np.where would eagerly keep
    # both full RGB branch expressions and a third result alive together.
    output = image.copy()
    output += 0.055
    output /= 1.055
    np.power(output, 2.4, out=output)
    image /= 12.92
    np.copyto(output, image, where=low)
    return output


def linear_to_srgb(image: Array, *, consume_input: bool = False) -> Array:
    """把近似线性 RGB 编码回归一化 sRGB。"""
    image = np.asarray(image, dtype=np.float32)
    can_consume = (
        consume_input
        and image.dtype == np.float32
        and image.flags.writeable
    )
    if can_consume:
        np.clip(image, 0.0, 1.0, out=image)
    else:
        image = np.clip(image, 0.0, 1.0)
    low = image <= 0.0031308
    if can_consume:
        # Preserve only the smaller piecewise branch, then transform the owned
        # RGB buffer in place. This keeps the exact scalar formulas while
        # avoiding a second full output tile. Typical scan output has very few
        # values in the linear toe, but the complementary branch also bounds
        # memory for an almost-black frame.
        if int(np.count_nonzero(low)) <= image.size // 2:
            branch_values = image[low]
            branch_values *= 12.92
            np.power(image, 1.0 / 2.4, out=image)
            image *= 1.055
            image -= 0.055
            image[low] = branch_values
        else:
            np.logical_not(low, out=low)
            branch_values = image[low]
            np.power(branch_values, 1.0 / 2.4, out=branch_values)
            branch_values *= 1.055
            branch_values -= 0.055
            image *= 12.92
            image[low] = branch_values
        return image
    # The clipped input is either this function's private copy or an explicitly
    # consumed private pipeline buffer. Reuse it for the linear segment while
    # the output buffer carries the power segment; formulas and operation order
    # remain identical to the previous np.where expression.
    output = image.copy()
    np.power(output, 1.0 / 2.4, out=output)
    output *= 1.055
    output -= 0.055
    image *= 12.92
    np.copyto(output, image, where=low)
    return output


def luminance(
    image: Array,
    *,
    _out: Array | None = None,
    _work: Array | None = None,
) -> Array:
    """从线性 RGB 计算 Rec. 709 亮度。

    ``_out`` / ``_work`` are private pipeline hooks for bounded scanner
    statistics. Public calls keep the established independent return value.
    """
    image = np.asarray(image, dtype=np.float32)
    shape = image.shape[:2]
    if _out is None:
        output = np.multiply(image[..., 0], 0.2126, dtype=np.float32)
    else:
        output = np.asarray(_out)
        if (
            output.shape != shape
            or output.dtype != np.float32
            or not output.flags.writeable
            or np.shares_memory(output, image)
        ):
            raise ValueError(
                "luminance output must be a writable independent float32 HxW array"
            )
        np.multiply(image[..., 0], 0.2126, out=output)
    if _work is None:
        work = np.multiply(image[..., 1], 0.7152, dtype=np.float32)
    else:
        work = np.asarray(_work)
        if (
            work.shape != shape
            or work.dtype != np.float32
            or not work.flags.writeable
            or np.shares_memory(work, image)
            or np.shares_memory(work, output)
        ):
            raise ValueError(
                "luminance work must be a writable independent float32 HxW array"
            )
        np.multiply(image[..., 1], 0.7152, out=work)
    output += work
    np.multiply(image[..., 2], 0.0722, out=work)
    output += work
    return output.astype(np.float32, copy=False)


def apply_color_matrix(image: Array, matrix: Array) -> Array:
    """对 RGB 图像应用 3x3 色彩矩阵，用于层间串扰/色彩响应近似。"""
    image = np.asarray(image, dtype=np.float32)
    matrix = np.asarray(matrix, dtype=np.float32).reshape(3, 3)
    return np.einsum("...c,dc->...d", image, matrix).astype(
        np.float32,
        copy=False,
    )


def ensure_rgb_float(image: Array) -> Array:
    """把灰度/RGB/RGBA 图像整理成 [0, 1] 范围内的 float32 RGB。"""
    image = np.asarray(image)
    if image.ndim not in {2, 3}:
        raise ValueError(f"Image array must be 2D grayscale or 3D color data, got shape {image.shape}.")
    if image.size == 0 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"Image array must have non-zero width and height, got shape {image.shape}.")

    if np.issubdtype(image.dtype, np.integer):
        integer_scale = max(float(np.iinfo(image.dtype).max), 1.0)
        image = image.astype(np.float32)
        image /= integer_scale
    else:
        image = image.astype(np.float32, copy=False)
        minimum = float(np.min(image))
        maximum = float(np.max(image))
        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError("Image array contains NaN or infinite values.")
        if maximum > 1.0:
            image = image / 255.0

    if image.ndim == 2:
        return np.clip(np.repeat(image[..., None], 3, axis=-1), 0.0, 1.0).astype(
            np.float32,
            copy=False,
        )

    channels = image.shape[-1]
    if channels == 1:
        image = np.repeat(image, 3, axis=-1)
    elif channels == 2:
        gray = np.repeat(image[..., :1], 3, axis=-1)
        alpha = np.clip(image[..., 1:2], 0.0, 1.0)
        image = gray * alpha + (1.0 - alpha)
    elif channels == 3:
        pass
    elif channels == 4:
        rgb = image[..., :3]
        alpha = np.clip(image[..., 3:4], 0.0, 1.0)
        # 透明图不是物理照片；按白底合成，避免透明区域被静默当成黑色曝光。
        image = rgb * alpha + (1.0 - alpha)
    else:
        raise ValueError(
            f"Unsupported channel count {channels}. Film Foundry currently accepts grayscale, RGB, or RGBA images."
        )
    image = np.clip(image, 0.0, 1.0).astype(np.float32, copy=False)
    minimum = np.min(image)
    maximum = np.max(image)
    if not bool(np.isfinite(minimum)) or not bool(np.isfinite(maximum)):
        raise ValueError("Image conversion produced NaN or infinite RGB values.")
    return image
