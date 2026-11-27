package com.filmfoundry.kernel

import kotlin.math.exp
import kotlin.math.ln1p
import kotlin.math.log10

/** Material-only H-D response. Prepare once per exposure and reuse it for every row. */
internal class StockExposureCurve(film: Map<String, Any?>, layers: Int) : LayerResponse {
    private fun vector(film: Map<String, Any?>, key: String, vararg defaults: Double): List<Double> {
        val values = film[key] ?: return defaults.toList()
        require(values is List<*> && values.size == 3) { "Invalid $key" }
        return values.map { require(it is Number && it.toDouble().isFinite()) { "Invalid $key" }; it.toDouble() }
    }

    private fun scalar(film: Map<String, Any?>, key: String, default: Double): Double {
        val value = film[key] ?: return default
        require(value is Number && value.toDouble().isFinite()) { "Invalid $key" }
        return value.toDouble()
    }

    private val toeWidth = scalar(film, "hd_toe_width", .18)
    private val shoulderWidth = scalar(film, "hd_shoulder_width", .22)
    private val reversal = scalar(film, "extreme_exposure_reversal_strength", 0.0)
    private val reversalWidth = scalar(film, "extreme_exposure_reversal_width", .18)
    private val curves: Array<(Double) -> Float>
    private val tables: Array<FloatArray>

    init {
        require(toeWidth > 0 && shoulderWidth > 0 && reversalWidth > 0 && reversal in 0.0..1.0)
        val gamma = vector(film, "hd_gamma", .62, .66, .60)
        val toe = vector(film, "log_exposure_toe", -2.20, -2.15, -2.25)
        val shoulder = vector(film, "log_exposure_shoulder", .45, .48, .42)
        val low = vector(film, "density_min", .10, .10, .10)
        val high = vector(film, "density_max", 1.85, 1.95, 1.75)
        val ranges = high.indices.map { (high[it] - low[it]).coerceAtLeast(1e-6) }
        val start = vector(film, "extreme_exposure_reversal_start_loge", .56, .56, .56)
        fun List<Double>.layer(i: Int) = if (layers == 1) average() else this[i]
        curves = Array(layers) { i ->
            val slope = gamma.layer(i) / ranges.layer(i)
            val toeAt = toe.layer(i)
            val shoulderAt = shoulder.layer(i)
            val reverseAt = start.layer(i)
            require(shoulderAt > toeAt && slope > 0.0)
            fun evaluate(logE: Double): Float {
                val delta = softplus(logE - toeAt, toeWidth) - softplus(logE - shoulderAt, shoulderWidth)
                val activation = (delta * slope).coerceIn(0.0, 1.0)
                if (reversal == 0.0) return activation.toFloat()
                val t = ((logE - reverseAt) / reversalWidth).coerceIn(0.0, 1.0)
                return (activation * (1.0 - reversal * t * t * (3.0 - 2.0 * t))).toFloat()
            }
            ::evaluate
        }
        // Keep per-pixel work to one logarithm and linear interpolation. Narrow
        // research transitions and out-of-table highlights use the formula directly.
        tables = Array(layers) { layer ->
            if (minOf(toeWidth, shoulderWidth) < .02 || reversal > 0 && reversalWidth < .02) FloatArray(0)
            else FloatArray(TABLE_INTERVALS + 1) { curves[layer](LOG_MIN + it.toDouble() / SAMPLES_PER_DECADE) }
        }
    }

    override fun activation(exposure: Float, layer: Int): Float {
        val logE = log10(exposure.toDouble().coerceAtLeast(1e-6))
        val table = tables[layer]
        val t = (logE - LOG_MIN) * SAMPLES_PER_DECADE
        if (table.isEmpty() || !(t >= 0 && t < TABLE_INTERVALS)) return curves[layer](logE)
        val i = t.toInt()
        return table[i] + (table[i + 1] - table[i]) * (t - i).toFloat()
    }

    private companion object {
        const val LOG_MIN = -6.0
        const val SAMPLES_PER_DECADE = 128
        const val TABLE_INTERVALS = 10 * SAMPLES_PER_DECADE
        fun softplus(x: Double, width: Double) = width * ln1p(exp((x / width).coerceIn(-60.0, 60.0)))
    }
}
