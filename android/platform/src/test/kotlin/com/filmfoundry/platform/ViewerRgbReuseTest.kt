package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ViewerRgbReuseTest {
    @Test fun synchronousViewerCanBorrowRgbStorageWhileDefaultConversionKeepsItsOwnFrame() {
        val yuv=Yuv420888(width=4,height=2,y=byteArrayOf(16,48,96,127,0,0,30,80,120,-30,0,0),
            u=byteArrayOf(64,0,-32,0),v=byteArrayOf(-10,0,78,0),yRowStride=6,uRowStride=4,vRowStride=4,uPixelStride=2,vPixelStride=2)
        val original=yuv.y.copyOf();val own=Yuv420.toEncodedRgb(yuv);val before=own.copyOf();val buffer=FloatArray(24)
        assertSame(buffer,Yuv420.toEncodedRgb(yuv,buffer));assertArrayEquals(before,buffer,0f)
        val changed=yuv.copy(y=ByteArray(12) { 16 })
        Yuv420.toEncodedRgb(changed,buffer)
        assertArrayEquals(before,own,0f);assertArrayEquals(original,yuv.y)
        assertThrows(IllegalArgumentException::class.java) { Yuv420.toEncodedRgb(yuv,FloatArray(3)) }
    }
}
