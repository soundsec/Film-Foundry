package com.filmfoundry.app.session

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.platform.CameraCapabilities
import com.filmfoundry.platform.CameraAdapter
import com.filmfoundry.platform.CameraXBufferFormat
import com.filmfoundry.platform.CameraXImageProxy
import com.filmfoundry.platform.CameraXUseCase
import com.filmfoundry.platform.CaptureNotReady
import com.filmfoundry.platform.CaptureRejected
import com.filmfoundry.platform.MediumStore
import com.filmfoundry.platform.MemoryStorageAdapter
import com.filmfoundry.platform.ScriptedCameraAdapter
import com.filmfoundry.platform.StillCaptureRequest
import com.filmfoundry.workflow.DefaultWorkflowFacade
import com.filmfoundry.workflow.FilmLibrary
import com.filmfoundry.workflow.FrameState
import com.filmfoundry.workflow.WorkflowControl
import com.filmfoundry.workflow.WorkflowException
import com.filmfoundry.workflow.WorkflowFacade
import com.filmfoundry.workflow.WorkflowRequest
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class ShootSessionTest {
    @Test
    fun invalidOrRepeatedFramesAreRejectedBeforeCaptureOrOverwrite() = runBlocking {
        val storage = MemoryStorageAdapter()
        val library = FilmLibrary()
        library.loadRoll("finite", 12, emptyMap())
        var captures = 0
        val delegate = ScriptedCameraAdapter(still = jpegStill())
        val camera = object : CameraAdapter by delegate {
            override fun captureStill(request: StillCaptureRequest) = delegate.captureStill(request).also { captures++ }
        }
        val session = ShootSession(camera, library, MediumStore(storage), DefaultWorkflowFacade())
        val first = session.captureFrame("finite", 1)
        val original = storage.readPrivate(first.latentPath!!)
        for (index in listOf(1, 0, 13)) {
            assertThrows(WorkflowException::class.java) { runBlocking { session.captureFrame("finite", index) } }
        }
        library.seal("finite")
        assertThrows(WorkflowException::class.java) { runBlocking { session.captureFrame("finite", 2) } }
        assertEquals(1, captures)
        assertArrayEquals(original, storage.readPrivate(first.latentPath!!))
        assertFalse(storage.existsPrivate("finite/frame_13.darkroom_latent.npz"))
    }

    @Test
    fun inFlightExposureBlocksOtherSessionsAndSealingUntilItsCommit() = runBlocking {
        val storage = MemoryStorageAdapter()
        val library = FilmLibrary()
        library.loadRoll("shared", 12, emptyMap())
        library.commitCameraShot("shared", 1, "previous.darkroom_latent.npz")
        val ready = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val delegate = DefaultWorkflowFacade()
        val paused = object : WorkflowFacade {
            override suspend fun run(request: WorkflowRequest) = run {
                ready.complete(Unit)
                release.await()
                delegate.run(request)
            }
        }
        val camera = ScriptedCameraAdapter(still = jpegStill())
        val first = ShootSession(camera, library, MediumStore(storage), paused)
        val second = ShootSession(camera, library, MediumStore(storage), delegate)
        val pending = async(start = CoroutineStart.UNDISPATCHED) { first.captureFrame("shared", 2) }
        ready.await()
        try {
            for (index in listOf(2, 3)) {
                assertThrows(WorkflowException::class.java) { runBlocking { second.captureFrame("shared", index) } }
            }
            assertThrows(WorkflowException::class.java) { library.seal("shared") }
            assertThrows(WorkflowException::class.java) {
                library.commitCameraShot("shared", 3, "direct.darkroom_latent.npz")
            }
        } finally { release.complete(Unit) }
        assertEquals(FrameState.LATENT, pending.await().state)
        assertEquals(FrameState.LATENT, second.captureFrame("shared", 3).state)
        assertEquals(3, library.roll("shared").latentCount())
    }

    @Test
    fun failedOrPreCancelledCaptureDoesNotSpendAFrameOrLeaveTheRollBusy() = runBlocking {
        val library = FilmLibrary()
        library.loadRoll("retry", 12, emptyMap())
        val store = MediumStore(MemoryStorageAdapter())
        val facade = DefaultWorkflowFacade()
        val broken = ShootSession(ScriptedCameraAdapter(), library, store, facade)
        assertThrows(CaptureNotReady::class.java) { runBlocking { broken.captureFrame("retry", 1) } }
        assertEquals(0, library.roll("retry").latentCount())
        val good = ShootSession(ScriptedCameraAdapter(still = jpegStill()), library, store, facade)
        val cancelled = assertThrows(WorkflowException::class.java) { runBlocking {
            good.captureFrame("retry", 1, control = WorkflowControl(cancelRequested = { true }))
        } }
        assertEquals("cancelled", cancelled.code)
        assertEquals(FrameState.LATENT, good.captureFrame("retry", 1).state)
    }

    @Test
    fun cameraStillExposesAndCommitsALatent() = runBlocking {
        val storage = MemoryStorageAdapter()
        val library = FilmLibrary()
        library.loadRoll("pocket", 12, emptyMap())
        val session = ShootSession(
            camera = ScriptedCameraAdapter(still = jpegStill()),
            library = library,
            store = MediumStore(storage),
            facade = DefaultWorkflowFacade(),
        )
        val frame = session.captureFrame("pocket", 1)
        assertEquals(FrameState.LATENT, frame.state)
        assertEquals("pocket/frame_01.darkroom_latent.npz", frame.latentPath)
        val latent = MediumStore(storage).readLatent(frame.latentPath!!)
        val capture = latent.metadata[DataKernel.CAPTURE_OBJECT_KEY] as Map<*, *>
        assertEquals("display_referred_srgb", capture["kind"])
        assertEquals(false, capture["scene_referred_claim"])
    }

    @Test
    fun previewBufferCannotBeShot() {
        val library = FilmLibrary()
        library.loadRoll("pocket", 12, emptyMap())
        val session = ShootSession(
            camera = ScriptedCameraAdapter(preview = analysisPreview(), still = analysisPreview()),
            library = library,
            store = MediumStore(MemoryStorageAdapter()),
            facade = DefaultWorkflowFacade(),
        )
        assertThrows(CaptureRejected::class.java) {
            runBlocking { session.captureFrame("pocket", 1) }
        }
    }

    @Test
    fun preferRawWithoutDngDoesNotBecomeJpeg() {
        val library = FilmLibrary()
        library.loadRoll("pocket", 12, emptyMap())
        val session = ShootSession(
            camera = ScriptedCameraAdapter(
                capabilities = CameraCapabilities(supportsDng = false),
                still = jpegStill(),
            ),
            library = library,
            store = MediumStore(MemoryStorageAdapter()),
            facade = DefaultWorkflowFacade(),
        )
        assertThrows(CaptureRejected::class.java) {
            runBlocking { session.captureFrame("pocket", 1, StillCaptureRequest(preferRaw = true)) }
        }
        assertEquals(FrameState.EMPTY, library.roll("pocket").frames[0].state)
    }

    @Test
    fun malformedRawStillIsRejectedBeforeCommit() {
        val library = FilmLibrary()
        library.loadRoll("pocket", 12, emptyMap())
        val session = ShootSession(
            camera = ScriptedCameraAdapter(
                capabilities = CameraCapabilities(supportsDng = true),
                still = CameraXImageProxy(
                    useCase = CameraXUseCase.IMAGE_CAPTURE,
                    format = CameraXBufferFormat.RAW_SENSOR,
                    width = 1,
                    height = 1,
                    dngBytes = byteArrayOf(1, 2, 3),
                ),
            ),
            library = library,
            store = MediumStore(MemoryStorageAdapter()),
            facade = DefaultWorkflowFacade(),
        )
        assertThrows(CaptureRejected::class.java) {
            runBlocking { session.captureFrame("pocket", 1, StillCaptureRequest(preferRaw = true)) }
        }
        assertTrue(library.roll("pocket").frames[0].latentPath == null)
    }

    private fun jpegStill(): CameraXImageProxy {
        return CameraXImageProxy(
            useCase = CameraXUseCase.IMAGE_CAPTURE,
            format = CameraXBufferFormat.JPEG,
            width = 2,
            height = 2,
            encodedRgb = FloatArray(12) { 0.5f },
        )
    }

    private fun analysisPreview(): CameraXImageProxy {
        return CameraXImageProxy(
            useCase = CameraXUseCase.PREVIEW,
            format = CameraXBufferFormat.RGBA_8888,
            width = 1,
            height = 1,
            encodedRgb = floatArrayOf(0.4f, 0.4f, 0.4f),
        )
    }
}
