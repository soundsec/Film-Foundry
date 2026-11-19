package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FinderArcGeometryTest {
    @Test fun exposedArcFollowsItsRailAndTransparentAreaLeavesNativePreviewTouchable() {
        val side=FinderArcGeometry.place(720f,360f,48f,98f,88f,false,1f)
        assertTrue(side.y>=98f);assertTrue(side.x<88f)
        assertTrue(side.contains(side.x+side.radius,side.y))
        assertFalse(side.contains(87f,98f,10f)) // Active rail retains its own short tap.
        assertFalse(side.contains(360f,160f)) // Main preview still receives native AF / AE.
        assertFalse(side.contains(105f,300f)) // Empty part of the overlay is not an input shield.
        assertTrue(side.y-side.radius*.96f>=7.99f)
        for (anchor in listOf(20f, 334f)) {
            val edge=FinderArcGeometry.place(720f,360f,48f,anchor,88f,false,1f)
            assertEquals(112f,edge.radius)
            assertTrue(edge.y-edge.radius*.96f >= 7.99f)
            assertTrue(edge.y+edge.radius*.96f <= 294.01f)
        }
        val bottom=FinderArcGeometry.place(720f,360f,404f,330f,306f,true,1f)
        assertEquals(404f,bottom.x);assertTrue(bottom.y>306f)
        assertTrue(bottom.contains(404f,bottom.y-bottom.radius))
        assertFalse(bottom.contains(404f,307f,10f))
        assertFalse(bottom.contains(150f,180f))
    }
}
