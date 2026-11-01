package com.filmfoundry.kernel

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ObservationEncodingTest {
    @Test fun fusedPointwiseEncodingIsBitExactForCurvesExposureAndAllViewerBranches() {
        val input=FloatArray(997*3) { i -> when(i%9) { 0->-.1f;1->0f;2->.0031308f;3->1f;4->4f;else->(i%317)/316f } }
        val curve=listOf(listOf(0.0,.01),listOf(.19,.12),listOf(.55,.63),listOf(1.0,.98))
        for(ev in listOf(-2.0,0.0,1.3)) for(contrast in listOf(.5,1.0,1.7)) for(curved in listOf(false,true)) for(viewer in listOf(false,true)) {
            val recipe=mapOf("look" to mapOf("print_exposure_ev" to ev,"print_contrast" to contrast),"scanner" to
                if(curved) mapOf("color_curve_rgb" to List(3){curve},"tone_curve" to curve) else emptyMap())
            val actual=input.copyOf();val reference=input.copyOf()
            PhoneObserve.encodeRows(actual,recipe,viewer);PhoneObserve.encodeRowsReference(reference,recipe,viewer)
            assertArrayEquals(reference,actual,0f)
        }
    }
    @Test fun contrastExpandsAroundHalfWhiteBeforeCurvesAndNeverChangesViewing() {
        val input=floatArrayOf(.2f,.5f,.8f)
        val recipe=mapOf("look" to mapOf("print_contrast" to 1.5))
        val actual=input.copyOf();PhoneObserve.encodeRows(actual,recipe)
        val expected=floatArrayOf(.05f,.5f,.95f);PhoneObserve.encodeRows(expected,emptyMap())
        assertArrayEquals(expected,actual,1e-6f)
        val viewer=input.copyOf();PhoneObserve.encodeRows(viewer,recipe,true)
        val unchanged=input.copyOf();PhoneObserve.encodeRows(unchanged,emptyMap(),true)
        assertArrayEquals(unchanged,viewer,0f)
        assertArrayEquals(floatArrayOf(.2f,.5f,.8f),input,0f)
    }
}
