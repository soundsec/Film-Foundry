package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class ViewerIlluminantSamplerTest {
    @Test fun meanOnlyIsExactAndDoesNotModifyEitherPreviewEncoding() {
        for(encoding in ViewerPreviewEncoding.entries) {
            val values=FloatArray(37*23*3) { (it%91)/31f };val before=values.copyOf()
            val frame=ViewerPreviewFrame(37,23,values,encoding)
            assertEquals(ViewerIlluminantSampler.sample(frame).rgb,ViewerIlluminantSampler.mean(frame))
            org.junit.jupiter.api.Assertions.assertArrayEquals(before,values,0f)
        }
        assertThrows(CaptureRejected::class.java) { ViewerIlluminantSampler.mean(ViewerPreviewFrame(1,1,FloatArray(3),ViewerPreviewEncoding.LINEAR)) }
    }
    @Test
    fun displayPreviewBecomesLinearMeanAndKeepsSpatialField() {
        val encoded = floatArrayOf(1f, 0f, 0f, 0f, 0f, 1f)
        val illuminant = ViewerIlluminantSampler.sample(
            ViewerPreviewFrame(2, 1, encoded, ViewerPreviewEncoding.DISPLAY_SRGB),
        )
        val expectedRed = TransferFunctions.srgbToLinear(1f).toDouble()
        assertEquals(expectedRed / 2.0, illuminant.rgb[0], 1e-6)
        assertEquals(0.0, illuminant.rgb[1], 1e-6)
        assertEquals(expectedRed / 2.0, illuminant.rgb[2], 1e-6)
        assertEquals(DataKernel.PHONE_VIEWER_PREVIEW_KIND, illuminant.sourceKind)
        assertEquals("spatial_linear_rgb_v0", illuminant.reduction)
        assertEquals(expectedRed.toFloat(), illuminant.image!!.values[0], 1e-6f)
        assertTrue(DataKernel.PHONE_VIEWER_PREVIEW_KIND !in DataKernel.CAPTURE_KIND_VALUES)
    }

    @Test
    fun linearPreviewDoesNotClipHighlights() {
        val illuminant = ViewerIlluminantSampler.sample(
            ViewerPreviewFrame(
                1,
                1,
                floatArrayOf(4f, 2f, 1f),
                ViewerPreviewEncoding.LINEAR,
            ),
        )
        assertEquals(4.0, illuminant.rgb[0], 1e-6)
        assertEquals(2.0, illuminant.rgb[1], 1e-6)
        assertEquals(1.0, illuminant.rgb[2], 1e-6)
    }

    @Test
    fun blackPreviewFailsClosed() {
        assertThrows(CaptureRejected::class.java) {
            ViewerIlluminantSampler.sample(
                ViewerPreviewFrame(1, 1, floatArrayOf(0f, 0f, 0f), ViewerPreviewEncoding.LINEAR),
            )
        }
    }

    @Test
    fun captureSourceCannotBeAViewerLamp() {
        val encoded = EncodedRgbCapture(1, 1, floatArrayOf(0.5f, 0.5f, 0.5f))
        val error = assertThrows(CaptureRejected::class.java) {
            ViewerIlluminantSampler.sample(encoded)
        }
        assertTrue(error.message!!.contains("fill rolls"))
        assertThrows(CaptureRejected::class.java) {
            ViewerIlluminantSampler.sample(PreviewCapture(8, 8))
        }
    }
}
