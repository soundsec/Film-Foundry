package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import java.io.ByteArrayOutputStream

class MediumArchiveTest {
    private val archive = NpzMediumArchive()

    private val identity = mapOf(
        "medium_family" to "film",
        "medium_process" to "negative",
        "image_polarity" to "negative",
        "view_mode" to "transmissive",
        "base_type" to "orange_mask",
        "color_system" to "color_negative_dye",
        "compatible_interpreters" to listOf("negative_scan"),
        "frame_identity" to mapOf(
            "roll_id" to "pocket-roll",
            "frame_index" to 1,
        ),
    )

    @Test
    fun readsDesktopOpticalFixture() {
        val bytes = resource("phone_optical_python_sample.npz")
        val developed = archive.readDevelopedOptical(bytes)
        assertArrayEquals(intArrayOf(2, 3, 3), developed.opticalDensityRgb.shape)
        assertEquals(0.10f, developed.opticalDensityRgb.values[0], 1e-6f)
        assertEquals(1.80f, developed.opticalDensityRgb.values.last(), 1e-6f)
        assertEquals("negative", developed.metadata["image_polarity"])
        val frame = developed.metadata["frame_identity"] as Map<*, *>
        assertEquals("pocket-roll", frame["roll_id"])
        assertEquals(1, (frame["frame_index"] as Number).toInt())
        assertFalse(developed.metadata.toString().contains("canonical_full_v1"))
        assertFalse(NpzFile.read(bytes).containsKey("density_cmy"))
    }

    @Test fun specializedOpticalValidatorsKeepFiniteAndSignedFieldRules() {
        val profile=mapOf("model" to "normalized_pool_absorbance_v1","profile_id" to "validation",
            "coefficient_unit" to "normalized_final_pool_v1","component_names" to SpectralOptics.components,
            "wavelengths_nm" to listOf(400,700),"component_absorbance" to List(9) { listOf(.1,.2) })
        fun field(channels: Int,value: Float)=NpyValue.Float32(FloatNd(intArrayOf(1,1,channels),FloatArray(channels){value}))
        val entries=mapOf(DataKernel.DEVELOPED_METADATA_KEY to NpyValue.UnicodeScalar(RecipeJson.text(identity+
            (SpectralOptics.MEDIUM_KEY to mapOf("profile" to profile,"base_component_amounts" to List(9){0.0},"post_process_model" to SpectralOptics.POST_PROCESS_MODEL)))),
            DataKernel.OPTICAL_DENSITY_KEY to field(3,.2f),SpectralOptics.AMOUNTS_KEY to field(9,.1f),
            SpectralOptics.POST_PROCESS_KEY to field(3,-.1f),
            DataKernel.CLEAR_BASE_KEY to NpyValue.Float32(FloatNd(intArrayOf(3),FloatArray(3){.1f})))
        assertEquals(-.1f,archive.opticalFromEntries(entries).spectralPostProcessDensityRgb!!.values[0])
        for(key in listOf(DataKernel.OPTICAL_DENSITY_KEY,SpectralOptics.AMOUNTS_KEY,SpectralOptics.POST_PROCESS_KEY,DataKernel.CLEAR_BASE_KEY)) {
            val original=(entries.getValue(key) as NpyValue.Float32).array
            for(value in listOf(Float.NaN,Float.POSITIVE_INFINITY,Float.NEGATIVE_INFINITY)) {
                val changed=NpyValue.Float32(FloatNd(original.shape.copyOf(),original.values.copyOf().also { it[0]=value }))
                assertThrows(InterchangeException::class.java) { archive.opticalFromEntries(entries+(key to changed)) }
            }
            if(key!=SpectralOptics.POST_PROCESS_KEY) {
                val changed=NpyValue.Float32(FloatNd(original.shape.copyOf(),original.values.copyOf().also { it[0]=-.1f }))
                assertThrows(InterchangeException::class.java) { archive.opticalFromEntries(entries+(key to changed)) }
            }
        }
    }

    @Test
    fun readsDesktopLatentFixture() {
        val bytes = resource("phone_latent_python_sample.npz")
        val latent = archive.readLatent(bytes)
        assertEquals(DataKernel.LATENT_KIND, latent.metadata["kind"])
        assertEquals(false, latent.metadata["consumed"])
        assertArrayEquals(intArrayOf(2, 2, 3), latent.arrays.getValue("halide").shape)
        assertEquals(0.5f, latent.arrays.getValue("halide").values[0], 1e-6f)
        assertEquals(0.8f, latent.arrays.getValue("developability").values[0], 1e-6f)
        assertEquals(0.0f, latent.arrays.getValue("metallic_silver").values[0], 1e-6f)
        val frame = latent.metadata["frame_identity"] as Map<*, *>
        assertEquals("pocket-roll", frame["roll_id"])
    }

