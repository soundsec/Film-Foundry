package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PiecewiseCurveTest {
    @Test fun pointEditsAreDetachedAndPreserveOrderedInputsAndEndpoints() {
        val original=PiecewiseCurve.identity();val inserted=original.insert(.4,.6)
        assertEquals(2,original.points.size);assertEquals(3,inserted.points.size)
        val moved=inserted.move(1,.8,.3)
        assertEquals(.4,inserted.points[1].x);assertEquals(.8,moved.points[1].x)
        assertEquals(0.0,moved.move(0,.2,.1).points.first().x)
        assertEquals(2,moved.remove(1).points.size)
        assertThrows(IllegalArgumentException::class.java) { moved.remove(0) }
        assertThrows(IllegalArgumentException::class.java) { moved.insert(.8,.2) }
    }
    @Test fun badOrNonFiniteCurvesAreRejectedAndLegacyUnsortedRgbCanBeSorted() {
        assertThrows(IllegalArgumentException::class.java) { PiecewiseCurve.parse(listOf(listOf(0.0,0.0),listOf(0.0,1.0))) }
        assertThrows(IllegalArgumentException::class.java) { PiecewiseCurve.from(listOf(CurvePoint(0.0,0.0),CurvePoint(1.0,Double.NaN))) }
        val value=PiecewiseCurve.parse(listOf(listOf(1.0,1.2),listOf(0.0,-.1)),sort=true)
        assertEquals(-.1,value.points.first().y);assertEquals(1.0,value.points.last().x)
        assertThrows(IllegalArgumentException::class.java) { RecipeJson.parse("{} garbage") }
    }
}
