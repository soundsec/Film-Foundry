package com.filmfoundry.app.ui

import com.filmfoundry.interchange.StillFrameFormat
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FrameGateProjectionTest {
    @Test fun finalIntegerApertureAndPanoramaAreProjectedInsideTheWholeSource() {
        for(format in listOf("135","half","6x6")) {
            val source=StillFrameFormat.create(format,false,4000,2250)
            val gate=FrameGateProjection.project(format,false,4000,2250,1600,1000)
            assertEquals(50f+source.top*.4f,gate.film.top)
            assertEquals(source.left*.4f,gate.film.left)
            assertEquals(gate.film,gate.exposed)
        }
        val normal=FrameGateProjection.project("135",false,4000,2250,1600,1000)
        val wide=FrameGateProjection.project("135",true,4000,2250,1600,1000)
        assertEquals(normal.film,wide.film)
        assertEquals(wide.film.left,wide.exposed.left);assertEquals(wide.film.right,wide.exposed.right)
        assertTrue(wide.exposed.top>wide.film.top);assertTrue(wide.exposed.bottom<wide.film.bottom)
        val half=FrameGateProjection.project("half",false,4000,3000,1600,1200)
        assertEquals(350f,half.film.left);assertEquals(0f,half.film.top)
        assertEquals(1250f,half.film.right);assertEquals(1200f,half.film.bottom)
        val old=FrameGateProjection.project("half",false,4000,3000,1600,1200,StillFrameFormat.V3_MODEL)
        assertTrue(old.film.top>half.film.top);assertTrue(old.film.left>half.film.left)
    }
}
