package com.filmfoundry.interchange

import java.io.ByteArrayInputStream
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class MediumInspectionTest {
    private val codec=NpzMediumArchive()
    @Test fun metadataAndShapeAgreeWithFullReadersAndLateInvalidValuesAreRejected() {
        for(latent in listOf(true,false)) {
            val name=if(latent) "phone_latent_python_sample.npz" else "phone_optical_python_sample.npz"
            val bytes=javaClass.getResourceAsStream("/$name")!!.use { it.readBytes() }
            val expected=if(latent) codec.readLatent(bytes).metadata else codec.readDevelopedOptical(bytes).metadata
            val header=codec.inspect(ByteArrayInputStream(bytes),latent)
            assertEquals(expected,header.metadata)
        }
        val metadata=mapOf("image_polarity" to "negative","compatible_interpreters" to listOf("negative_scan"))
        val values=FloatArray(130*130*3){.2f}
        val entries=codec.opticalEntries(DevelopedOpticalArchive(FloatNd(intArrayOf(130,130,3),values),metadata))
        values[values.lastIndex]=-1f
        assertThrows(IllegalArgumentException::class.java) { codec.inspect(ByteArrayInputStream(NpzFile.write(entries)),false) }
        values[values.lastIndex]=.2f
        val bad=entries+ (DataKernel.CLEAR_BASE_KEY to NpyValue.UnicodeScalar("wrong type"))
        assertThrows(IllegalArgumentException::class.java) { codec.inspect(ByteArrayInputStream(NpzFile.write(bad)),false) }
    }
    @Test fun inspectionCapsRetainedMetadataAndInnerArchiveExpansionWithoutAllocatingFullArrays() {
        val numeric=NpzFile.write(mapOf("metadata" to NpyValue.Float32(FloatNd(intArrayOf(100000),FloatArray(100000)))))
        assertThrows(NpyException::class.java) {
            NpzFile.readChunks(ByteArrayInputStream(numeric),{true},NpzReadLimits(maxRetainedBytes=1024),null)
        }
        assertThrows(NpyException::class.java) {
            NpzFile.readChunks(ByteArrayInputStream(numeric),{false},NpzReadLimits(maxTotalBytes=1024),null)
        }
        val text=NpzFile.write(mapOf("metadata" to NpyValue.UnicodeScalar("x".repeat(1000))))
        assertThrows(NpyException::class.java) {
            NpzFile.readChunks(ByteArrayInputStream(text),{true},NpzReadLimits(maxStringBytes=1024),null)
        }
    }
}
