package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import kotlin.math.floor

/** Read-only resample of an optical master onto the scan or projection grid. */
internal object OpticalGrid {
    data class Sized(val master: OpticalMaster, val sampler: String?)

    fun resize(source: OpticalMaster, width: Int, height: Int): Sized {
        val density = source.opticalDensityRgb
        val sourceHeight = density.shape[0]
        val sourceWidth = density.shape[1]
        if (sourceWidth == width && sourceHeight == height) return Sized(source, null)
        val sampler = if (divides(sourceWidth, width) && divides(sourceHeight, height)) "box_average_v1" else "bilinear_v1"
        return Sized(OpticalMaster(
            FloatNd(intArrayOf(height, width, 3), resample(density.values, sourceWidth, sourceHeight, 3, width, height)),
            source.metadata,
            source.clearBaseOpticalDensityRgb?.copyOf(),
            source.spectralComponentAmounts?.let {
                FloatNd(intArrayOf(height, width, 9), resample(it.values, sourceWidth, sourceHeight, 9, width, height))
            },
            source.spectralPostProcessDensityRgb?.let {
                FloatNd(intArrayOf(height, width, 3), resample(it.values, sourceWidth, sourceHeight, 3, width, height))
            },
        ), sampler)
    }

    fun resample(src: FloatArray, sourceWidth: Int, sourceHeight: Int, channels: Int, width: Int, height: Int): FloatArray {
        val out = FloatArray(width * height * channels)
        val boxX = sourceWidth / width
        val boxY = sourceHeight / height
        if (boxX >= 1 && boxY >= 1 && boxX * width == sourceWidth && boxY * height == sourceHeight) {
            val count = (boxX * boxY).toFloat()
            for (y in 0 until height) for (x in 0 until width) {
                val base = (y * width + x) * channels
                for (channel in 0 until channels) {
                    var sum = 0f
                    for (yy in 0 until boxY) for (xx in 0 until boxX) {
                        sum += src[((y * boxY + yy) * sourceWidth + x * boxX + xx) * channels + channel]
                    }
                    out[base + channel] = sum / count
                }
            }
            return out
        }
        for (y in 0 until height) {
            val gy = (y + 0.5f) * sourceHeight / height - 0.5f
            val y0 = floor(gy.toDouble()).toInt().coerceIn(0, sourceHeight - 1)
            val y1 = (y0 + 1).coerceAtMost(sourceHeight - 1)
            val fy = (gy - y0).coerceIn(0f, 1f)
            for (x in 0 until width) {
                val gx = (x + 0.5f) * sourceWidth / width - 0.5f
                val x0 = floor(gx.toDouble()).toInt().coerceIn(0, sourceWidth - 1)
                val x1 = (x0 + 1).coerceAtMost(sourceWidth - 1)
                val fx = (gx - x0).coerceIn(0f, 1f)
                val base = (y * width + x) * channels
                for (channel in 0 until channels) {
                    fun at(sx: Int, sy: Int) = src[(sy * sourceWidth + sx) * channels + channel]
                    out[base + channel] = at(x0, y0) * (1 - fx) * (1 - fy) + at(x1, y0) * fx * (1 - fy) +
                        at(x0, y1) * (1 - fx) * fy + at(x1, y1) * fx * fy
                }
            }
        }
        return out
    }

    private fun divides(source: Int, target: Int) = target > 0 && source / target >= 1 && (source / target) * target == source
}
