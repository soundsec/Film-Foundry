package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.workflow.SceneIlluminant
import kotlin.math.max

enum class ViewerPreviewEncoding {
    DISPLAY_SRGB,
    LINEAR,
}

/**
 * Live view pixels that may light a viewer. This is not a still, not a
 * latent, and not a CaptureSource.
 */
data class ViewerPreviewFrame(
    val width: Int,
    val height: Int,
    val rgb: FloatArray,
    val encoding: ViewerPreviewEncoding,
    val rotationDegrees: Int=0,
) {
    init {
        if (width <= 0 || height <= 0) {
            throw CaptureRejected("viewer preview dimensions must be positive.")
        }
        val expected = Math.multiplyExact(Math.multiplyExact(width, height), 3)
        if (rgb.size != expected) {
            throw CaptureRejected("viewer preview RGB size ${rgb.size} does not match $expected.")
        }
    }
}

/**
 * Turn a preview frame into a transmissive illuminant. Never a roll fill.
 */
object ViewerIlluminantSampler {
    /** Same row order and float arithmetic as sample(), without an unused spatial image. */
    fun mean(frame: ViewerPreviewFrame): List<Double> {
        val mean=FloatArray(3)
        for(i in 0 until frame.width*frame.height) for(c in 0..2) {
            val v=frame.rgb[i*3+c]
            mean[c]+=max(if(frame.encoding==ViewerPreviewEncoding.DISPLAY_SRGB) TransferFunctions.srgbToLinear(v) else v,0f)
        }
        for(c in 0..2) mean[c]/=(frame.width*frame.height).toFloat()
        if(mean.all { it<=1e-8f }) throw CaptureRejected("viewer preview illuminant is black.")
        return mean.map { it.toDouble() }
    }
    fun sample(frame: ViewerPreviewFrame): SceneIlluminant {
        val linear = when (frame.encoding) {
            ViewerPreviewEncoding.DISPLAY_SRGB -> TransferFunctions.srgbToLinear(frame.rgb)
            ViewerPreviewEncoding.LINEAR -> frame.rgb.copyOf()
        }
        val pixels = frame.width * frame.height
        val mean = FloatArray(3)
        for (i in 0 until pixels) {
            for (channel in 0 until 3) {
                mean[channel] += max(linear[i * 3 + channel], 0f)
            }
        }
        for (channel in 0 until 3) {
            mean[channel] /= pixels.toFloat()
        }
        if (mean[0] <= 1e-8f && mean[1] <= 1e-8f && mean[2] <= 1e-8f) {
            throw CaptureRejected("viewer preview illuminant is black.")
        }
        return SceneIlluminant(
            rgb = listOf(mean[0].toDouble(), mean[1].toDouble(), mean[2].toDouble()),
            image = FloatNd(intArrayOf(frame.height, frame.width, 3), linear),
            sourceKind = DataKernel.PHONE_VIEWER_PREVIEW_KIND,
            reduction = DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION[1],
        )
    }

    fun sample(source: CaptureSource): Nothing {
        throw CaptureRejected(
            "capture sources fill rolls; viewer illuminant uses ViewerPreviewFrame.",
        )
    }
}
