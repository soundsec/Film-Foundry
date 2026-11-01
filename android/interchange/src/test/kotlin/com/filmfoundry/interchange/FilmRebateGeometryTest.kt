package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FilmRebateGeometryTest {
    @Test fun perforationsAreRoundedRectanglesAnd120HasNone() {
        val g=FilmRebateGeometry.layout("135",800,240)
        assertTrue(g.hole(50,20));assertTrue(g.hole(26,20))
        assertFalse(g.hole(26,7),"inside bounding rectangle but outside rounded corner")
        assertFalse(g.hole(25,20));assertFalse(g.hole(50,0))
        assertTrue(g.holeBottom<g.textTop)
        val medium=FilmRebateGeometry.layout("120",800,240)
        assertEquals(0,medium.holesPerRow);assertFalse(medium.hole(50,10));assertEquals(.12,medium.textTop)
    }
}
