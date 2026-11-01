package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test
import java.io.ByteArrayInputStream

class JpegSizeTest {
    @Test
    fun readsSofSizeAndIgnoresOtherMarkers() {
        val jpeg = byteArrayOf(
            0xFF.toByte(), 0xD8.toByte(),
            0xFF.toByte(), 0xE0.toByte(), 0x00, 0x04, 0x00, 0x00,
            0xFF.toByte(), 0xC0.toByte(), 0x00, 0x07, 0x08, 0x01, 0x00, 0x01, 0x40,
        )
        assertEquals(320 to 256, JpegSize.read(ByteArrayInputStream(jpeg)))
    }
}