    @Test
    fun opticalRoundTripKeepsValuesAndOmitsLayerKeys() {
        val original = archive.readDevelopedOptical(resource("phone_optical_python_sample.npz"))
        val written = archive.writeDevelopedOptical(original)
        val keys = NpzFile.read(written).keys
        assertTrue("optical_density_rgb" in keys)
        assertTrue("developed_medium_metadata" in keys)
        assertFalse("density_cmy" in keys)
        assertFalse("density_grain" in keys)
        val restored = archive.readDevelopedOptical(written)
        assertArrayEquals(original.opticalDensityRgb.values, restored.opticalDensityRgb.values, 0.0f)
        assertEquals("negative", restored.metadata["image_polarity"])
    }

    @Test
    fun latentRoundTripThenConsume() {
        val original = archive.readLatent(resource("phone_latent_python_sample.npz"))
        val written = archive.writeLatent(original)
        val restored = archive.readLatent(written)
        assertArrayEquals(
            original.arrays.getValue("developability").values,
            restored.arrays.getValue("developability").values,
            0.0f,
        )
        val consumed = archive.markLatentConsumed(written)
        assertThrows(InterchangeException::class.java) { archive.readLatent(consumed) }
        val allowed = archive.readLatent(consumed, allowConsumed = true)
        assertEquals(true, allowed.metadata["consumed"])
    }

    @Test
    fun rejectsPickleMember() {
        assertThrows(NpyException::class.java) {
            archive.readDevelopedOptical(zipOf("legacy" to objectNpy()))
        }
    }

    @Test
    fun rejectsNegativeOpticalDensity() {
        val optical = FloatNd(intArrayOf(1, 1, 3), floatArrayOf(-0.1f, 0.2f, 0.3f))
        assertThrows(InterchangeException::class.java) {
            archive.writeDevelopedOptical(DevelopedOpticalArchive(optical, identity))
        }
    }

    @Test
    fun rejectsCanonicalFullStampOnPhoneWrite() {
        val optical = FloatNd(intArrayOf(1, 1, 3), floatArrayOf(0.1f, 0.2f, 0.3f))
        val stamped = identity + ("film_process_model" to mapOf(
            "execution_implementation" to mapOf("profile_id" to "canonical_full_v1"),
        ))
        assertThrows(InterchangeException::class.java) {
            archive.writeDevelopedOptical(DevelopedOpticalArchive(optical, stamped))
        }
    }

    @Test
    fun rejectsDevelopedFileAsLatent() {
        assertThrows(InterchangeException::class.java) {
            archive.readLatent(resource("phone_optical_python_sample.npz"))
        }
    }

    private fun resource(name: String): ByteArray {
        return javaClass.getResource("/$name")!!.readBytes()
    }

    private fun objectNpy(): ByteArray {
        val dict = "{'descr': '|O', 'fortran_order': False, 'shape': (), }"
        var header = dict.toByteArray(Charsets.US_ASCII)
        val prefix = 10
        val padlen = 64 - ((prefix + header.size + 1) % 64)
        header += ByteArray(padlen) { ' '.code.toByte() } + byteArrayOf('\n'.code.toByte())
        val out = ByteArray(prefix + header.size + 1)
        out[0] = 0x93.toByte()
        "NUMPY".encodeToByteArray().copyInto(out, 1)
        out[6] = 1
        out[7] = 0
        ByteBuffer.wrap(out, 8, 2).order(ByteOrder.LITTLE_ENDIAN).putShort(header.size.toShort())
        header.copyInto(out, prefix)
        out[out.lastIndex] = 0
        return out
    }

    private fun zipOf(vararg members: Pair<String, ByteArray>): ByteArray {
        val buffer = ByteArrayOutputStream()
        ZipOutputStream(buffer).use { zip ->
            for ((name, payload) in members) {
                zip.putNextEntry(ZipEntry("$name.npy"))
                zip.write(payload)
                zip.closeEntry()
            }
        }
        return buffer.toByteArray()
    }
}
