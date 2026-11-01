package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer

class ViewerPlaneScratchTest {
    @Test fun copiesVisiblePlaneBytesWithoutMovingInputAndReusesOnlyOwnedMatchingBuffers() {
        val source=byteArrayOf(99,98,1,2,3,4,97)
        val buffer=ByteBuffer.wrap(source).apply { position(2);limit(6) }
        val first=ViewerPlaneScratch();val second=ViewerPlaneScratch()
        val a=first.copy(0,buffer);assertArrayEquals(byteArrayOf(1,2,3,4),a)
        assertEquals(2,buffer.position());assertEquals(6,buffer.limit())
        source[2]=7;assertSame(a,first.copy(0,buffer));assertEquals(7,a[0].toInt())
        assertNotSame(a,first.copy(1,buffer));assertNotSame(a,second.copy(0,buffer))
        buffer.limit(5);val resized=first.copy(0,buffer)
        assertNotSame(a,resized);assertArrayEquals(byteArrayOf(7,2,3),resized)
    }
}
