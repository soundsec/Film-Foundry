package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import java.io.*
import java.util.zip.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.io.TempDir

class MovieMasterZipTest {
    @TempDir lateinit var directory: File
    private val session=MovieMasterSession()
    private fun reader()=object: MovieFrameReader {
        var at=0
        override fun next(): MovieLinearFrame? = if(at == 2) null else
            MovieLinearFrame(movieRgbRows(17,13) { _,n -> FloatArray(17*n*3){.4f} },listOf(80L,120L)[at++],1,1000)
        override fun close() {}
    }
    private fun archive(root: File): ByteArray=ByteArrayOutputStream().also { MovieMasterZip().export(root,it) }.toByteArray()
    private fun entries(bytes: ByteArray): Map<String,ByteArray> {
        val entries=linkedMapOf<String,ByteArray>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip -> while(true) {
            val entry=zip.nextEntry ?: break;entries[entry.name]=zip.readBytes();zip.closeEntry()
        } }
        return entries
    }
    private fun zip(entries: Map<String,ByteArray>)=ByteArrayOutputStream().also { out -> ZipOutputStream(out).use { z ->
        entries.forEach { (name,bytes) -> z.putNextEntry(ZipEntry(name));z.write(bytes);z.closeEntry() }
    } }.toByteArray()
    private fun clean()=assertTrue(File(directory,MaterialMasterWorkspace.REGISTRY).listFiles()!!.all { it.name == "guard.lock" })

    @Test fun latentAndWrappedOpticalRoundTripsKeepBytesAndPermitRepeatedDevelopmentObservation()=runBlocking {
        val source=session.expose(reader(),File(directory,"source"),emptyMap())
        val packed=archive(source)
        val imported=MovieMasterZip().import(ByteArrayInputStream(packed),File(directory,"imported"))
        val original=session.read(source);val restored=session.read(imported)
        assertEquals(original,restored)
        for(frame in original.frames) assertArrayEquals(File(source,frame.path).readBytes(),File(imported,frame.path).readBytes())
        val first=session.develop(imported,File(directory,"first"),mapOf("develop" to mapOf("time_min" to 2)))
        val second=session.develop(imported,File(directory,"second"),mapOf("develop" to mapOf("time_min" to 12)))
        assertFalse(session.observe(first,1,emptyMap()).rgb.contentEquals(session.observe(second,1,emptyMap()).rgb))
        val wrapped=zip(entries(archive(first)).mapKeys { "desktop-master/"+it.key })
        val optical=MovieMasterZip().import(ByteArrayInputStream(wrapped),File(directory,"optical"))
        assertEquals(session.read(first),session.read(optical))
        assertArrayEquals(session.observe(first,1,emptyMap()).rgb,session.observe(optical,1,emptyMap()).rgb)
        for(frame in original.frames) assertArrayEquals(File(source,frame.path).readBytes(),File(imported,frame.path).readBytes())
        assertThrows(IllegalArgumentException::class.java) { MovieMasterZip().import(ByteArrayInputStream(packed),imported) }
        clean()
    }

    @Test fun traversalAliasesExtraMissingFramesAndTruncatedArchivesNeverPublish()=runBlocking {
        val source=session.expose(reader(),File(directory,"source"),emptyMap())
        val original=entries(archive(source));val frame=original.keys.first { it.startsWith("frames/") }
        val bad=listOf(original+("../escape" to byteArrayOf(1)),original+("/absolute" to byteArrayOf(1)),
            original+("frames\\evil" to byteArrayOf(1)),original+("MASTER.JSON" to original.getValue("master.json")),
            original+("unexpected" to byteArrayOf(1)),original-frame)
        for((i,entries) in bad.withIndex()) {
            val target=File(directory,"bad$i")
            assertThrows(IllegalArgumentException::class.java) { MovieMasterZip().import(ByteArrayInputStream(zip(entries)),target) }
            assertFalse(target.exists());clean()
        }
        val bytes=archive(source)
        val truncated=assertThrows(Exception::class.java) {
            MovieMasterZip().import(ByteArrayInputStream(bytes.copyOf(bytes.size-22)),File(directory,"truncated"))
        }
        // A reader may find an embedded NPZ end record after the outer end was
        // truncated; the required outer manifest must still reject that archive.
        assertTrue(truncated is IOException || truncated is IllegalArgumentException)
        assertFalse(File(directory,"truncated").exists())
        assertFalse(File(directory,"escape").exists());clean()
    }

    @Test fun wrongMaterialIdentityDimensionsOrPayloadAreRejectedBeforePublication()=runBlocking {
        val source=session.expose(reader(),File(directory,"source"),emptyMap())
        val original=entries(archive(source));val document=session.read(source)
        val changed=listOf(document.copy(rollId="wrong"),document.copy(width=18))
        for((i,doc) in changed.withIndex()) {
            assertThrows(IllegalArgumentException::class.java) {
                MovieMasterZip().import(ByteArrayInputStream(zip(original+("master.json" to MovieMasterCodec.encode(doc)))),File(directory,"identity$i"))
            }
            assertFalse(File(directory,"identity$i").exists());clean()
        }
        val path=document.frames.first().path
        assertThrows(IOException::class.java) { MovieMasterZip().import(ByteArrayInputStream(zip(original+(path to byteArrayOf(1,2,3)))),File(directory,"payload")) }
        val corrupt=original.getValue(path).copyOf()
        val central=(0..corrupt.size-4).first { i -> corrupt[i] == 80.toByte() && corrupt[i+1] == 75.toByte() &&
            corrupt[i+2] == 1.toByte() && corrupt[i+3] == 2.toByte() }
        corrupt[central+16]=(corrupt[central+16].toInt() xor 1).toByte()
        assertThrows(IllegalArgumentException::class.java) {
            MovieMasterZip().import(ByteArrayInputStream(zip(original+(path to corrupt))),File(directory,"bad-central"))
        }
        assertFalse(File(directory,"bad-central").exists())
        clean()
    }

    @Test fun cancellationLimitsAndLowSpaceAfterPartialImportPreserveSource()=runBlocking {
        val source=session.expose(reader(),File(directory,"source"),emptyMap())
        val packed=archive(source);val sourceBytes=File(source,session.read(source).frames.first().path).readBytes()
        var cancel=false;var frames=0
        assertThrows(WorkflowException::class.java) { MovieMasterZip().import(ByteArrayInputStream(packed),File(directory,"cancel"),
            WorkflowControl(cancelRequested={cancel},onProgress={ _,_ -> if(++frames == 2) cancel=true })) }
        assertFalse(File(directory,"cancel").exists());clean()
        for(limits in listOf(MovieMasterZip.Limits(maxArchiveBytes=10),MovieMasterZip.Limits(maxExpandedBytes=10),
            MovieMasterZip.Limits(maxFrameBytes=10),MovieMasterZip.Limits(maxManifestBytes=10),MovieMasterZip.Limits(maxFrames=1))) {
            assertThrows(IllegalArgumentException::class.java) { MovieMasterZip(limits=limits).import(ByteArrayInputStream(packed),File(directory,"limit")) }
            assertFalse(File(directory,"limit").exists());clean()
        }
        var free=1L shl 30
        assertThrows(IOException::class.java) { MovieMasterZip(MaterialMasterWorkspace { free }).import(ByteArrayInputStream(packed),File(directory,"full"),
            WorkflowControl(onProgress={ _,_ -> free=0 })) }
        assertFalse(File(directory,"full").exists());clean()
        assertArrayEquals(sourceBytes,File(source,session.read(source).frames.first().path).readBytes())
    }
}
