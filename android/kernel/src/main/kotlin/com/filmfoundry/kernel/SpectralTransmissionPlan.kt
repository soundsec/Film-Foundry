package com.filmfoundry.kernel

import kotlin.math.pow

/** Read-only numerical coefficients, with the same wavelength order as the CPU observer.
 * No Android objects or material ownership cross this optional compute port.
 */
class SpectralTransmissionPlan(val wavelengths: Int, val absorbance: DoubleArray,
    val weights: DoubleArray, val overlay: DoubleArray) {
    init {
        require(wavelengths>=2 && absorbance.size==wavelengths*9 && weights.size==wavelengths*3 && overlay.size==wavelengths*3)
        require(absorbance.all { it.isFinite() } && weights.all { it.isFinite() } && overlay.all { it.isFinite() })
    }
    /** Small qualification/reference blocks only; production CPU observation retains its original loop. */
    fun reference(amounts: FloatArray,post: FloatArray?): FloatArray {
        require(amounts.size%9==0 && (post==null || post.size==amounts.size/3))
        return FloatArray(amounts.size/3).also { result ->
            val sum=DoubleArray(3)
            for(pixel in 0 until amounts.size/9) {
                sum.fill(0.0)
                for(n in 0 until wavelengths) {
                    var density=0.0
                    for(k in 0..8) density+=amounts[pixel*9+k].toDouble()*absorbance[n*9+k]
                    if(post!=null) {
                        for(k in 0..2) density+=post[pixel*3+k]*overlay[n*3+k]
                        if(density<0.0) density=0.0
                    }
                    val transmission=10.0.pow(-density.coerceAtMost(80.0))
                    for(c in 0..2) sum[c]+=transmission*weights[n*3+c]
                }
                for(c in 0..2) result[pixel*3+c]=sum[c].toFloat()
            }
        }
    }
}
