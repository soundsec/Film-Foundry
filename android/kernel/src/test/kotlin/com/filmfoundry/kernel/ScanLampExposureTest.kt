package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.interchange.PhoneScanModels
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanLampExposureTest {
    private val kernel=PhoneReducedKernel()
    private fun master(positive: Boolean)=OpticalMaster(FloatNd(intArrayOf(4,17,3),FloatArray(4*17*3) { i ->
        .2f+(i/3%17)*.065f+(i%3)*.03f
    }),mapOf("image_polarity" to if(positive) "positive" else "negative"),floatArrayOf(.2f,.22f,.24f))
    private fun recipe(ev: Double,strength: Double,base: Boolean,invert: Boolean?=null): Map<String,Any?> = mapOf("scanner" to
        (mapOf("print_mapping_mode" to "sigmoid","transmission_light_ev" to ev,"remove_base_mask" to base,
            "scan_normalize" to true,"scan_normalize_strength" to strength)+if(invert==null) emptyMap() else mapOf("invert_transmission" to invert)))

    @Test fun lightExposureRemainsBidirectionalAcrossBothPolaritiesBaseCorrectionAndNormalization() {
        for(positive in listOf(false,true)) for(base in listOf(false,true)) for(strength in listOf(0.0,.45,1.0)) {
            val film=master(positive);val original=film.opticalDensityRgb.values.copyOf()
            val samples=listOf(-1.0,0.0,1.0).map { ev -> kernel.observe(film,recipe(ev,strength,base)).outputRgb.values.average() }
            if(positive) assertTrue(samples[0]<samples[1] && samples[1]<samples[2],"positive/$base/$strength: $samples")
            else assertTrue(samples[0]>samples[1] && samples[1]>samples[2],"negative/$base/$strength: $samples")
            assertArrayEquals(original,film.opticalDensityRgb.values,0f)
            val neutral=recipe(0.0,strength,base)
            assertArrayEquals(kernel.observe(film,neutral+(PhoneScanModels.OPTION to PhoneScanModels.RESULT_BASE)).outputRgb.values,
                kernel.observe(film,neutral+(PhoneScanModels.OPTION to PhoneScanModels.CALIBRATED)).outputRgb.values,0f)
        }
        val film=master(false)
        val dark=kernel.observe(film,recipe(-1.0,1.0,true,invert=false)).outputRgb.values.average()
        val light=kernel.observe(film,recipe(1.0,1.0,true,invert=false)).outputRgb.values.average()
        assertTrue(dark<light,"Manual inversion governs exposure direction, not stock type")
    }

    @Test fun preparedRowsAndReferenceEncodingAgreeAndViewerLightingIsUnchanged() {
        val device=mapOf("scanner_response_matrix" to listOf(listOf(.8,.1,.05),listOf(.05,.9,.05),listOf(.1,.05,.8)),
            "device_response_rgb" to List(3) { listOf(listOf(0.0,0.0),listOf(.4,.3),listOf(1.0,1.0)) })
        for(positive in listOf(false,true)) for(response in listOf(emptyMap(),device)) {
            fun settings(ev: Double): Map<String,Any?> {
                val config=recipe(ev,.45,true)
                @Suppress("UNCHECKED_CAST") val scanner=config["scanner"] as Map<String,Any?>
                return config+("scanner" to (scanner+response))
            }
            val film=master(positive);val base=settings(0.0)
            val prepared=kernel.prepareObservation(film,base)
            for(ev in listOf(-1.0,0.0,1.0)) {
                val changed=settings(ev);assertTrue(prepared.matches(changed))
                val expected=kernel.observe(film,changed)
                val reused=prepared.observe(changed)
                assertArrayEquals(expected.outputRgb.values,reused.outputRgb.values,0f);assertEquals(expected.metadata,reused.metadata)
                val rows=PhoneRowObservation(changed);val linear=rows.linear(film).outputRgb.values
                val normalize=rows.normalizer(linear.size/3) { it(linear) }
                val output=linear.copyOf();normalize(output)
                val separate=output.copyOf();PhoneObserve.encodeRowsReference(separate,changed,invertTransmission=!positive)
                rows.encode(output)
                assertArrayEquals(expected.outputRgb.values,output,0f);assertArrayEquals(separate,output,0f)
                for(mode in listOf("viewer_light_table","viewer_projection")) {
                    val viewer=changed+("observe_mode" to mode)
                    assertArrayEquals(kernel.observe(film,viewer+(PhoneScanModels.OPTION to PhoneScanModels.RESULT_BASE)).outputRgb.values,
                        kernel.observe(film,viewer).outputRgb.values,0f)
                }
            }
        }
    }
}
