package com.filmfoundry.interchange

import java.io.*
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipException
import java.util.zip.ZipOutputStream
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class StreamingArchiveTest {
    @Test fun finiteBoundariesKeepExactBitsAndRejectNonfiniteOnEveryCodecPath() {
        val values=floatArrayOf(-Float.MAX_VALUE,-java.lang.Float.MIN_NORMAL,-Float.MIN_VALUE,-0f,
            0f,Float.MIN_VALUE,java.lang.Float.MIN_NORMAL,Float.MAX_VALUE)
        val encoded=NpyCodec.encode(NpyValue.Float32(FloatNd(intArrayOf(values.size),values)))
        val actual=(NpyCodec.decode(encoded) as NpyValue.Float32).array.values
        assertArrayEquals(values.map(Float::toRawBits).toIntArray(),actual.map(Float::toRawBits).toIntArray())
        for(bits in listOf(0x7f800000,0xff800000.toInt(),0x7fc00000,0xffc00001.toInt(),0x7f800001)) {
            val invalid=Float.fromBits(bits)
            assertThrows(NpyException::class.java) {
                NpyCodec.encode(NpyValue.Float32(FloatNd(intArrayOf(1),floatArrayOf(invalid))))
            }
            val corrupt=encoded.copyOf()
            java.nio.ByteBuffer.wrap(corrupt).order(java.nio.ByteOrder.LITTLE_ENDIAN).putInt(corrupt.size-4,bits)
            for(retain in listOf(true,false)) assertThrows(NpyException::class.java) {
                NpyCodec.readValue(corrupt.inputStream(),corrupt.size.toLong(),retain)
            }
        }
        // Desktop float64 input is narrowed before the same finite guard; overflow stays invalid.
        val header=String(NpyCodec.floatHeader(intArrayOf(1)),Charsets.ISO_8859_1)
            .replace("<f4","<f8").toByteArray(Charsets.ISO_8859_1)
        for(value in listOf(Double.MAX_VALUE,-Double.MAX_VALUE,Double.NaN,Double.POSITIVE_INFINITY,Double.NEGATIVE_INFINITY)) {
            val payload=header+java.nio.ByteBuffer.allocate(8).order(java.nio.ByteOrder.LITTLE_ENDIAN).putDouble(value).array()
            assertThrows(NpyException::class.java) { NpyCodec.decode(payload) }
        }
    }

    private class Fragmented(bytes: ByteArray) : ByteArrayInputStream(bytes) {
        var closed = false
        override fun read(bytes: ByteArray, offset: Int, length: Int): Int = super.read(bytes, offset, minOf(7, length))
        override fun close() { closed = true }
    }
    private class TrackedOutput : ByteArrayOutputStream() {
        var closed = false
        var largestWrite = 0
        override fun write(bytes: ByteArray, offset: Int, length: Int) {
            largestWrite = maxOf(largestWrite, length)
            super.write(bytes, offset, length)
        }
        override fun close() { closed = true }
    }

    @Test fun fragmentedNpyAndZipKeepValuesBoundedWritesAndCallerOwnership() {
        val values = FloatArray(8197 * 3) { (it % 97) / 97f }
        val array = NpyValue.Float32(FloatNd(intArrayOf(1, 8197, 3), values))
        val before = values.copyOf()
        val npy = TrackedOutput()
        NpyCodec.write(array, npy)
        assertTrue(npy.largestWrite <= 65536)
        assertFalse(npy.closed)
        val input = Fragmented(npy.toByteArray())
        assertArrayEquals(values, (NpyCodec.read(input) as NpyValue.Float32).array.values, 0f)
        assertFalse(input.closed)
        val output = TrackedOutput()
        NpzFile.write(mapOf("optical_density_rgb" to array, "metadata" to NpyValue.UnicodeScalar("材料🎞")), output)
        assertFalse(output.closed)
        val zipInput = Fragmented(output.toByteArray())
        val decoded = NpzFile.read(zipInput)
        assertFalse(zipInput.closed)
        assertArrayEquals(values, (decoded.getValue("optical_density_rgb") as NpyValue.Float32).array.values, 0f)
        assertEquals("材料🎞", (decoded.getValue("metadata") as NpyValue.UnicodeScalar).text)
        assertArrayEquals(before, values, 0f)
    }

    @Test fun typedStreamPathsKeepDesktopFixturesAndConsumedRules() {
        val archive = NpzMediumArchive()
        val fixture = javaClass.getResourceAsStream("/phone_latent_python_sample.npz")!!.use { it.readBytes() }
        val latent = archive.readLatent(Fragmented(fixture))
        val output = TrackedOutput()
        archive.writeLatent(latent, output)
        assertFalse(output.closed)
        val restored = archive.readLatent(Fragmented(output.toByteArray()))
        for (key in latent.arrays.keys) assertArrayEquals(latent.arrays.getValue(key).values, restored.arrays.getValue(key).values, 0f)
        val consumed = ByteArrayOutputStream()
        archive.writeLatent(latent.copy(metadata = latent.metadata + ("consumed" to true)), consumed)
        assertThrows(InterchangeException::class.java) { archive.readLatent(Fragmented(consumed.toByteArray())) }
        assertEquals(true, archive.readLatent(Fragmented(consumed.toByteArray()), true).metadata["consumed"])
        val optical = javaClass.getResourceAsStream("/phone_optical_python_sample.npz")!!.use { archive.readDevelopedOptical(it) }
        val opticalBytes = ByteArrayOutputStream()
        archive.writeDevelopedOptical(optical, opticalBytes)
        val opticalRoundTrip = archive.readDevelopedOptical(Fragmented(opticalBytes.toByteArray()))
        assertArrayEquals(optical.opticalDensityRgb.values, opticalRoundTrip.opticalDensityRgb.values, 0f)
    }

    @Test fun invalidLengthsAndTruncationFailWithoutAllocatingDeclaredPayload() {
        val header = "{'descr': '<f4', 'fortran_order': False, 'shape': (1000000000,), }\n".toByteArray()
        val prefix = byteArrayOf(0x93.toByte(), 78, 85, 77, 80, 89, 1, 0, header.size.toByte(), 0)
        assertThrows(NpyException::class.java) { NpyCodec.read(Fragmented(prefix + header)) }
        val good = NpyCodec.encode(NpyValue.Float32(FloatNd(intArrayOf(3), floatArrayOf(1f, 2f, 3f))))
        assertThrows(NpyException::class.java) { NpyCodec.read(Fragmented(good.copyOf(good.size - 1))) }
        val hugeHeader = byteArrayOf(0x93.toByte(), 78, 85, 77, 80, 89, 2, 0, -1, -1, -1, 127)
        assertThrows(NpyException::class.java) { NpyCodec.read(Fragmented(hugeHeader)) }
    }

    @Test fun crcAndDuplicateBasenamesAreStillRejected() {
        val payload = NpyCodec.encode(NpyValue.Float32(FloatNd(intArrayOf(3), floatArrayOf(1f, 2f, 3f))))
        val output = ByteArrayOutputStream()
        ZipOutputStream(output).use { zip ->
            val entry = ZipEntry("a.npy").apply {
                method = ZipEntry.STORED; size = payload.size.toLong(); compressedSize = size
                crc = CRC32().apply { update(payload) }.value
            }
            zip.putNextEntry(entry); zip.write(payload); zip.closeEntry()
        }
        val corrupt = output.toByteArray()
        // First member is STORED, so changing a trailing float byte preserves valid NPY but breaks CRC.
        val payloadEnd = 30 + "a.npy".length + payload.size - 1
        corrupt[payloadEnd] = (corrupt[payloadEnd].toInt() xor 1).toByte()
        assertThrows(ZipException::class.java) { NpzFile.read(Fragmented(corrupt)) }
        val duplicate = ByteArrayOutputStream()
        ZipOutputStream(duplicate).use { zip ->
            for (name in listOf("a/x.npy", "b/x.npy")) {
                zip.putNextEntry(ZipEntry(name)); zip.write(payload); zip.closeEntry()
            }
        }
        assertThrows(NpyException::class.java) { NpzFile.read(Fragmented(duplicate.toByteArray())) }
        // Duplicates must also be rejected when the first occurrence was discarded.
        assertThrows(NpyException::class.java) { NpzFile.read(Fragmented(duplicate.toByteArray())) { false } }
    }

    @Test fun discardedDiagnosticsStillReceiveFiniteAndDtypeValidation() {
        val sample = javaClass.getResourceAsStream("/phone_optical_python_sample.npz")!!.use { NpzFile.read(it) }
        val diagnostic = NpyValue.Float32(FloatNd(intArrayOf(8197), FloatArray(8197) { .5f }))
        val bytes = NpzFile.write(sample + ("large_diagnostic" to diagnostic))
        val selected = NpzFile.read(Fragmented(bytes)) { it != "large_diagnostic" }
        assertFalse("large_diagnostic" in selected)
        val optical = NpzMediumArchive().readDevelopedOptical(Fragmented(bytes))
        assertArrayEquals((sample.getValue(DataKernel.OPTICAL_DENSITY_KEY) as NpyValue.Float32).array.values, optical.opticalDensityRgb.values, 0f)
        val corrupted = NpyCodec.encode(diagnostic)
        // Valid ZIP CRC, invalid ignored numeric member: it must still fail finite checks.
        corrupted[corrupted.size - 4] = 0
        corrupted[corrupted.size - 3] = 0
        corrupted[corrupted.size - 2] = 0x80.toByte()
        corrupted[corrupted.size - 1] = 0x7f
        fun withRawDiagnostic(payload: ByteArray): ByteArray {
            val out = ByteArrayOutputStream()
            ZipOutputStream(out).use { zip ->
                for ((key, value) in sample) {
                    zip.putNextEntry(ZipEntry("$key.npy")); NpyCodec.write(value, zip); zip.closeEntry()
                }
                zip.putNextEntry(ZipEntry("large_diagnostic.npy")); zip.write(payload); zip.closeEntry()
            }
            return out.toByteArray()
        }
        assertThrows(NpyException::class.java) { NpzMediumArchive().readDevelopedOptical(Fragmented(withRawDiagnostic(corrupted))) }
        val objectHeader = corrupted.copyOf()
        for (i in 0 until objectHeader.size - 3) {
            if (objectHeader[i] == '<'.code.toByte() && objectHeader[i+1] == 'f'.code.toByte() && objectHeader[i+2] == '4'.code.toByte()) {
                objectHeader[i+1] = 'O'.code.toByte(); break
            }
        }
        assertThrows(NpyException::class.java) { NpzMediumArchive().readDevelopedOptical(Fragmented(withRawDiagnostic(objectHeader))) }
    }
}
