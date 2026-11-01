package com.filmfoundry.platform

import java.io.File
import java.io.ByteArrayInputStream
import java.nio.file.Files
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class MovieSourcesTest {
    private val info=MovieInfo(1920,1080,300000L,90,"video/avc",1,false)

    @Test fun importedAndFinalizedRecordingsUseOnePrivateSourceCatalogWithoutLatentClaims() {
        val root=Files.createTempDirectory("ff-movie").toFile()
        try {
            val bytes=ByteArray(150000){(it%251).toByte()}
            val store=MovieSources(root) { file -> assertArrayEquals(bytes,file.readBytes());info }
            val imported=store.import(ByteArrayInputStream(bytes),"trip.mov")
            val draft=store.draft()
            assertEquals(1,store.list().size)
            draft.file.writeBytes(bytes)
            val recorded=store.commit(draft,"camera")
            draft.abandon()
            assertEquals(setOf("import","camera"),store.list().map { it.origin }.toSet())
            assertEquals("trip.mov",imported.displayName)
            assertEquals("recording.mp4",recorded.displayName)
            assertArrayEquals(bytes,recorded.file.readBytes())
            val metadata=File(imported.file.parentFile,"source.json").readText()
            assertTrue(metadata.contains("captured_source"))
            assertFalse(metadata.contains("latent"))
            assertEquals(info,store.list().first().info)
        } finally { root.deleteRecursively() }
    }

    @Test fun rejectedAndCancelledImportsNeverCommitAndCleanTheirDraft() {
        val root=Files.createTempDirectory("ff-movie-failure").toFile()
        try {
            val store=MovieSources(root) { throw CaptureRejected("no video track") }
            assertThrows(CaptureRejected::class.java) { store.import(ByteArrayInputStream(byteArrayOf(1,2,3))) }
            assertTrue(store.list().isEmpty())
            assertTrue(File(root,"movie_sources").listFiles()!!.isEmpty())
            val accepting=MovieSources(root) { info }
            var checks=0
            assertThrows(IllegalStateException::class.java) {
                accepting.import(ByteArrayInputStream(ByteArray(150000))) { if(++checks == 2) error("cancel") }
            }
            assertTrue(accepting.list().isEmpty())
            assertTrue(File(root,"movie_sources").listFiles()!!.isEmpty())
        } finally { root.deleteRecursively() }
    }
}
