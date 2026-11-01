package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import com.filmfoundry.interchange.FloatNd

class PointwiseDevelopTest {
    @Test fun preservingAndConsumingPathsHaveIdenticalOpticalResultsAndPoolOwnership() {
        val kernel = PhoneReducedKernel()
        for (bw in listOf(false, true)) {
            val recipe = if (bw) mapOf("enable_subtractive" to false) else emptyMap()
            val latent = kernel.expose(RgbVolume(37, 19, linearRgb = FloatArray(37 * 19 * 3) { (it % 97) / 97f }), recipe)
            val before = latent.arrays.mapValues { it.value.copy() }
            val kept = kernel.develop(latent, recipe)
            for (key in before.keys) assertArrayEquals(before.getValue(key).values, latent.arrays.getValue(key).values, 0f)
            val consumed = kernel.develop(latent, recipe, true)
            assertArrayEquals(kept.opticalDensityRgb.values, consumed.opticalDensityRgb.values, 0f)
            assertArrayEquals(kept.clearBaseOpticalDensityRgb, consumed.clearBaseOpticalDensityRgb, 0f)
            assertEquals(kept.metadata, consumed.metadata)
            assertFalse(before.getValue("halide").values.contentEquals(latent.arrays.getValue("halide").values))
            assertArrayEquals(before.getValue("developability").values, latent.arrays.getValue("developability").values, 0f)
        }
    }

    @Test fun mismatchedOptionalPoolsFailBeforeConsumingInput() {
        val kernel = PhoneReducedKernel()
        val latent = kernel.expose(RgbVolume(2, 2), emptyMap())
        val before = latent.arrays.getValue("halide").values.copyOf()
        val malformed = LatentState(latent.arrays + ("dye" to FloatNd(intArrayOf(1, 1, 3), FloatArray(3))), latent.metadata)
        assertThrows(KernelException::class.java) { kernel.develop(malformed, emptyMap(), true) }
        assertArrayEquals(before, latent.arrays.getValue("halide").values, 0f)
    }
}
