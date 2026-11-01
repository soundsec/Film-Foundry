package com.filmfoundry.kernel

import kotlin.math.max
import kotlin.math.min
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

/** Small independent legacy oracle; no image hashes, timing assertions or large fixtures. */
class ScanNormalizationTest {
    @Test fun fullPixelPercentilesRemainExactAcrossModesAndBoundaries() {
        val inputs = listOf(
            floatArrayOf(0f, 0f, 0f),
            FloatArray(24) { .25f },
            floatArrayOf(-0f, 0f, 1f, 2f, 1e-7f, 3f, .2f, .3f, .4f),
            FloatArray(63) { ((it * 7919 % 65521) / 65521f) * 4f - .2f },
            FloatArray(66) { (it % 5) * .125f },
        )
        for (input in inputs) for (mode in listOf("luma", "rgb")) {
            for ((black, white) in listOf(.3f to 99.7f, 0f to 100f, 50f to 50f, -1f to 101f)) {
                for (strength in listOf(0f, .45f, 1f)) {
                    val expected = input.copyOf()
                    val actual = input.copyOf()
                    reference(expected, input.size / 3, strength, mode, black, white)
                    PhoneObserve.normalize(actual, input.size / 3, strength, mode, black, white)
                    assertArrayEquals(expected, actual, 0f, "$mode/$black/$white/$strength/${input.size}")
                }
            }
        }
    }

    @Test fun selectedRanksMatchSortForLargeOrderedRepeatedAndSignedZeroInputs() {
        val samples = listOf(
            FloatArray(4099 * 3) { (it * 7919 % 65521) / 1024f - .2f },
            FloatArray(4099 * 3) { it / 333f },
            FloatArray(4099 * 3) { (12300 - it) / 333f },
            FloatArray(4099 * 3) { if (it % 3 == 0) -0f else if (it % 3 == 1) 0f else .4f },
        )
        for (input in samples) for (mode in listOf("luma", "rgb")) {
            val expected = input.copyOf()
            PhoneObserve.normalizeReference(expected, input.size / 3, .63f, mode, .3f, 99.7f)
            val actual = input.copyOf()
            PhoneObserve.normalize(actual, input.size / 3, .63f, mode, .3f, 99.7f)
            assertArrayEquals(expected, actual, 0f)
        }
    }

    @Test fun oneGlobalMeasurementAppliesToSeparateRowsAndKeepsSourceReadOnly() {
        val input = FloatArray(37 * 19 * 3) { ((it * 1297) % 65521) / 65521f }
        val before = input.copyOf()
        for (mode in listOf("luma", "rgb")) {
            val expected = input.copyOf()
            PhoneObserve.normalizeReference(expected, 37 * 19, .45f, mode, 1.3f, 98.4f)
            val measured = ScanNormalization.measure(input, 37 * 19, .45f, mode, 1.3f, 98.4f)
            assertArrayEquals(before, input, 0f)
            val actual = FloatArray(input.size)
            for (start in 0 until 37 * 19 step 111) {
                val count = min(111, 37 * 19 - start)
                val row = input.copyOfRange(start * 3, (start + count) * 3)
                measured.apply(row)
                row.copyInto(actual, start * 3)
            }
            assertArrayEquals(expected, actual, 0f)
        }
        assertArrayEquals(before, input, 0f)
    }
    private fun reference(
        linear: FloatArray,
        pixels: Int,
        strength: Float,
        mode: String,
        blackPercentile: Float,
        whitePercentile: Float,
    ) {
        if (strength <= 0f || pixels <= 0) {
            return
        }
        if (mode == "rgb") {
            for (channel in 0 until 3) {
                val samples = FloatArray(pixels) { linear[it * 3 + channel] }
                val lo = percentile(samples, blackPercentile)
                val hi = percentile(samples, whitePercentile)
                val span = hi - lo
                if (span <= 1e-6f) continue
                for (i in 0 until pixels) {
                    val src = linear[i * 3 + channel]
                    val stretched = ((src - lo) / span).coerceIn(0f, 1f)
                    linear[i * 3 + channel] = src * (1f - strength) + stretched * strength
                }
            }
            return
        }
        val luma = FloatArray(pixels) { i ->
            0.2126f * linear[i * 3] + 0.7152f * linear[i * 3 + 1] + 0.0722f * linear[i * 3 + 2]
        }
        val lo = percentile(luma, blackPercentile)
        val hi = percentile(luma, whitePercentile)
        val span = hi - lo
        if (span <= 1e-6f) {
            return
        }
        for (i in 0 until pixels) {
            val y = luma[i]
            val stretched = ((y - lo) / span).coerceIn(0f, 1f)
            val gain = if (y > 1e-6f) {
                (y * (1f - strength) + stretched * strength) / y
            } else {
                0f
            }
            for (channel in 0 until 3) {
                linear[i * 3 + channel] = max(linear[i * 3 + channel] * gain, 0f)
            }
        }
    }

    private fun percentile(samples: FloatArray, percentile: Float): Float {
        val sorted = samples.sorted()
        if (sorted.isEmpty()) {
            return 0f
        }
        val t = (percentile.coerceIn(0f, 100f) / 100f) * (sorted.lastIndex)
        val lo = t.toInt().coerceIn(0, sorted.lastIndex)
        val hi = min(lo + 1, sorted.lastIndex)
        val frac = t - lo
        return sorted[lo] * (1f - frac) + sorted[hi] * frac
    }


}
