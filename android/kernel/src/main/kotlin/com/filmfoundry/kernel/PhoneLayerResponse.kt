package com.filmfoundry.kernel

import kotlin.math.*

/** Prepared bounded layer response using shared stock curve fields; independent phone reduction. */
internal class PhoneLayerResponse(private val film: Map<String,Any?>,private val layers: Int,contrast: Float) {
    private fun vector(key: String,fallback: Double): List<Double> {
        val raw=film[key] as? List<*> ?: List(3) { fallback }
        require(raw.size==3 && raw.all { it is Number && it.toDouble().isFinite() }) { "invalid $key" }
        val values=raw.map { (it as Number).toDouble() }
        return if(layers==1) listOf(values.average()) else values
    }
    private val gamma=vector("hd_gamma",.62).map { require(it in .1..3.0);(it/.62).toFloat() }
    private val toe=vector("log_exposure_toe",-2.1)
    private val shoulder=vector("log_exposure_shoulder",.6)
    private val half=toe.indices.map { i -> require(shoulder[i]>toe[i]);10.0.pow(((toe[i]+shoulder[i])/2).coerceIn(-6.0,3.0)).toFloat()/contrast }
    fun activation(exposure: Float,layer: Int): Float {
        if(exposure<=0f) return 0f
        val ratio=(half[layer]/exposure).pow(gamma[layer])
        return 1f/(1f+ratio)
    }
}
