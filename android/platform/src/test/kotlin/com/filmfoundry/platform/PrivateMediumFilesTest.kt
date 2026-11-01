package com.filmfoundry.platform

import com.filmfoundry.interchange.*
import java.io.*
import java.nio.file.Path
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class PrivateMediumFilesTest {
    @TempDir lateinit var root: Path

    @Test fun failedSerializationKeepsPreviousMediumAndCleansPendingFile() {
        val files = PrivateMediumFiles(root.toFile())
        files.write("roll/frame.darkroom_negative.npz") { it.write(byteArrayOf(1, 2, 3)) }
        assertThrows(IOException::class.java) {
            files.write("roll/frame.darkroom_negative.npz") { it.write(ByteArray(70000)); throw IOException("cancelled") }
        }
        assertArrayEquals(byteArrayOf(1, 2, 3), files.read("roll/frame.darkroom_negative.npz") { it.readBytes() })
        assertEquals(listOf("frame.darkroom_negative.npz"), root.resolve("roll").toFile().listFiles()!!.map { it.name })
        assertThrows(IOException::class.java) { files.write("new.npz") { throw IOException("cancelled") } }
        assertFalse(root.resolve("new.npz").toFile().exists())
        assertThrows(CaptureRejected::class.java) { files.write("../outside") { it.write(0) } }
        files.write("roll/frame.darkroom_negative.npz") { it.write(byteArrayOf(4, 5)) }
        assertArrayEquals(byteArrayOf(4, 5), files.read("roll/frame.darkroom_negative.npz") { it.readBytes() })
    }

    @Test fun mediumStoreUsesStreamsWithoutCallingWholeFileByteApis() {
        val files = PrivateMediumFiles(root.toFile())
        val storage = object : StorageAdapter by MemoryStorageAdapter() {
            override fun writePrivate(relative: String, bytes: ByteArray): Unit = error("whole-file write")
            override fun readPrivate(relative: String): ByteArray = error("whole-file read")
            override fun writePrivate(relative: String, writer: (OutputStream) -> Unit) = files.write(relative, writer)
            override fun <T> readPrivate(relative: String, reader: (InputStream) -> T): T = files.read(relative, reader)
        }
        val store = MediumStore(storage)
        val metadata = mapOf<String, Any?>("image_polarity" to "negative", "compatible_interpreters" to listOf("negative_scan"))
        val original = DevelopedOpticalArchive(FloatNd(intArrayOf(1, 2, 3), floatArrayOf(.1f, .2f, .3f, .4f, .5f, .6f)), metadata)
        store.writeOptical("frame.darkroom_negative.npz", original)
        assertArrayEquals(original.opticalDensityRgb.values, store.readOptical("frame.darkroom_negative.npz").opticalDensityRgb.values, 0f)
        val pools = mapOf("halide" to FloatNd(intArrayOf(1, 2, 3), FloatArray(6) { 1f }),
            "developability" to FloatNd(intArrayOf(1, 2, 3), FloatArray(6) { .25f }),
            "metallic_silver" to FloatNd(intArrayOf(1, 2, 3), FloatArray(6)))
        store.writeLatent("frame.darkroom_latent.npz", LatentArchive(pools, metadata))
        assertArrayEquals(pools.getValue("developability").values, store.readLatent("frame.darkroom_latent.npz").arrays.getValue("developability").values, 0f)
        assertThrows(IllegalArgumentException::class.java) {
            store.writeOptical("frame.darkroom_negative.npz", original.copy(opticalDensityRgb = FloatNd(intArrayOf(1, 1, 3), floatArrayOf(Float.NaN, 0f, 0f))))
        }
        assertArrayEquals(original.opticalDensityRgb.values, store.readOptical("frame.darkroom_negative.npz").opticalDensityRgb.values, 0f)
    }

    @Test fun cancellationImmediatelyBeforeRenameKeepsOldFile() {
        val files = PrivateMediumFiles(root.toFile())
        files.write("frame.npz") { it.write(1) }
        assertThrows(IOException::class.java) {
            files.write("frame.npz", { throw IOException("cancelled before commit") }) { it.write(2) }
        }
        assertArrayEquals(byteArrayOf(1), files.read("frame.npz") { it.readBytes() })
        assertEquals(listOf("frame.npz"), root.toFile().listFiles()!!.map { it.name })
    }
}
