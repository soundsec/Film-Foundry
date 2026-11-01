package com.filmfoundry.kernel

import kotlin.math.max
import kotlin.math.pow

/** Optional platform-independent math port. Input density is read-only; output is newly owned. */
interface DensityTransmissionCompute {
    fun transmit(density: FloatArray): FloatArray
    /** Null means unavailable: the observer executes its unchanged CPU wavelength integral. */
    fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: SpectralTransmissionPlan): FloatArray?=null
    /** Whole readout chain: normalization application, exposure, contrast, encoding and curves.
     * Input is read-only; null requests the CPU reference. Implementations need not support it.
     */
    fun encodeOutput(linear: FloatArray,plan: ScanOutputPlan): FloatArray?=null
    companion object {
        fun reference(density: FloatArray)=FloatArray(density.size) { i ->
            10.0.pow((-max(density[i],0f)).toDouble()).toFloat().coerceAtLeast(1e-6f)
        }
    }
}
