package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import kotlin.math.ceil
import kotlin.math.exp
import kotlin.math.pow

class PhoneExposureOpticsBlurTest {
    @Test fun mtfInputKeepsFiniteExtremesAndRejectsNonfiniteSamples() {
        val optics=PhoneExposureOptics(mapOf("film" to mapOf("emulsion_mtf_strength" to 1.0)),1,1,1f)
        fun source(value: Float)=object: LinearRgbRows {
            override val width=1;override val height=1
            override fun readRows(start: Int,count: Int)=RgbVolume(1,1,linearRgb=FloatArray(3){value})
        }
        for(value in listOf(-Float.MAX_VALUE,-Float.MIN_VALUE,-0f,0f,Float.MIN_VALUE,Float.MAX_VALUE)) {
            optics.bind(source(value)).use { bound ->
                assertTrue(bound.readRows(0,1).linearRgb.all { it.isFinite() })
            }
        }
        for(value in listOf(Float.NaN,Float.POSITIVE_INFINITY,Float.NEGATIVE_INFINITY)) {
            optics.bind(source(value)).use { bound ->
                assertThrows(IllegalArgumentException::class.java) { bound.readRows(0,1) }
            }
        }
    }

    @Test fun interiorSamplesMatchTheClampedReferenceInOrder() {
        val w=17;val h=9;val source=FloatArray(w*h) { i -> ((i*17)%255)/255f }
        val actual=PhoneExposureOptics.blur(source,w,h,1.2f) {}
        val sigma=1.2f;val r=ceil(sigma*3).toInt()
        val weights=DoubleArray(2*r+1) { exp(-((it-r).toDouble().pow(2))/(2*sigma*sigma)) }
        val total=weights.sum();for(i in weights.indices) weights[i]/=total
        val horizontal=FloatArray(source.size)
        for(y in 0 until h) for(x in 0 until w) {
            var sum=0.0;for(k in -r..r) sum+=source[y*w+(x+k).coerceIn(0,w-1)]*weights[k+r]
            horizontal[y*w+x]=sum.toFloat()
        }
        for(y in 0 until h) for(x in 0 until w) {
            var sum=0.0;for(k in -r..r) sum+=horizontal[(y+k).coerceIn(0,h-1)*w+x]*weights[k+r]
            assertEquals(sum.toFloat(),actual[y*w+x])
        }
    }
}
