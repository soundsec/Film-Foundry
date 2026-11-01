package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.pow

class RangeFirstReadoutTest {
    private val kernel=PhoneReducedKernel()
    private val scanner=mapOf("print_mapping_mode" to "sigmoid","remove_base_mask" to true,"invert_transmission" to true,
        "scan_normalize" to true,"scan_normalize_strength" to 1.0,"scan_black_percentile" to 0.0,"scan_white_percentile" to 100.0)
    private fun film()=OpticalMaster(FloatNd(intArrayOf(1,101,3),FloatArray(303) { .2f+(it/3)*.02f }),
        mapOf("image_polarity" to "negative"),FloatArray(3){.2f})
    @Test fun negativeRangeIsMeasuredBeforeShoulderAgainstIndependentDensityWedge() {
        val film=film();val before=film.opticalDensityRgb.values.copyOf()
        val values=kernel.observe(film,mapOf("scanner" to scanner)).outputRgb.values
        for(p in 0..100) {
            val density=(film.opticalDensityRgb.values[p*3]-.2f).toDouble()
            val white=(film.opticalDensityRgb.values[300]-.2f).toDouble()
            val linear=(10.0.pow(density)-1)/(10.0.pow(white)-1)
            val expected=if(linear<=.0031308) linear*12.92 else 1.055*linear.pow(1/2.4)-.055
            assertEquals(expected,values[p*3].toDouble(),3e-6)
        }
        val previous=kernel.observe(film,mapOf("scanner" to scanner,PhoneScanModels.OPTION to PhoneScanModels.CALIBRATED)).outputRgb.values
        assertTrue(previous[150]>values[150]+.2f)
        assertArrayEquals(before,film.opticalDensityRgb.values,0f)
    }
    @Test fun normalizationBlendAndLampColorAgreeAcrossMemoryPreparedAndRows() {
        val film=film()
        for(mode in listOf("rgb","luma")) for(strength in listOf(0.0,.4,1.0)) {
            val recipe=mapOf("scanner" to (scanner+mapOf("scan_normalize_mode" to mode,"scan_normalize_strength" to strength,"transmission_light_temperature_k" to 3200)))
            val expected=kernel.observe(film,recipe).outputRgb.values
            assertArrayEquals(expected,kernel.prepareObservation(film,recipe).observe(recipe).outputRgb.values,0f)
            val rows=PhoneRowObservation(recipe);val raw=rows.linear(film).outputRgb.values
            val normalize=rows.normalizer(101){it(raw)};val actual=raw.copyOf();normalize(actual);rows.encode(actual)
            assertArrayEquals(expected,actual,0f)
            assertTrue(actual.all { it.isFinite() && it in 0f..1f })
        }
        fun at(k: Int)=kernel.observe(film,mapOf("scanner" to (scanner+("transmission_light_temperature_k" to k)))).outputRgb.values
        val warm=at(3000);val cool=at(9000)
        assertTrue(warm.indices.maxOf { kotlin.math.abs(warm[it]-cool[it]) }>.1f)
        val spectral=RecipeShareCodec.decode(javaClass.getResourceAsStream("/spectral_optics_sample.ffc2")!!.bufferedReader().readText())
        val master=kernel.develop(kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.01f,.01f,.01f,.12f,.18f,.23f,.9f,.8f,.7f)),spectral),spectral)
        val source=(spectral["scanner"] as Map<String,Any?>)+scanner
        val a=kernel.observe(master,spectral+("scanner" to (source+("transmission_light_temperature_k" to 3000)))).outputRgb.values
        val b=kernel.observe(master,spectral+("scanner" to (source+("transmission_light_temperature_k" to 9000)))).outputRgb.values
        assertTrue(a.indices.maxOf { kotlin.math.abs(a[it]-b[it]) }>.03f)
    }
    @Test fun byteEncodingRetainsReferenceTruncationIncludingEveryByteBoundary() {
        val samples=mutableListOf(-1f,0f,1f,2f,Float.NaN)
        val random=java.util.Random(91)
        repeat(12000) { samples+=random.nextFloat() }
        for(code in 1..254) {
            val s=code/255.0;val x=(if(s<=.04045) s/12.92 else ((s+.055)/1.055).pow(2.4)).toFloat()
            for(delta in -12..12) samples+=Float.fromBits(x.toRawBits()+delta)
        }
        for(x in samples) assertEquals((PhoneObserve.encodeDisplaySample(x)*255).toInt(),DisplayByteEncoding.encode(x),"$x")
    }
}
