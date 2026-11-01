package com.filmfoundry.kernel

import com.filmfoundry.interchange.SpectralOptics
import kotlin.math.pow

/** Wavelength transmission, then the observer integral.
 * Each wavelength uses 10^(-D(λ)); each channel is the weighted mean of those transmissions.
 * One wavelength walk fills all three channels. This is the phone observation when a spectrum is present.
 */
internal class PhoneSpectralCapture(optical: OpticalMaster, value: Any?) {
    private val amounts = optical.spectralComponentAmounts
        ?: throw KernelException("spectral observation requires final component amounts.")
    private val definition = SpectralOptics.objectValue(optical.metadata[SpectralOptics.MEDIUM_KEY])
    private val material = SpectralOptics.material(definition["profile"])
    private val observer = SpectralOptics.observer(value)
    private val base = SpectralOptics.vector(definition["base_component_amounts"], 9)
    private val weights = Array(3) { DoubleArray(0) }
    private val postProcess = optical.spectralPostProcessDensityRgb
    private val overlayWeights = Array(observer.wave.size) { SpectralOptics.postProcessWeights(observer.wave[it]) }
    private val clearBase = FloatArray(3)
    private val sampleAmounts=DoubleArray(9)
    private val sum=DoubleArray(3)
    private val absorbanceByWavelength=Array(observer.wave.size) { n -> DoubleArray(9) { k -> material.absorbance[k][n] } }
    private var cachedPixel = -1
    private val cachedTransmission = FloatArray(3)
    val profileId = observer.metadata["profile_id"]

    init {
        SpectralOptics.validateMedium(optical.metadata, amounts, optical.opticalDensityRgb.shape, postProcess)
        if (!material.wave.contentEquals(observer.wave)) throw KernelException("spectral wavelength grids must match.")
        val quadrature = DoubleArray(observer.wave.size)
        for (n in 0 until quadrature.size-1) {
            val half = (observer.wave[n+1] - observer.wave[n]) * 0.5
            quadrature[n] += half
            quadrature[n+1] += half
        }
        for (c in 0..2) {
            val row = DoubleArray(quadrature.size) { observer.light[it] * observer.response[c][it] * quadrature[it] }
            val sum = row.sum()
            if (!sum.isFinite() || sum <= 0.0) throw KernelException("spectral weights are not representable.")
            weights[c] = DoubleArray(row.size) { row[it] / sum }
            if (weights[c].any { !it.isFinite() }) throw KernelException("spectral weights are not representable.")
        }
        integrate(null, clearBase)
    }

    fun transmission(pixel: Int, channel: Int): Float {
        if (pixel != cachedPixel) {
            integrate(pixel, cachedTransmission)
            cachedPixel = pixel
        }
        return cachedTransmission[channel]
    }

    fun baseTransmission(channel: Int) = clearBase[channel]

    fun accelerated(compute: DensityTransmissionCompute?): FloatArray? {
        if(compute==null) return null
        val plan=SpectralTransmissionPlan(observer.wave.size,
            DoubleArray(observer.wave.size*9) { absorbanceByWavelength[it/9][it%9] },
            DoubleArray(observer.wave.size*3) { weights[it%3][it/3] },
            DoubleArray(observer.wave.size*3) { overlayWeights[it/3][it%3] })
        return compute.transmitSpectral(amounts.values,postProcess?.values,plan)?.also {
            require(it.size==amounts.values.size/3 && it.all { value -> value>=0f && value<=1.00001f })
        }
    }

    private fun integrate(pixel: Int?, out: FloatArray) {
        sum.fill(0.0)
        for(k in 0..8) sampleAmounts[k]=if(pixel==null) base[k] else amounts.values[pixel*9+k].toDouble()
        for (n in observer.wave.indices) {
            var density = 0.0
            for (k in 0..8) {
                density += sampleAmounts[k] * absorbanceByWavelength[n][k]
            }
            if (pixel != null && postProcess != null) {
                val overlay = overlayWeights[n]
                for (k in 0..2) density += postProcess.values[pixel*3+k] * overlay[k]
                if (density < 0.0) density = 0.0
            }
            val transmission = 10.0.pow(-density.coerceAtMost(80.0))
            for (c in 0..2) sum[c] += transmission * weights[c][n]
        }
        for (c in 0..2) {
            if (!sum[c].isFinite()) throw KernelException("spectral transmission is not representable.")
            out[c] = sum[c].toFloat()
        }
    }

    companion object {
        const val MODEL = "phone_spectral_wavelength_transmission_v1"
    }
}
