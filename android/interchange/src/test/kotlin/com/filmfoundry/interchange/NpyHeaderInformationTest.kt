package com.filmfoundry.interchange

import java.io.ByteArrayInputStream
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class NpyHeaderInformationTest {
    @Test fun headerInspectionStopsBeforePayloadAndFullReaderKeepsItsValidation() {
        val values=FloatNd(intArrayOf(2,3,3),FloatArray(18){it*.1f})
        val bytes=NpyCodec.encode(NpyValue.Float32(values))
        val first=ByteArrayInputStream(bytes);val header=NpyCodec.readHeader(first,bytes.size.toLong())
        assertArrayEquals(intArrayOf(2,3,3),header.shape)
        assertEquals(bytes.size-header.headerBytes.toInt(),first.available())
        val broken=bytes.copyOf(header.headerBytes.toInt())
        assertArrayEquals(header.shape,NpyCodec.readHeader(broken.inputStream(),bytes.size.toLong()).shape)
        assertThrows(NpyException::class.java) { NpyCodec.decode(broken) }
        assertArrayEquals(values.values,(NpyCodec.decode(bytes) as NpyValue.Float32).array.values,0f)
        assertEquals("Hello",(NpyCodec.decode(NpyCodec.encode(NpyValue.UnicodeScalar("Hello"))) as NpyValue.UnicodeScalar).text)
    }
}
