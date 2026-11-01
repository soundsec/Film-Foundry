package com.filmfoundry.workflow

import com.filmfoundry.interchange.StillFrameFormat
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class StillResolutionPlanTest {
    @Test fun lensSwitchFollowsMaximumButRetainsSmallSupportedOutputs() {
        val original=listOf(8000 to 6000,4000 to 3000,1280 to 720)
        val alternate=listOf(3264 to 2448,1920 to 1080,1280 to 720,640 to 480)
        assertTrue(StillResolutionPlan.isMaximum(6000 to 8000,original,"135"))
        assertFalse(StillResolutionPlan.isMaximum(1280 to 720,original,"135"))
        assertEquals(3264 to 2448,StillResolutionPlan.forLens(8000 to 6000,alternate,"135",true))
        assertEquals(1280 to 720,StillResolutionPlan.forLens(1280 to 720,alternate,"135",false))
        assertEquals(1280 to 720,StillResolutionPlan.forLens(1600 to 900,alternate,"135",false))
        assertEquals(8000 to 6000,StillResolutionPlan.forLens(3264 to 2448,original,"135",true))
        assertNull(StillResolutionPlan.forLens(null,alternate,"135",false))
    }

    @Test fun openMultipleExposureLocksGeometryAndCannotSilentlyChangeLensSize() {
        val sizes=listOf(4000 to 3000,1280 to 720)
        assertEquals(1280 to 720,StillResolutionPlan.forLens(4000 to 3000,sizes,"135",true,1280 to 720))
        assertThrows(IllegalStateException::class.java) {
            StillResolutionPlan.forLens(4000 to 3000,listOf(1920 to 1080),"135",true,1280 to 720)
        }
        assertThrows(IllegalArgumentException::class.java) {
            StillResolutionPlan.forLens(4000 to 3000,emptyList(),"135",true)
        }
    }

    @Test fun recommendationUsesUsefulPixelsAndDoesNotLoseCandidatesAfterEightEntries() {
        val sizes=(1..9).map { 5000+it*100 to 1800 }+listOf(4000 to 3000,4000 to 3000,0 to 3000)
        val ranked=StillResolutionPlan.ranked(sizes,"135")
        assertEquals(10,ranked.size)
        assertEquals(4000 to 3000,ranked.first().source)
        assertEquals(4000,ranked.first().gate.width)
        assertEquals(2666,ranked.first().gate.height)
        assertEquals(10_664_000L,ranked.first().imagePixels)
        assertEquals(7_593_750L,StillResolutionPlan.estimate(4000 to 2250,"135").imagePixels)
        assertTrue(ranked.all { it.imagePixels<=it.sourcePixels && it.source in sizes })
    }

    @Test fun formatChangesRecommendationWithoutChangingTheSourceOrLegacyGeometry() {
        val sizes=listOf(5000 to 3000,4000 to 3500)
        assertEquals(4000 to 3500,StillResolutionPlan.ranked(sizes,"6x6").first().source)
        assertEquals(5000 to 3000,StillResolutionPlan.ranked(sizes,"6x9").first().source)
        val half=StillResolutionPlan.estimate(4000 to 3000,"half")
        assertEquals(2250 to 3000,half.gate.width to half.gate.height)
        val old=StillResolutionPlan.estimate(4000 to 3000,"half",model=StillFrameFormat.LEGACY_MODEL)
        assertEquals(4000 to 3000,old.gate.width to old.gate.height)
        assertTrue(StillResolutionPlan.ranked(emptyList(),"135").isEmpty())
    }

    @Test fun panoramaReportsUnexposedBandsSeparatelyFromStoredFilmPixels() {
        val normal=StillResolutionPlan.estimate(4000 to 3000,"135")
        val wide=StillResolutionPlan.estimate(4000 to 3000,"135",panorama=true)
        assertEquals(normal.imagePixels,wide.imagePixels)
        assertEquals(4000 to 2666,wide.gate.width to wide.gate.height)
        assertEquals(5_776_000L,wide.exposedPixels) // Current 36×13 mm baffles.
        assertEquals(6_664_000L,StillResolutionPlan.estimate(4000 to 3000,"135",true,StillFrameFormat.V4_MODEL).exposedPixels)
        assertTrue(wide.exposedPixels<wide.imagePixels)
    }
}
