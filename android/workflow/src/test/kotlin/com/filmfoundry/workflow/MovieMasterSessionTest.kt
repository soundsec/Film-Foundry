package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.io.TempDir

class MovieMasterSessionTest {
    @TempDir lateinit var directory: File
    private fun reader()=object: MovieFrameReader {
        var at=0
        override fun next(): MovieLinearFrame? {
            if(at == 3) return null
            val pts=listOf(80000L,120000L,200000L)[at++]
            return MovieLinearFrame(movieRgbRows(17,13) { _,count -> FloatArray(17*count*3){ .4f } },pts)
        }
        override fun close() {}
    }
    @Test fun actualRowFilesRetainSourceAndCanBeDevelopedAndObservedIndependently()=runBlocking {
        val session=MovieMasterSession()
        val source=session.expose(reader(),File(directory,"latent"),emptyMap(),42)
        val original=session.read(source)
        val before=original.frames.map { File(source,it.path).readBytes() }
        val a=session.develop(source,File(directory,"short"),mapOf("develop" to mapOf("time_min" to 2)))
        val b=session.develop(source,File(directory,"long"),mapOf("develop" to mapOf("time_min" to 12)))
        val aDoc=session.read(a);val bDoc=session.read(b)
        assertEquals(original.masterId,aDoc.sourceMasterId)
        assertEquals(original.frames.map { it.pts },bDoc.frames.map { it.pts })
        val observed=session.observe(a,1,emptyMap())
        val changed=session.observe(b,1,emptyMap())
        assertFalse(observed.rgb.contentEquals(changed.rgb))
        original.frames.forEachIndexed { i,f -> assertArrayEquals(before[i],File(source,f.path).readBytes()) }
        assertEquals(17,observed.width);assertEquals(13,observed.height)
        assertTrue(directory.listFiles()!!.none { it.name.startsWith(".ff-master-") })
        val manifest=File(source,MovieMasterCodec.MANIFEST)
        manifest.writeBytes(MovieMasterCodec.encode(original.copy(rollId="wrong-roll")))
        assertThrows(WorkflowException::class.java) { runBlocking {
            session.develop(source,File(directory,"bad-identity"),emptyMap())
        } }
        assertFalse(File(directory,"bad-identity").exists())
        assertTrue(directory.listFiles()!!.none { it.name.startsWith(".ff-master-") })
    }
    @Test fun cancelOrDecoderFailurePublishesNoPartialMaster() {
        val session=MovieMasterSession()
        assertThrows(WorkflowException::class.java) { runBlocking {
            session.expose(reader(),File(directory,"cancelled"),emptyMap(),control=WorkflowControl(cancelRequested={true}))
        } }
        assertFalse(File(directory,"cancelled").exists())
        val failing=object: MovieFrameReader {
            val delegate=reader()
            var called=false
            override fun next(): MovieLinearFrame? { if(called) error("decoder failure");called=true;return delegate.next() }
            override fun close() {}
        }
        assertThrows(IllegalStateException::class.java) { runBlocking {
            session.expose(failing,File(directory,"broken"),emptyMap())
        } }
        assertTrue(File(directory,MaterialMasterWorkspace.REGISTRY).listFiles()!!.all { it.name == "guard.lock" })
        assertTrue(directory.listFiles()!!.all { it.name == MaterialMasterWorkspace.REGISTRY })
    }
}
