package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ArgbRowConverterTest {
    private fun color(i: Int): Int = ((i * 29 and 255) shl 24) or
        ((i * 17 and 255) shl 16) or ((i * 7 and 255) shl 8) or (i * 3 and 255)

    @Test fun stripsReuseBoundedScratchAndPreserveEveryChannelIncludingTail() {
        val width = 7; val height = 5001
        var buffer: IntArray? = null
        var next = 0
        var calls = 0
        val actual = ArgbRowConverter.convert(width, height) { pixels, start, rows ->
            if (buffer == null) buffer = pixels else assertSame(buffer, pixels)
            assertTrue(pixels.size <= 16384)
            assertEquals(next, start)
            for (i in 0 until rows * width) pixels[i] = color(start * width + i)
            next += rows; calls++
        }
        assertEquals(height, next)
        assertEquals(3, calls)
        for (i in 0 until width * height) {
            val packed = color(i)
            assertEquals(((packed ushr 16) and 255) / 255f, actual[i * 3])
            assertEquals(((packed ushr 8) and 255) / 255f, actual[i * 3 + 1])
            assertEquals((packed and 255) / 255f, actual[i * 3 + 2])
        }
    }

    @Test fun veryWideImagesUseAtLeastOneCompleteRow() {
        val width = 20001
        var calls = 0
        val rgb = ArgbRowConverter.convert(width, 2) { pixels, start, rows ->
            assertEquals(width, pixels.size)
            assertEquals(1, rows)
            assertEquals(calls++, start)
            pixels.fill(0xff123456.toInt())
        }
        assertEquals(2, calls)
        assertEquals(width * 2 * 3, rgb.size)
        assertEquals(0x12 / 255f, rgb[0])
        assertEquals(0x56 / 255f, rgb.last())
    }
}
