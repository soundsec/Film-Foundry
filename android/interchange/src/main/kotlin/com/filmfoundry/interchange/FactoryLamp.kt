package com.filmfoundry.interchange

import kotlin.math.pow

/** Factory mask exposure in linear RGB, before development and independent of camera light. */
data class FactoryLamp(val ev: Double = 0.0, val rgb: List<Double> = listOf(1.0,1.0,1.0)) {
    init {
        require(ev in -4.0..4.0 && rgb.size==3 && rgb.all { it in 0.0..1.0 }) { "Invalid factory lamp exposure or RGB" }
    }

    fun record() = mapOf("factory_exposure_ev" to ev, "factory_light_color_rgb" to rgb)

    fun applyTo(stencil: FloatArray): FloatArray {
        val gain = 2.0.pow(ev)
        return FloatArray(stencil.size) { i -> (stencil[i]*gain*rgb[i%3]).toFloat() }
    }

    companion object {
        fun read(config: Map<*, *>): FactoryLamp {
            val ev = config["factory_exposure_ev"] ?: 0.0
            val rgb = config["factory_light_color_rgb"] ?: listOf(1.0,1.0,1.0)
            require(ev is Number && rgb is List<*> && rgb.all { it is Number })
            return FactoryLamp(ev.toDouble(), rgb.map { (it as Number).toDouble() })
        }
    }
}
