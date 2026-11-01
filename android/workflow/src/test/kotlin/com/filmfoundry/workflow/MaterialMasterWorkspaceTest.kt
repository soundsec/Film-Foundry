package com.filmfoundry.workflow

import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.util.concurrent.TimeUnit
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.io.TempDir

class MaterialMasterWorkspaceTest {
    @TempDir lateinit var directory: File
    private fun directoryLink(link: File,target: File) {
        if(File.separatorChar == '\\') {
            fun quote(file: File)="'"+file.path.replace("'","''")+"'"
            val process=ProcessBuilder("powershell.exe","-NoProfile","-NonInteractive","-Command",
                "New-Item -ItemType Junction -Path ${quote(link)} -Target ${quote(target)} | Out-Null")
                .redirectErrorStream(true).start()
            val message=process.inputStream.bufferedReader().use { it.readText() }
            assertEquals(0,process.waitFor(),message)
        } else Files.createSymbolicLink(link.toPath(),target.toPath())
    }

    @Test fun recoveryRetainsActiveAndUnknownAndOnlyRemovesMarkedIdleJobs() {
        val storage=MaterialMasterWorkspace()
        storage.open(directory,WorkflowControl()).use { live ->
            val registry=File(directory,MaterialMasterWorkspace.REGISTRY)
            val unknown=File(registry,"job-${UUID.randomUUID()}").apply { mkdir() }
            File(unknown,"keep").writeText("unknown")
            val orphan=File(registry,"job-${UUID.randomUUID()}").apply { mkdir() }
            File(orphan,"owner").writeText("film_foundry_material_job_v1\n")
            File(orphan,"lease.lock").writeBytes(byteArrayOf())
            File(orphan,"payload/scratch").mkdirs()
            File(orphan,"payload/scratch/partial").writeText("partial")
            val protected=File(directory,"protected").apply { mkdir() }
            File(protected,"owner").writeText("film_foundry_material_job_v1\n")
            File(protected,"lease.lock").writeBytes(byteArrayOf())
            File(protected,"keep").writeText("outside task")
            directoryLink(File(orphan,"payload/outside"),protected)
            directoryLink(File(registry,"job-${UUID.randomUUID()}"),protected)
            val old=File(directory,".ff-master-old").apply { mkdir() }
            assertEquals(1,storage.recover(directory))
            assertFalse(orphan.exists());assertTrue(live.payload.isDirectory)
            assertTrue(unknown.isDirectory);assertTrue(old.isDirectory)
            assertEquals("outside task",File(protected,"keep").readText())
        }
    }

    @Test fun lowSpaceDuringActualFrameWriteCleansJobAndPreservesExistingOutput() {
        var free=1L shl 30
        val session=MovieMasterSession(storage=MaterialMasterWorkspace { free })
        val existing=File(directory,"existing").apply { mkdir() }
        File(existing,"keep").writeText("existing")
        var closed=false;var at=0
        val reader=object: MovieFrameReader {
            override fun next(): MovieLinearFrame? {
                if(at++ > 0) return null
                return MovieLinearFrame(movieRgbRows(17,13) { _,count ->
                    free=0 // Storage drops after admission, before actual NPZ write.
                    FloatArray(17*count*3){.4f}
                },0)
            }
            override fun close() { closed=true }
        }
        val error=assertThrows(WorkflowException::class.java) { runBlocking {
            session.expose(reader,File(directory,"failed"),emptyMap())
        } }
        assertEquals("runtime_failure",error.code)
        assertTrue(error.message!!.contains("16 MiB"))
        assertTrue(closed);assertFalse(File(directory,"failed").exists())
        assertEquals("existing",File(existing,"keep").readText())
        assertTrue(File(directory,MaterialMasterWorkspace.REGISTRY).listFiles()!!.all { it.name == "guard.lock" })
        assertThrows(IOException::class.java) { MaterialMasterWorkspace { 0 }.open(directory,WorkflowControl()) }
    }

    @Test fun otherProcessLeaseSurvivesCollectionAndIsReclaimedAfterKill() {
        val registry=File(directory,MaterialMasterWorkspace.REGISTRY).apply { mkdir() }
        val orphan=File(registry,"job-${UUID.randomUUID()}").apply { mkdir() }
        File(orphan,"owner").writeText("film_foundry_material_job_v1\n")
        File(orphan,"payload").mkdir()
        val source=File(directory,"LeaseChild.java").apply { writeText("""
            import java.nio.file.*;
            import java.nio.channels.*;
            class LeaseChild {
                public static void main(String[] args) throws Exception {
                    try (var channel=FileChannel.open(Path.of(args[0]),StandardOpenOption.CREATE,StandardOpenOption.WRITE);
                         var lock=channel.lock(0,1,false)) {
                        System.out.println("locked");System.out.flush();
                        Thread.sleep(60000);
                    }
                }
            }
        """.trimIndent()) }
        val java=File(System.getProperty("java.home"),"bin/"+if(File.separatorChar == '\\') "java.exe" else "java")
        val process=ProcessBuilder(java.path,source.path,File(orphan,"lease.lock").path).redirectErrorStream(true).start()
        try {
            assertEquals("locked",process.inputStream.bufferedReader().readLine())
            val storage=MaterialMasterWorkspace()
            assertEquals(0,storage.recover(directory));assertTrue(orphan.exists())
            process.destroyForcibly();assertTrue(process.waitFor(10,TimeUnit.SECONDS))
            assertEquals(1,storage.recover(directory));assertFalse(orphan.exists())
        } finally {
            process.destroyForcibly();process.waitFor(10,TimeUnit.SECONDS)
            process.inputStream.close();process.outputStream.close();process.errorStream.close()
        }
    }
}
