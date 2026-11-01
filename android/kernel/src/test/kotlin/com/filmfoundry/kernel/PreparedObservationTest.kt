package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PreparedObservationTest {
    @Test fun readoutReuseIsExactAcrossInterpretationChangesForRgbAndSpectralMedia() {
        val kernel=PhoneReducedKernel()
        val spectral=RecipeShareCodec.decode(javaClass.getResourceAsStream("/spectral_optics_sample.ffc2")!!.bufferedReader().use { it.readText() })
        val curve=List(3) { listOf(listOf(0.0,.01),listOf(.4,.55),listOf(1.0,.98)) }
        val tones=listOf(listOf(0.0,0.0),listOf(.45,.62),listOf(1.0,1.0))
        for(recipe in listOf(emptyMap(),spectral)) {
            val film=kernel.develop(kernel.expose(RgbVolume(7,5,linearRgb=FloatArray(105) { .02f+(it%23)*.09f }),recipe),recipe)
            val rgb=film.opticalDensityRgb.values.copyOf();val pools=film.spectralComponentAmounts?.values?.copyOf()
            val scanner=(recipe["scanner"] as? Map<String,Any?>).orEmpty()+mapOf("scanner_response_matrix" to listOf(listOf(.8,.1,.05),listOf(.05,.9,.1),listOf(.1,.05,.8)),"device_response_rgb" to curve)
            val source=recipe+mapOf("observe_mode" to "scanner","scanner" to scanner)
            val prepared=kernel.prepareObservation(film,source)
            for(settings in listOf(emptyMap(),mapOf("scan_saturation" to 1.7,"tone_curve" to tones,"color_curve_rgb" to curve),mapOf("scan_normalize" to false,"invert_transmission" to false),
                mapOf("transmission_light_ev" to 4.0,"transmission_light_temperature_k" to 3200),
                mapOf("transmission_light_ev" to -5.0,"transmission_light_temperature_k" to 9000,"scanner_light_color" to listOf(.8,1.1,.5)))) {
                val changed=source+mapOf("scanner" to scanner+settings,"look" to mapOf("print_exposure_ev" to -.25))
                assertTrue(prepared.matches(changed))
                val actual=prepared.observe(changed);val full=kernel.observe(film,changed)
                assertArrayEquals(full.outputRgb.values,actual.outputRgb.values,0f);assertEquals(full.metadata,actual.metadata)
                assertArrayEquals(actual.outputRgb.values,prepared.observe(changed).outputRgb.values,0f)
            }
            for(setting in listOf("scan_grid_long_edge" to 4,"scanner_response_matrix" to List(3){List(3){.2}},"spectral_observation_profile" to emptyMap<String,Any?>()))
                assertFalse(prepared.matches(source+mapOf("scanner" to scanner+setting)))
            assertArrayEquals(rgb,film.opticalDensityRgb.values,0f)
            pools?.let { assertArrayEquals(it,film.spectralComponentAmounts!!.values,0f) }
        }
        val matrix=MutableList(3) { y->MutableList(3) { x->if(x==y) 1.0 else 0.0 } }
        val source=mapOf("scanner" to mapOf("scanner_response_matrix" to matrix))
        val film=OpticalMaster(FloatNd(intArrayOf(1,1,3),FloatArray(3)),emptyMap())
        val prepared=kernel.prepareObservation(film,source);matrix[0][0]=.5
        assertFalse(prepared.matches(source))
    }
    @Test fun retainedTransportRelightsBothViewerPolaritiesWithoutRescalingClippedSamples() {
        val kernel=PhoneReducedKernel()
        val original=FloatArray(5*7*3) { (it%37)*.18f }
        for(polarity in listOf("negative","positive")) for(mode in listOf("viewer_light_table","viewer_projection")) {
            val master=OpticalMaster(FloatNd(intArrayOf(5,7,3),original.copyOf()),mapOf("image_polarity" to polarity),floatArrayOf(.9f,.4f,.1f))
            val source=mapOf("observe_mode" to mode,"scanner" to mapOf("transmission_light_ev" to 5.0))
            val ready=kernel.prepareObservation(master,source)
            for(ev in listOf(-5.0,0.0,5.0)) {
                val changed=source+mapOf("scanner" to mapOf("transmission_light_ev" to ev,"transmission_light_temperature_k" to 3200))
                assertTrue(ready.matches(changed))
                assertArrayEquals(kernel.observe(master,changed).outputRgb.values,ready.observe(changed).outputRgb.values,0f)
            }
            assertArrayEquals(original,master.opticalDensityRgb.values,0f)
        }
        val master=OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap())
        val spatial=mapOf("observe_mode" to "viewer_light_table","viewer_illuminant" to "scene_camera",
            "scene_illuminant_rgb" to listOf(.5,.5,.5),"scene_illuminant_rgb_image" to FloatNd(intArrayOf(1,1,3),floatArrayOf(.2f,.4f,.8f)))
        val ready=kernel.prepareObservation(master,spatial)
        assertFalse(ready.matches(spatial+mapOf("scanner" to mapOf("transmission_light_ev" to 1.0))))
    }
}
