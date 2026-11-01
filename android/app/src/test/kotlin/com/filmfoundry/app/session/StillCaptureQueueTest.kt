package com.filmfoundry.app.session

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class StillCaptureQueueTest {
    @Test fun activeShotReservesCapacityAndRollTailAndRequestsKeepTheirOrder() {
        val queue=StillCaptureQueue<Int>()
        assertTrue(queue.offer(1,2,true));assertEquals(1,queue.take())
        assertTrue(queue.offer(2,2,true));assertFalse(queue.offer(3,2,true))
        queue.complete();assertEquals(2,queue.take());queue.complete();assertNull(queue.take())
        repeat(3) { assertTrue(queue.offer(it,12,true)) }
        assertFalse(queue.offer(4,12,true));assertEquals(0,queue.take())
        assertFalse(queue.offer(5,12,true));assertEquals(3,queue.outstanding())
    }
    @Test fun manualAndMultipleExposuresCannotBeQueuedBehindAnUnfinishedShutter() {
        val queue=StillCaptureQueue<Int>()
        assertTrue(queue.offer(1,12,false));assertFalse(queue.offer(2,12,false))
        assertEquals(1,queue.take());assertFalse(queue.offer(2,12,false))
        assertEquals(0,queue.discardWaiting());assertEquals(1,queue.outstanding())
        queue.complete();assertTrue(queue.offer(2,12,false))
        assertEquals(1,queue.discardWaiting());assertEquals(0,queue.outstanding())
    }
}
