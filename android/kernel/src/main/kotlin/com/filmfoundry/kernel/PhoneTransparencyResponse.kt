package com.filmfoundry.kernel

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.ln1p

/** Optical yield of the formed reversal image. The reaction ledger keeps the original amounts. */
internal class PhoneTransparencyResponse(private val stock: Map<String, Any?>) {
    private fun field(name: String, fallback: Double) =
        (stock["positive_$name"] as? Number)?.toDouble() ?: fallback
    private val contrast = field("density_contrast", 1.0).coerceAtLeast(.01)
    private val bias = 4 * field("density_bias", 0.0)
    private val latitude = field("latitude_compression", 0.0).coerceIn(0.0, 1.0)
    private val midtone = .20 * field("midtone_density", 0.0).coerceIn(0.0, 1.0)
    private val toeWidth = field("shadow_toe_width", .22).coerceIn(.02, .8).toFloat()
    private val shoulderWidth = field("highlight_shoulder_width", .18).coerceIn(.02, .8).toFloat()
    private val toe = .45 * toeWidth * field("shadow_toe", 0.0).coerceIn(0.0, 1.0)
    private val shoulder = .55 * shoulderWidth * field("highlight_shoulder", 0.0).coerceIn(0.0, 1.0)
    private val saturation = field("dye_saturation", 1.0).coerceAtLeast(0.0).toFloat()
    private val highlight = field("highlight_chroma_retention", .22).coerceIn(0.0, 1.0).toFloat()
    private val shadow = field("shadow_chroma_retention", .28).coerceIn(0.0, 1.0).toFloat()

    private fun smooth(value: Float): Float {
        val v = value.coerceIn(0f, 1f)
        return v * v * (3f - 2f * v)
    }

    // Build once per development setup. Every row block shares this stock curve.
    private val table = FloatArray(4097) { index ->
        val x = (index / 4096.0).coerceIn(.000001, .999999)
        val logit = ((ln(x) - ln1p(-x)) * contrast + bias).coerceIn(-30.0, 30.0)
        var y = (1.0 / (1.0 + exp(-logit))).toFloat()
        y += (latitude * (smooth(y) - y)).toFloat()
        y += (midtone * smooth(1f - abs(2f * y - 1f))).toFloat()
        y -= (toe * smooth((y - (1f - toeWidth)) / toeWidth)).toFloat()
        y += (shoulder * smooth(1f - y / shoulderWidth)).toFloat()
        y.coerceIn(0f, 1f)
    }

    fun density(fraction: Float): Float {
        val position = fraction.coerceIn(0f, 1f) * table.lastIndex
        val i = position.toInt().coerceAtMost(table.lastIndex - 1)
        return table[i] + (position - i) * (table[i + 1] - table[i])
    }

    fun dye(amount: FloatArray) {
        for (c in 0..2) amount[c] = density(amount[c])
        val mean = (amount[0] + amount[1] + amount[2]) / 3f
        val gain = saturation * (highlight + (1f - highlight) * smooth(mean / shoulderWidth)) *
            (shadow + (1f - shadow) * smooth((1f - mean) / toeWidth))
        for (c in 0..2) amount[c] = (mean + (amount[c] - mean) * gain).coerceIn(0f, 1f)
    }
}
