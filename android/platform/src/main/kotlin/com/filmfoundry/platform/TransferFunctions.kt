package com.filmfoundry.platform

import kotlin.math.pow

/**
 * IEC 61966-2-1 sRGB EOTF used by desktop `srgb_to_linear`.
 *
 * This builds the same display-referred linear working space the desktop
 * loader uses. It is not restored scene irradiance.
 */
object TransferFunctions {
    const val SRGB_TOE = 0.04045f
    const val SRGB_LINEAR_SCALE = 12.92f
    private val byteEncoded=FloatArray(256) { it/255f }
    private val byteLinear=FloatArray(256) { decode(byteEncoded[it]) }

    /** Exact 8-bit decode cache; arbitrary/HDR float values still use the reference formula. */
    fun srgbToLinearInPlace(encoded: FloatArray): FloatArray {
        for(i in encoded.indices) {
            val u=encoded[i];val index=(u.coerceIn(0f,1f)*255f+.5f).toInt()
            encoded[i]=if(u.toRawBits()==byteEncoded[index].toRawBits()) byteLinear[index] else srgbToLinear(u)
        }
        return encoded
    }

    fun srgbToLinear(encoded: Float): Float {
        val u = encoded.coerceIn(0f, 1f)
        val index=(u*255f+.5f).toInt().coerceIn(0,255)
        return if(u.toRawBits()==byteEncoded[index].toRawBits()) byteLinear[index] else decode(u)
    }
    private fun decode(u: Float): Float {
        return if (u <= SRGB_TOE) {
            u / SRGB_LINEAR_SCALE
        } else {
            ((u + 0.055f) / 1.055f).toDouble().pow(2.4).toFloat()
        }
    }

    fun srgbToLinear(encoded: FloatArray): FloatArray {
        return FloatArray(encoded.size) { index -> srgbToLinear(encoded[index]) }
    }
}
