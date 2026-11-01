package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhotoDisplayPolicyTest {
    @Test fun nativePhotoRemainsFullSizeWhenBudgetAllowsAndHugePhotosStayBounded() {
        assertEquals(1,PhotoDisplayPolicy.sample(3375,2250,128L*1024*1024))
        assertEquals(1,PhotoDisplayPolicy.sample(4000,3000,192L*1024*1024))
        assertEquals(2,PhotoDisplayPolicy.sampleForEdge(3375,2250,1200))
        for((w,h) in listOf(20000 to 15000,3375 to 2250,2250 to 3375)) {
            val available=24L*1024*1024;val s=PhotoDisplayPolicy.sample(w,h,available)
            val pixels=((w.toLong()+s-1)/s)*((h.toLong()+s-1)/s)
            assertTrue(pixels*4<=available/3)
            assertEquals(0,s and (s-1))
        }
        assertTrue(PhotoDisplayPolicy.sample(Int.MAX_VALUE,Int.MAX_VALUE,0)>1)
    }
    @Test fun zoomedDragPansUntilEdgeAndOnlyAnIntentionalOverflowCanTurnPage() {
        val inside=PhotoDisplayPolicy.pan(0f,0f,-80f,30f,100f,50f)
        assertEquals(PhotoDisplayPolicy.Pan(-80f,30f,0f),inside)
        val edge=PhotoDisplayPolicy.pan(inside.x,inside.y,-60f,50f,100f,50f)
        assertEquals(PhotoDisplayPolicy.Pan(-100f,50f,-40f),edge)
        assertEquals(0f,PhotoDisplayPolicy.pan(-100f,0f,20f,0f,100f,50f).overflowX)
        assertFalse(PhotoDisplayPolicy.commitPage(-10f,-1200f,400,true,8f))
        assertFalse(PhotoDisplayPolicy.commitPage(-50f,1200f,400,true,8f))
        assertTrue(PhotoDisplayPolicy.commitPage(-50f,-1200f,400,true,8f))
        assertTrue(PhotoDisplayPolicy.commitPage(90f,0f,400,true,8f))
    }
}
