package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class RollDirectReferenceTest {
    private fun rgb(r: Int,g: Int,b: Int)=(r shl 16) or (g shl 8) or b
    private fun flat(r: Int,g: Int,b: Int)=RollDirectReference.fromSrgb(IntArray(16){rgb(r,g,b)},4,4)
    private fun ev(reference: Map<String,Any?>)=RollDirectReference.recommend(
        mapOf("scan_saturation" to 1.18,"scan_black_percentile" to .25),emptyMap(),reference,false,false).second["print_exposure_ev"] as Double

    @Test fun displayStatsShiftRecommendedExposureAndColorWithoutMovingFilmPercentiles() {
        val gray=flat(115,115,115)
        assertEquals(RollDirectReference.MODEL,gray["model"])
        assertTrue(kotlin.math.abs(ev(gray))<.05)
        assertTrue(ev(flat(20,20,20))<-.2)
        assertTrue(ev(flat(230,230,230))>.2)
        val neutral=RollDirectReference.recommend(mapOf("scan_saturation" to 1.18,"scan_black_percentile" to .25),emptyMap(),gray,false,false)
        val vivid=RollDirectReference.recommend(mapOf("scan_saturation" to 1.18,"scan_black_percentile" to .25),emptyMap(),flat(220,20,20),false,false)
        assertEquals(.25,neutral.first["scan_black_percentile"])
        assertTrue((vivid.first["scan_saturation"] as Double)>(neutral.first["scan_saturation"] as Double))
        val locked=RollDirectReference.recommend(mapOf("scan_saturation" to 1.18),mapOf("print_exposure_ev" to 1.5),flat(20,20,20),true,true)
        assertEquals(1.5,locked.second["print_exposure_ev"])
        assertEquals(1.18,locked.first["scan_saturation"])
        assertEquals(emptyMap<String,Any?>() to emptyMap<String,Any?>(),RollDirectReference.recommend(emptyMap(),emptyMap(),mapOf("model" to "other"),false,false))
        assertThrows(IllegalArgumentException::class.java) { RollDirectReference.fromSrgb(IntArray(3),2,2) }
        assertEquals(listOf("roll/direct_reference.jpg","roll/direct_reference.json"),RollDirectReference.relativePaths("roll"))
    }
}
