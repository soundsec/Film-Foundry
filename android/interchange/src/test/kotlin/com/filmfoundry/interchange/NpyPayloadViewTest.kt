package com.filmfoundry.interchange

import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class NpyPayloadViewTest {
    private fun npy(descr: String, shape: String, payload: ByteArray, version: Int = 1): ByteArray {
        val header = "{'descr': '$descr', 'fortran_order': False, 'shape': $shape, }\n".toByteArray()
        val prefix = ByteBuffer.allocate(if (version == 1) 10 else 12).order(ByteOrder.LITTLE_ENDIAN)
        prefix.put(byteArrayOf(0x93.toByte(), 78, 85, 77, 80, 89, version.toByte(), 0))
        if (version == 1) prefix.putShort(header.size.toShort()) else prefix.putInt(header.size)
        return prefix.array() + header + payload
    }

    @Test fun numericViewsPreserveVersionsPrecisionAndIndependentOwnership() {
        for (version in 1..3) for (size in listOf(4, 8)) {
            val payload = ByteBuffer.allocate(size * 3).order(ByteOrder.LITTLE_ENDIAN)
            for (v in listOf(-.125, .5, 123.0)) {
                if (size == 4) payload.putFloat(v.toFloat()) else payload.putDouble(v)
            }
            val bytes = npy("<f$size", "(1, 1, 3)", payload.array(), version)
            val before = bytes.copyOf()
            val decoded = (NpyCodec.decode(bytes) as NpyValue.Float32).array
            assertArrayEquals(floatArrayOf(-.125f, .5f, 123f), decoded.values, 0f)
            decoded.values[0] = 9f
            assertArrayEquals(before, bytes)
            bytes.fill(0)
            assertEquals(.5f, decoded.values[1])
        }
    }

    @Test fun scalarStringsHonorNonzeroOffsetEndianAndPadding() {
        for (order in listOf(ByteOrder.LITTLE_ENDIAN, ByteOrder.BIG_ENDIAN)) {
            val payload = ByteBuffer.allocate(16).order(order)
            for (code in intArrayOf(0x6750, 0x6599, 0x1f39e, 0)) payload.putInt(code)
            val descr = if (order == ByteOrder.LITTLE_ENDIAN) "<U4" else ">U4"
            assertEquals("材料🎞", (NpyCodec.decode(npy(descr, "()", payload.array())) as NpyValue.UnicodeScalar).text)
        }
        assertEquals("hello", (NpyCodec.decode(npy("|S7", "()", byteArrayOf(104, 101, 108, 108, 111, 0, 0))) as NpyValue.UnicodeScalar).text)
    }

    @Test fun truncatedViewsAndNonfiniteNumbersStillFail() {
        for ((descr, shape, payload) in listOf(
            Triple("<f4", "(1,)", ByteArray(3)),
            Triple("<f8", "(1,)", ByteArray(7)),
            Triple("<U1", "()", ByteArray(3)),
            Triple("|S2", "()", ByteArray(1)),
            Triple("<f4", "(1,)", ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putFloat(Float.NaN).array()),
        )) assertThrows(NpyException::class.java) { NpyCodec.decode(npy(descr, shape, payload)) }
    }
}
