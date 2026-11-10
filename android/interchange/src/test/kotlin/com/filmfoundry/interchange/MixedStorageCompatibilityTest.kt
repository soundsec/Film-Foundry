package com.filmfoundry.interchange

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.file.Path
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class MixedStorageCompatibilityTest {
    @TempDir lateinit var root: Path
    private fun numeric(descriptor: String,payload: ByteArray,count: Int): ByteArray {
        val header=NpyCodec.floatHeader(intArrayOf(count))
        val text=String(header,Charsets.ISO_8859_1).replace("<f4",descriptor)
        return text.toByteArray(Charsets.ISO_8859_1)+payload
    }
    private fun halves(bits: IntArray): ByteArray = ByteBuffer.allocate(bits.size*2).order(ByteOrder.LITTLE_ENDIAN).apply {
        bits.forEach { putShort(it.toShort()) }
    }.array()

    @Test fun halfNormalsSubnormalsAndSignedZeroDecodeAcrossChunks() {
        val pattern=intArrayOf(0,0x8000,1,0x8001,0x03ff,0x0400,0x3c00,0xc000,0x7bff)
        val expected=floatArrayOf(0f,-0f,1f/16777216f,-1f/16777216f,1023f/16777216f,1f/16384f,1f,-2f,65504f)
        val bits=IntArray(20003) { pattern[it%pattern.size] }
        val decoded=(NpyCodec.decode(numeric("<f2",halves(bits),bits.size)) as NpyValue.Float32).array.values
        for(i in decoded.indices) assertEquals(expected[i%expected.size].toRawBits(),decoded[i].toRawBits())
    }

    @Test fun unsignedBytesKeepTheirValuesAndExpandedMemoryCountsAgainstBudget() {
        val bytes=numeric("|u1",byteArrayOf(0,1,127,128.toByte(),255.toByte()),5)
        assertArrayEquals(floatArrayOf(0f,1f,127f,128f,255f),(NpyCodec.decode(bytes) as NpyValue.Float32).array.values)
        assertThrows(NpyException::class.java) { NpyCodec.readValue(bytes.inputStream(),bytes.size.toLong(),true,maxRetainedBytes=19) }
    }

    @Test fun evenUnretainedHalfArraysRejectNonfiniteOrTruncatedPayloads() {
        for(bits in intArrayOf(0x7c00,0xfc00,0x7e00)) {
            val bytes=numeric("<f2",halves(intArrayOf(bits)),1)
            assertThrows(NpyException::class.java) { NpyCodec.readValue(bytes.inputStream(),bytes.size.toLong(),false) }
        }
        val bytes=numeric("<f2",halves(intArrayOf(0x3c00)),1)
        assertThrows(NpyException::class.java) { NpyCodec.decode(bytes.copyOf(bytes.size-1)) }
    }

    @Test fun actualDesktopMixedArchiveOpensThroughMemoryInspectionAndRowPaths() {
        val codec=NpzMediumArchive()
        val bytes=javaClass.getResourceAsStream("/desktop_mixed_storage_sample.npz")!!.use { it.readBytes() }
        val medium=codec.readDevelopedOptical(bytes)
        val shape=medium.opticalDensityRgb.shape
        val info=codec.inspect(bytes.inputStream(),false)
        assertEquals(shape[1],info.width);assertEquals(shape[0],info.height)
        RowArchiveSpool.readOptical(bytes.inputStream(),root.toFile()).use { rows ->
            val actual=rows.opticalRows(0,rows.height)
            assertArrayEquals(medium.opticalDensityRgb.values,actual.opticalDensityRgb.values)
        }
        assertEquals(0,root.toFile().listFiles()!!.size)
    }
}
