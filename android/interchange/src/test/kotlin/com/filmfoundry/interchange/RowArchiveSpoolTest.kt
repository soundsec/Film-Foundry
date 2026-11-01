package com.filmfoundry.interchange

import java.io.ByteArrayOutputStream
import java.nio.file.Path
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class RowArchiveSpoolTest {
    @TempDir lateinit var root: Path
    private fun field(rows: Int, width: Int, value: (Int) -> Float) =
        NpyValue.Float32(FloatNd(intArrayOf(rows, width, 1), FloatArray(rows * width, value)))

    @Test fun constantsSkipDiskAndLateVariationRestoresExactPrefixAndFullNpz() {
        val width = 8193
        RowArchiveSpool(root.toFile(), 5, width).use { spool ->
            for ((start, rows) in listOf(0 to 2, 2 to 2, 4 to 1)) {
                spool.append(mapOf(
                    "one" to field(rows, width) { 1f },
                    "zero" to field(rows, width) { 0f },
                    "negative_zero" to field(rows, width) { -0f },
                    "arbitrary" to field(rows, width) { .375f },
                    "late" to field(rows, width) { if (start == 4 && it == width - 1) 0f else -0f },
                ), rows)
                val files = java.io.File(root.toFile().listFiles()!!.single(),"data").listFiles()!!
                assertEquals(if (start == 4) 1 else 0, files.size)
                if (start == 4) assertEquals(5L * width * 4, files.single().length())
            }
            val packed = ByteArrayOutputStream().also(spool::pack).toByteArray()
            val entries = NpzFile.read(packed)
            for ((key, constant) in mapOf("one" to 1f, "zero" to 0f, "negative_zero" to -0f, "arbitrary" to .375f, "late" to -0f)) {
                val array = (entries.getValue(key) as NpyValue.Float32).array
                assertArrayEquals(intArrayOf(5, width, 1), array.shape)
                array.values.forEachIndexed { i, value ->
                    val expected = if (key == "late" && i == array.values.lastIndex) 0f else constant
                    assertEquals(expected.toRawBits(), value.toRawBits(), "$key at $i")
                }
            }
        }
        assertEquals(0, root.toFile().listFiles()!!.size)
    }

    @Test fun latentReadReconstructsConstantAndVariableRowsWithoutAssumingPoolNames() {
        val codec = NpzMediumArchive()
        val original = javaClass.getResourceAsStream("/phone_latent_python_sample.npz")!!.use { codec.readLatent(it) }
        val arrays = original.arrays.mapValues { (key, array) ->
            FloatNd(array.shape, FloatArray(array.values.size) { i ->
                when (key) {
                    "halide" -> if (i == array.values.lastIndex) .75f else 1f
                    "metallic_silver" -> -0f
                    else -> .25f
                }
            })
        }
        val bytes = codec.writeLatent(original.copy(arrays = arrays))
        RowArchiveSpool.readLatent(bytes.inputStream(), root.toFile()).use { spool ->
            val files = java.io.File(root.toFile().listFiles()!!.single(),"data").listFiles()!!
            assertEquals(1, files.size)
            assertEquals(arrays.getValue("halide").values.size * 4L, files.single().length())
            for (row in 0 until spool.height) {
                val actual = spool.latentRows(row, 1)
                for ((key, source) in arrays) {
                    val stride = spool.width * source.shape[2]
                    val expected = source.values.copyOfRange(row * stride, (row + 1) * stride)
                    assertArrayEquals(expected.map(Float::toRawBits).toIntArray(),
                        actual.arrays.getValue(key).values.map(Float::toRawBits).toIntArray())
                }
            }
        }
        assertEquals(0, root.toFile().listFiles()!!.size)
    }

    @Test fun cancelledPrefixPromotionAndInvalidConstantsCleanOwnedFiles() {
        var armed = false
        var calls = 0
        assertThrows(IllegalStateException::class.java) {
            RowArchiveSpool(root.toFile(), 3, 8193) {
                if (armed && ++calls == 3) error("cancel during prefix materialization")
            }.use { spool ->
                spool.append(mapOf("pool" to field(2, 8193) { 1f }), 2)
                armed = true
                spool.append(mapOf("pool" to field(1, 8193) { .5f }), 1)
            }
        }
        assertEquals(3, calls)
        assertEquals(0, root.toFile().listFiles()!!.size)
        for (invalid in listOf(Float.NaN, Float.POSITIVE_INFINITY)) {
            RowArchiveSpool(root.toFile(), 2, 1).use { spool ->
                assertThrows(NpyException::class.java) { spool.append(mapOf("pool" to field(1, 1) { invalid }), 1) }
                assertThrows(IllegalArgumentException::class.java) { spool.pack(ByteArrayOutputStream()) }
            }
        }
        assertEquals(0, root.toFile().listFiles()!!.size)
    }

    @Test fun materializedFieldsStillRejectNonfiniteValuesInLaterBlocks() {
        for(invalid in listOf(Float.NaN,Float.POSITIVE_INFINITY,Float.NEGATIVE_INFINITY)) {
            RowArchiveSpool(root.toFile(),2,3).use { spool ->
                spool.append(mapOf("pool" to field(1,3) { it.toFloat() }),1)
                assertThrows(NpyException::class.java) { spool.append(mapOf("pool" to field(1,3) { if(it==2) invalid else 1f }),1) }
                assertThrows(IllegalArgumentException::class.java) { spool.pack(ByteArrayOutputStream()) }
            }
        }
        assertTrue(root.toFile().listFiles().orEmpty().isEmpty())
    }

    @Test fun fastCompressionKeepsEveryNpyPayloadAndCleansScratchOnCancellation() {
        RowArchiveSpool(root.toFile(), 3, 8193).use { spool ->
            spool.append(mapOf("varied" to field(3,8193) { if(it%11==0) -0f else (it%233)/71f },
                "constant" to field(3,8193) { -0f }),3)
            val normal=ByteArrayOutputStream().also(spool::pack).toByteArray()
            val fast=ByteArrayOutputStream().also { spool.pack(it,java.util.zip.Deflater.BEST_SPEED) }.toByteArray()
            fun payloads(bytes: ByteArray): Map<String,ByteArray> {
                val result=linkedMapOf<String,ByteArray>()
                java.util.zip.ZipInputStream(bytes.inputStream()).use { zip ->
                    while(true) { val member=zip.nextEntry ?: break;result[member.name]=zip.readBytes() }
                }
                return result
            }
            val expected=payloads(normal);val actual=payloads(fast)
            assertEquals(expected.keys,actual.keys)
            for(key in expected.keys) assertArrayEquals(expected.getValue(key),actual.getValue(key))
            assertEquals(2,NpzFile.read(fast).size)
            assertThrows(IllegalArgumentException::class.java) { spool.pack(ByteArrayOutputStream(),10) }
        }
        var stop=false
        assertThrows(IllegalStateException::class.java) {
            RowArchiveSpool(root.toFile(),3,8193) { if(stop) error("cancel pack") }.use { spool ->
                spool.append(mapOf("data" to field(3,8193) { it/71f }),3);stop=true
                spool.pack(ByteArrayOutputStream(),java.util.zip.Deflater.BEST_SPEED)
            }
        }
        assertTrue(root.toFile().listFiles()!!.isEmpty())
    }
}
