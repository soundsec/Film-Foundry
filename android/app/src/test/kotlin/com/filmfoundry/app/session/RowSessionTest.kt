package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import com.filmfoundry.interchange.DataKernel
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class RowSessionTest {
    @TempDir lateinit var root: Path

    @Test fun savedLatentAfterIndexFailureIsRecoveredWithoutAnotherExposureOrRewrite() = runBlocking {
        for(restart in listOf(false,true)) {
            val index=MemoryLibraryIndexStore()
            var fail=false
            val failing=object: LibraryIndexStore by index {
                override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index unavailable"); index.writeIndex(bytes) }
            }
            val id=if(restart) "restart" else "same-session"
            val library=FilmLibrary(failing); library.loadRoll(id,12,emptyMap())
            val store=MediumStore(storage()); val facade=DefaultWorkflowFacade()
            val scripted=ScriptedCameraAdapter(still=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
                CameraXBufferFormat.JPEG,17,13,encodedRgb=FloatArray(17*13*3){.5f}))
            var captures=0
            val camera=object: CameraAdapter by scripted {
                override fun captureStill(request: StillCaptureRequest)=scripted.captureStill(request).also { captures++ }
            }
            fail=true
            assertThrows(IOException::class.java) { runBlocking { ShootSession(camera,library,store,facade).captureNextFrame(id) } }
            val path=cameraLatentPath(id,1)
            val original=Files.readAllBytes(root.resolve(path))
            assertEquals(0,library.roll(id).latentCount())
            assertEquals(FrameState.EMPTY,library.roll(id).frames[0].state)
            fail=false
            val resumed=if(restart) FilmLibrary(failing) else library
            var recovered=false
            val session=ShootSession(camera,resumed,store,facade)
            val first=session.captureNextFrame(id,onRecovered={recovered=true})
            assertTrue(recovered); assertEquals(1,first.frameIndex); assertEquals(1,captures)
            assertArrayEquals(original,Files.readAllBytes(root.resolve(path)))
            assertEquals(1,FilmLibrary(failing).roll(id).latentCount())
            assertEquals(2,session.captureNextFrame(id).frameIndex)
            assertEquals(2,captures)
        }
    }

    @Test fun sealingRecoversAnUnindexedSavedShotWithoutAWorkingCamera() = runBlocking {
        val saved=MemoryLibraryIndexStore(); var fail=false
        val index=object: LibraryIndexStore by saved {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index unavailable"); saved.writeIndex(bytes) }
        }
        val library=FilmLibrary(index); library.loadRoll("seal-recover",12,emptyMap())
        val store=MediumStore(storage())
        val camera=ScriptedCameraAdapter(still=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
            CameraXBufferFormat.JPEG,2,2,encodedRgb=FloatArray(12){.4f}))
        fail=true
        assertThrows(IOException::class.java) { runBlocking {
            ShootSession(camera,library,store,DefaultWorkflowFacade()).captureNextFrame("seal-recover")
        } }
        val path=cameraLatentPath("seal-recover",1)
        val original=Files.readAllBytes(root.resolve(path))
        fail=false
        val restored=FilmLibrary(index)
        val roll=SealSession(restored,store).seal("seal-recover")
        assertEquals(RollLifecycle.SEALED,roll.lifecycle); assertEquals(1,roll.latentCount())
        assertArrayEquals(original,Files.readAllBytes(root.resolve(path)))
        assertEquals(RollLifecycle.SEALED,FilmLibrary(index).roll("seal-recover").lifecycle)
    }

    @Test fun corruptOrOtherRollLatentsBlockRetakeAndSealingAndStayUntouched() = runBlocking {
        val library=FilmLibrary(); val store=MediumStore(storage()); val facade=DefaultWorkflowFacade()
        library.loadRoll("original",12,emptyMap()); library.loadRoll("wrong",12,emptyMap()); library.loadRoll("broken",12,emptyMap())
        val camera=ScriptedCameraAdapter(still=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
            CameraXBufferFormat.JPEG,2,2,encodedRgb=FloatArray(12){.4f}))
        val first=ShootSession(camera,library,store,facade).captureNextFrame("original")
        val bytes=Files.readAllBytes(root.resolve(first.latentPath!!))
        for((id,content) in listOf("wrong" to bytes,"broken" to byteArrayOf(1,2,3))) {
            val path=root.resolve(cameraLatentPath(id,1)); Files.createDirectories(path.parent); Files.write(path,content)
            val noCamera=ShootSession(ScriptedCameraAdapter(),library,store,facade)
            assertThrows(CaptureRejected::class.java) { runBlocking { noCamera.captureNextFrame(id) } }
            assertThrows(CaptureRejected::class.java) { runBlocking { SealSession(library,store).seal(id) } }
            assertArrayEquals(content,Files.readAllBytes(path)); assertEquals(0,library.roll(id).latentCount())
            assertEquals(RollLifecycle.LOADING,library.roll(id).lifecycle)
        }
    }

    @Test fun wholeRollFreezesProcessAndMaterialWhileKeepingOriginalLatents() = runBlocking {
        val library = FilmLibrary()
        val store = MediumStore(storage()); val facade = DefaultWorkflowFacade()
        library.loadRoll("whole",12,mapOf("base_density_rgb" to listOf(.2,.1,.05)))
        val camera = ScriptedCameraAdapter(still=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
            CameraXBufferFormat.JPEG,17,13,encodedRgb=FloatArray(17*13*3){.5f}))
        val shoot = ShootSession(camera,library,store,facade)
        val frames = List(3) { shoot.captureNextFrame("whole") }
        assertEquals(listOf(1,2,3),frames.map { it.frameIndex })
        val before = frames.map { Files.readAllBytes(root.resolve(it.latentPath!!)) }
        library.seal("whole")
        val process = mutableMapOf<String,Any?>("time_min" to 2)
        val recipe = mutableMapOf<String,Any?>("develop" to process,"film" to mapOf("base_density_rgb" to listOf(9,9,9)))
        val develop = DevelopSession(library,store,facade)
        val results = develop.developRoll("whole","batch",recipe) { process["time_min"] = 12 }
        assertEquals(3,results.size)
        val optical = results.map { store.readOptical(it.opticalPath) }
        for(i in frames.indices) {
            assertArrayEquals(before[i],Files.readAllBytes(root.resolve(frames[i].latentPath!!)))
            assertArrayEquals(optical[0].opticalDensityRgb.values,optical[i].opticalDensityRgb.values)
            assertEquals(i+1,((optical[i].metadata["frame_identity"] as Map<*,*>)["frame_index"] as Number).toInt())
            assertEquals("whole:${i+1}",(optical[i].metadata["frame_identity"] as Map<*,*>)["frame_id"])
            assertEquals(2,((results[i].developRecipe["develop"] as Map<*,*>)["time_min"] as Number).toInt())
        }
        val second = develop.developRoll("whole","second",recipe)
        assertFalse(optical[0].opticalDensityRgb.values.contentEquals(store.readOptical(second[0].opticalPath).opticalDensityRgb.values))
        assertEquals(6,library.developedResults().size)
    }

    @Test fun cancellingRollDevelopmentRetainsCommittedResultsAndUntouchedRemainingLatents() = runBlocking {
        val library=FilmLibrary(); val store=MediumStore(storage()); val facade=DefaultWorkflowFacade()
        library.loadRoll("partial",12,emptyMap())
        val camera=ScriptedCameraAdapter(still=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
            CameraXBufferFormat.JPEG,2,2,encodedRgb=FloatArray(12){.5f}))
        val shoot=ShootSession(camera,library,store,facade)
        repeat(2) { shoot.captureNextFrame("partial") }
        val source=library.roll("partial").frames.take(2).map { Files.readAllBytes(root.resolve(it.latentPath!!)) }
        library.seal("partial")
        var cancelled=false
        val session=DevelopSession(library,store,facade)
        val error=assertThrows(WorkflowException::class.java) { runBlocking {
            session.developRoll("partial","partial-batch",emptyMap(),WorkflowControl(cancelRequested={cancelled})) { cancelled=true }
        } }
        assertEquals("cancelled",error.code)
        assertEquals(listOf("partial-batch-1"),library.developedResults().map { it.resultId })
        assertFalse(Files.exists(root.resolve("results/partial-batch-2.darkroom_negative.npz")))
        for(i in 0..1) assertArrayEquals(source[i],Files.readAllBytes(root.resolve(library.roll("partial").frames[i].latentPath!!)))
        assertThrows(IllegalArgumentException::class.java) { runBlocking {
            session.developRoll("partial","partial-batch",emptyMap())
        } }
        Unit // JUnit requires a void test; assertThrows otherwise becomes runBlocking's return value.
    }
    @Test fun unindexedOpticalResultIsNeverOverwrittenOrRecomputedOnRetry() = runBlocking {
        val saved=MemoryLibraryIndexStore();var fail=false
        val index=object: LibraryIndexStore by saved {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index unavailable");saved.writeIndex(bytes) }
        }
        val library=FilmLibrary(index);library.loadRoll("result-guard",12,emptyMap())
        val store=MediumStore(storage());val original=DefaultWorkflowFacade()
        val camera=ScriptedCameraAdapter(still=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
            CameraXBufferFormat.JPEG,2,2,encodedRgb=FloatArray(12){.4f}))
        val frame=ShootSession(camera,library,store,original).captureNextFrame("result-guard")
        library.seal("result-guard")
        val latentBytes=Files.readAllBytes(root.resolve(frame.latentPath!!))
        var runs=0
        val facade=object: WorkflowFacade {
            override suspend fun run(request: WorkflowRequest): WorkflowResult { runs++;return original.run(request) }
        }
        val session=DevelopSession(library,store,facade)
        fail=true
        assertThrows(IOException::class.java) { runBlocking { session.developFrame("result-guard",1,"unindexed",emptyMap()) } }
        val path=root.resolve("results/unindexed.darkroom_negative.npz");val opticalBytes=Files.readAllBytes(path)
        assertEquals(0,library.developedResults().size);assertEquals(1,runs)
        fail=false
        val restored=FilmLibrary(index)
        assertThrows(CaptureRejected::class.java) { runBlocking { DevelopSession(restored,store,facade).developFrame("result-guard",1,"unindexed",emptyMap()) } }
        assertEquals(1,runs);assertArrayEquals(opticalBytes,Files.readAllBytes(path))
        assertArrayEquals(latentBytes,Files.readAllBytes(root.resolve(frame.latentPath!!)))
        DevelopSession(restored,store,facade).developFrame("result-guard",1,"new-id",emptyMap())
        assertEquals(2,runs);assertEquals(1,restored.developedResults().size)
    }

    private fun storage(): StorageAdapter = object : StorageAdapter by MemoryStorageAdapter() {
        override fun existsPrivate(relative: String) = Files.isRegularFile(root.resolve(relative))
        override fun rowScratchRoot() = root.resolve("scratch").toFile()
        override fun samePrivateLocation(a: String, b: String) = root.resolve(a).normalize() == root.resolve(b).normalize()
        override fun readPrivate(relative: String): ByteArray = error("whole-file read forbidden")
        override fun writePrivate(relative: String, bytes: ByteArray): Unit = error("whole-file write forbidden")
        override fun <T> readPrivate(relative: String, reader: (InputStream) -> T): T = root.resolve(relative).toFile().inputStream().use(reader)
        override fun writePrivate(relative: String, writer: (OutputStream) -> Unit) = writePrivate(relative, {}, writer)
        override fun writePrivate(relative: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit) {
            val path = root.resolve(relative)
            Files.createDirectories(path.parent)
            val pending = Files.createTempFile(path.parent, "pending-", ".tmp")
            try {
                pending.toFile().outputStream().use(writer)
                beforeCommit()
                Files.move(pending, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            } finally { Files.deleteIfExists(pending) }
        }
    }

    @Test fun realDngSamplesReachRowLatentAndRepeatedDevelopmentWithoutTouchingTheRawSource() = runBlocking {
        val raw=javaClass.getResourceAsStream("/fixtures/synthetic_bayer.dng")!!.use { it.readBytes() }
        val original=raw.copyOf()
        val library=FilmLibrary()
        library.loadRoll("raw",12,emptyMap())
        val store=MediumStore(storage());val facade=DefaultWorkflowFacade()
        val camera=ScriptedCameraAdapter(CameraCapabilities(supportsDng=true),
            CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,CameraXBufferFormat.RAW_SENSOR,4,4,dngBytes=raw))
        val frame=ShootSession(camera,library,store,facade).captureFrame("raw",1,StillCaptureRequest(preferRaw=true))
        val latent=store.readLatent(frame.latentPath!!)
        assertTrue(latent.arrays.getValue("developability").values.all { it > .6f })
        val record=latent.metadata["capture"] as Map<*, *>
        assertEquals("sensor_raw_dng",record["kind"])
        assertEquals(false,record["isp_processed"])
        assertEquals(DngRawDecoder.MODEL,record["source_encoding"])
        val before=Files.readAllBytes(root.resolve(frame.latentPath!!))
        library.seal("raw")
        val session=DevelopSession(library,store,facade)
        val first=session.developFrame("raw",1,"raw-first",emptyMap())
        session.developFrame("raw",1,"raw-second",emptyMap())
        assertEquals(record,store.readOptical(first.opticalPath).metadata["capture"])
        assertArrayEquals(before,Files.readAllBytes(root.resolve(frame.latentPath!!)))
        assertArrayEquals(original,raw)
        assertEquals(0,root.resolve("scratch").toFile().listFiles()!!.size)
    }

    @Test fun hdrAndSmitsReachActualRowExposureAndKeepProvenanceThroughDevelopment() = runBlocking {
        val spectrum = mapOf("model" to DataKernel.SMITS_EXPOSURE_MODEL_ID, "profile_id" to "synthetic_hdr_smits",
            "working_space" to DataKernel.CAPTURE_WORKING_SPACE, "wavelengths_nm" to listOf(400,500),
            "rgb_basis" to List(7){listOf(1,1)}, "layer_sensitivity" to List(3){listOf(1,1)}, "layer_gain" to listOf(.01,.01,.01))
        val library=FilmLibrary()
        library.loadRoll("hdr",12,mapOf("spectral_exposure_profile" to spectrum))
        val store=MediumStore(storage()); val facade=DefaultWorkflowFacade()
        val map=HdrGainMap(1,1,FloatArray(3){1f},FloatArray(3){1f},FloatArray(3){8f},FloatArray(3){1f},FloatArray(3),FloatArray(3))
        val camera=ScriptedCameraAdapter(CameraCapabilities(supportsUltraHdr=true),
            CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,CameraXBufferFormat.JPEG,3,5,
                encodedRgb=FloatArray(45){.5f},hdrGainMap=map))
        val frame=ShootSession(camera,library,store,facade).captureFrame("hdr",1,StillCaptureRequest(preferHdr=true))
        val latent=store.readLatent(frame.latentPath!!)
        assertTrue(latent.arrays.getValue("developability").values.all { it > .9f })
        val captured=latent.metadata["capture"] as Map<*, *>
        assertEquals("applied",captured["hdr_gainmap"])
        assertEquals("ultra_hdr_gainmap",captured["kind"])
        assertEquals(DataKernel.SMITS_EXPOSURE_MODEL_ID,(latent.metadata["spectral_exposure_profile"] as Map<*, *>)["model"])
        val before=Files.readAllBytes(root.resolve(frame.latentPath!!))
        library.seal("hdr")
        val result=DevelopSession(library,store,facade).developFrame("hdr",1,"hdr-result",emptyMap())
        assertEquals(captured,store.readOptical(result.opticalPath).metadata["capture"])
        assertArrayEquals(before,Files.readAllBytes(root.resolve(frame.latentPath!!)))
        assertEquals(0,root.resolve("scratch").toFile().listFiles()!!.size)
    }

    @Test fun cameraRowArchiveAndRepeatedDevelopmentsKeepTheSourceRoll() = runBlocking {
        val library = FilmLibrary()
        library.loadRoll("pocket", 12, emptyMap())
        val store = MediumStore(storage())
        val facade = DefaultWorkflowFacade()
        val camera = ScriptedCameraAdapter(still = CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,
            CameraXBufferFormat.JPEG, 37, 19, encodedRgb = FloatArray(37 * 19 * 3) { (it % 67) / 67f }))
        val frame = ShootSession(camera, library, store, facade).captureFrame("pocket", 1)
        assertEquals(FrameState.LATENT, frame.state)
        val latentPath = frame.latentPath!!
        val before = Files.readAllBytes(root.resolve(latentPath))
        val capture = store.readLatent(latentPath).metadata["capture"] as Map<*, *>
        assertEquals("display_referred_srgb", capture["kind"])
        library.seal("pocket")
        val session = DevelopSession(library, store, facade)
        val first = session.developFrame("pocket", 1, "first", mapOf("develop" to mapOf("time_min" to 2)))
        val second = session.developFrame("pocket", 1, "second", mapOf("develop" to mapOf("time_min" to 12)))
        assertEquals(2, library.developedResults().size)
        assertEquals(FrameState.LATENT, library.roll("pocket").frames[0].state)
        assertArrayEquals(before, Files.readAllBytes(root.resolve(latentPath)))
        assertFalse(store.readOptical(first.opticalPath).opticalDensityRgb.values.contentEquals(store.readOptical(second.opticalPath).opticalDensityRgb.values))
        assertEquals(0, root.resolve("scratch").toFile().listFiles()!!.size)
        assertEquals("pocket", (store.readOptical(second.opticalPath).metadata["frame_identity"] as Map<*, *>) ["roll_id"])
        val error = assertThrows(WorkflowException::class.java) { runBlocking {
            session.developFrame("pocket", 1, "cancelled", emptyMap(), WorkflowControl(cancelRequested = { true }))
        } }
        assertEquals("cancelled", error.code)
        assertEquals(2, library.developedResults().size)
        assertFalse(Files.exists(root.resolve("results/cancelled.darkroom_negative.npz")))
    }
}
