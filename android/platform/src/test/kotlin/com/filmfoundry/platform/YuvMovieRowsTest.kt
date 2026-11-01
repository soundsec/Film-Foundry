package com.filmfoundry.platform

import java.nio.ByteBuffer
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class YuvMovieRowsTest {
    @Test fun paddedInterleavedYuvCropRotationAndRowsAgreeWithPhysicalSamples() {
        val y=ByteArray(8*6){16}
        for(row in 0 until 6) for(col in 0 until 6) y[row*8+col]=(20+row*20+col*5).toByte()
        val uv=ByteArray(8*3){0}
        for(row in 0 until 3) for(col in 0 until 3) uv[row*8+col*2]=(120+row*3+col*2).toByte()
        val yp=MovieYuvPlane(ByteBuffer.wrap(y),8,1)
        val up=MovieYuvPlane(ByteBuffer.wrap(uv),8,2)
        val vp=MovieYuvPlane(ByteBuffer.wrap(uv),8,2)
        val source=YuvMovieRows(4,2,2,2,yp,up,vp,0,false).readRows(0,2)
        for(rotation in listOf(0,90,180,270)) {
            val rows=YuvMovieRows(4,2,2,2,yp,up,vp,rotation,false)
            val all=rows.readRows(0,rows.height)
            for(row in 0 until rows.height) assertArrayEquals(all.copyOfRange(row*rows.width*3,(row+1)*rows.width*3),rows.readRows(row,1),0f)
            assertTrue(all.all { it.isFinite() && it in 0f..1f })
            val first=when(rotation){90->source[4*3];180->source[7*3];270->source[3*3];else->source[0]}
            assertEquals(first,all[0],0f)
        }
        assertEquals(0,yp.buffer.position())
        val white=MovieYuvPlane(ByteBuffer.wrap(ByteArray(4){235.toByte()}),2,1)
        val neutral=MovieYuvPlane(ByteBuffer.wrap(byteArrayOf(128.toByte())),1,1)
        assertTrue(YuvMovieRows(2,2,0,0,white,neutral,neutral,0,false).readRows(0,2).all { kotlin.math.abs(it-1f) < 1e-6f })
        val fullWhite=MovieYuvPlane(ByteBuffer.wrap(ByteArray(4){255.toByte()}),2,1)
        assertTrue(YuvMovieRows(2,2,0,0,fullWhite,neutral,neutral,0,true).readRows(0,2).all { kotlin.math.abs(it-1f) < 1e-6f })
        assertThrows(CaptureRejected::class.java) {
            YuvMovieRows(2,2,0,0,MovieYuvPlane(ByteBuffer.wrap(byteArrayOf(16)),2,1),neutral,neutral,0,false).readRows(0,2)
        }
    }
}
