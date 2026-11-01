package com.filmfoundry.app.ui

import com.filmfoundry.interchange.*
import com.filmfoundry.workflow.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanHistogramOutputTest {
    @Test fun finalPreviewHistogramFollowsLightInterpretationStylesAndCurvesInsteadOfTheSourceFilm() = runBlocking {
        val w=43;val h=17
        val density=FloatArray(w*h*3) { .15f+(it%71)/40f };val original=density.copyOf()
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(h,w,3),density),emptyMap(),floatArrayOf(.12f,.06f,.03f))
        val preview=OpticalPreview(archive,w,h,1)
        val observer=OpticalPreviewSession(retainPrepared=true)
        val scanner=mapOf<String,Any?>("invert_transmission" to false,"remove_base_mask" to false,
            "scan_normalize" to false,"scan_saturation" to 1.0,"print_mapping_mode" to "sigmoid")
        val base=mapOf(PhoneScanModels.OPTION to PhoneScanModels.CURRENT,"scanner" to scanner)
        suspend fun histogram(recipe: Map<String,Any?>): DisplayHistogram {
            val output=observer.observe(preview,recipe,false)
            val reference=OpticalPreviewSession().scan(archive,recipe)
            assertArrayEquals(reference.rgb,output.rgb,0f) // Reusing prepared readout must not freeze the edit.
            val builder=DisplayHistogram.Builder(output.width,output.height)
            for(i in output.rgb.indices step 3) builder.addEncodedRgb(output.rgb,i)
            return builder.finish()
        }
        val baseline=histogram(base)
        val variants=listOf(
            base+("scanner" to scanner+("transmission_light_ev" to -1.0)),
            base+("scanner" to scanner+("transmission_light_temperature_k" to 3000)),
            base+("scanner" to scanner+("invert_transmission" to true)),
            base+("scanner" to scanner+("remove_base_mask" to true)),
            base+("scanner" to scanner+("scan_saturation" to 0.0)),
            base+("look" to mapOf("print_exposure_ev" to .6,"print_contrast" to 1.2)),
            base+("scanner" to scanner+("tone_curve" to listOf(listOf(0,0),listOf(.5,.7),listOf(1,1))))
        )
        for(recipe in variants) {
            val edited=histogram(recipe)
            assertTrue((0..3).any { c -> (0..255).any { bin -> edited.count(c,bin)!=baseline.count(c,bin) } })
            for(c in 0..3) assertEquals(w*h,(0..255).sumOf { edited.count(c,it) })
        }
        // A final RGB curve forces every visible component to 0.75, regardless of source density.
        val fixed=histogram(base+("scanner" to scanner+("color_curve_rgb" to List(3) { listOf(listOf(0,.75),listOf(1,.75)) })))
        for(c in 0..3) { assertEquals(w*h,fixed.count(c,191));assertEquals(0,fixed.count(c,0));assertEquals(0,fixed.count(c,255)) }
        assertArrayEquals(original,density,0f)
    }
}
