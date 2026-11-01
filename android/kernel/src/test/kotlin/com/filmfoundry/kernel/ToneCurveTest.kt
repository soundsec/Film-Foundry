package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ToneCurveTest {
    private val kernel = PhoneReducedKernel()
    private val optical = OpticalMaster(FloatNd(intArrayOf(1, 2, 3), floatArrayOf(.7f, .5f, .3f, .4f, .6f, .8f)), emptyMap())
    private val scanner = mapOf<String, Any?>("remove_base_mask" to false, "invert_transmission" to false, "scan_normalize" to false)
    private fun observe(parameters: Map<String, Any?>) = kernel.observe(optical, mapOf("scanner" to (scanner + parameters)))

    @Test fun optionalToneIdentityIsExactAndChangesOnlyObservation() {
        val before = optical.opticalDensityRgb.values.copyOf()
        val baseline = observe(emptyMap())
        val identity = observe(mapOf("tone_curve" to listOf(listOf(0.0, 0.0), listOf(0.5, 0.5), listOf(1.0, 1.0))))
        assertArrayEquals(baseline.outputRgb.values, identity.outputRgb.values, 0f)
        val changed = observe(mapOf("tone_curve" to listOf(listOf(0.0, 0.1), listOf(1.0, 1.0))))
        assertFalse(baseline.outputRgb.values.contentEquals(changed.outputRgb.values))
        val ratio = changed.outputRgb.values[0] / baseline.outputRgb.values[0]
        for (channel in 1..2) assertEquals(ratio, changed.outputRgb.values[channel] / baseline.outputRgb.values[channel], 1e-6f)
        assertEquals("piecewise_output_luma_v1", (changed.metadata["scanner_color_response"] as Map<*,*>)["tone"])
        assertArrayEquals(before, optical.opticalDensityRgb.values, 0f)
    }

    @Test fun toneRunsAfterRgbAndViewerIgnoresBoth() {
        val tone = listOf(listOf(0.0, 0.1), listOf(1.0, 1.0))
        val rgb = List(3) { listOf(listOf(0.0, 0.0), listOf(1.0, 0.8)) }
        val rgbOnly = observe(mapOf("color_curve_rgb" to rgb))
        val combined = observe(mapOf("tone_curve" to tone, "color_curve_rgb" to rgb))
        for (pixel in 0 until 2) {
            val i = pixel * 3
            val inputBrightness = rgbOnly.outputRgb.values[i] * .2126f + rgbOnly.outputRgb.values[i+1] * .7152f + rgbOnly.outputRgb.values[i+2] * .0722f
            val outputBrightness = combined.outputRgb.values[i] * .2126f + combined.outputRgb.values[i+1] * .7152f + combined.outputRgb.values[i+2] * .0722f
            assertEquals(.1f + .9f * inputBrightness, outputBrightness, 1e-6f)
        }
        val viewer = mapOf("observe_mode" to "viewer_light_table")
        val plain = kernel.observe(optical, viewer)
        val curved = kernel.observe(optical, viewer + ("scanner" to mapOf("tone_curve" to tone, "color_curve_rgb" to rgb)))
        assertArrayEquals(plain.outputRgb.values, curved.outputRgb.values, 0f)
    }

    @Test fun invalidToneCannotBeSilentlyIgnored() {
        assertThrows(IllegalArgumentException::class.java) { observe(mapOf("tone_curve" to listOf(listOf(0.5, 0.0), listOf(0.5, 1.0)))) }
    }
}
