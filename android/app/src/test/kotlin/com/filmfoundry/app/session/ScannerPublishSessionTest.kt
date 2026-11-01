package com.filmfoundry.app.session

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.platform.CameraXBufferFormat
import com.filmfoundry.platform.CameraXImageProxy
import com.filmfoundry.platform.CameraXUseCase
import com.filmfoundry.platform.MemoryStorageAdapter
import com.filmfoundry.platform.ScriptedCameraAdapter
import com.filmfoundry.workflow.DevelopedResult
import com.filmfoundry.workflow.ObservationDesk
import com.filmfoundry.workflow.ViewerIlluminant
import com.filmfoundry.workflow.WorkflowException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class ScannerPublishSessionTest {
    @Test fun streamingProjectionCommitsOnlyAfterTheWriterSucceeds() {
        val storage=MemoryStorageAdapter();val desk=ObservationDesk();val session=ScannerPublishSession(desk,storage)
        session.commitStream(result,"projection.png","image/png") { it.write(byteArrayOf(1,2,3)) }
        assertTrue(storage.galleryBytes("projection.png").contentEquals(byteArrayOf(1,2,3)))
        assertEquals(1,desk.galleryItems().size)
        assertThrows(java.io.IOException::class.java) {
            session.commitStream(result,"failed.png","image/png") { it.write(9);throw java.io.IOException("write failed") }
        }
        assertEquals(1,desk.galleryItems().size);assertEquals(1,storage.galleryInserts.size)
        assertThrows(WorkflowException::class.java) {
            session.commitStream(result,"source.darkroom_negative.npz","image/png") { error("must not write") }
        }
    }
    private val result = DevelopedResult(
        resultId = "r1",
        sourceRollId = "roll",
        sourceFrameIndex = 1,
        opticalPath = "frame.darkroom_negative.npz",
        developRecipe = emptyMap(),
    )

    @Test
    fun scannerPngEntersMediaStoreAndDesk() {
        val storage = MemoryStorageAdapter()
        val desk = ObservationDesk()
        val session = ScannerPublishSession(desk, storage)
        val bytes = byteArrayOf(137.toByte(), 80, 78, 71)
        val (item, insert) = session.commit(result, "scan.png", "image/png", bytes)
        assertEquals(DataKernel.PHONE_GALLERY_ITEM_KIND, item.kind)
        assertEquals("Pictures/FilmFoundry/scan.png", insert.publicPath())
        assertEquals(false, insert.isPending)
        assertTrue(storage.galleryBytes("scan.png").contentEquals(bytes))
        assertEquals(1, desk.galleryItems().size)
    }

    @Test
    fun viewerPreviewCannotBePublished() {
        val camera = ScriptedCameraAdapter(
            preview = CameraXImageProxy(
                useCase = CameraXUseCase.PREVIEW,
                format = CameraXBufferFormat.RGBA_8888,
                width = 1,
                height = 1,
                encodedRgb = floatArrayOf(0.9f, 0.2f, 0.1f),
            ),
        )
        val desk = ObservationDesk()
        desk.openViewer(result)
        val lamp = ViewerLampSession(camera)
        val viewer = lamp.apply(desk, "r1")
        assertEquals(ViewerIlluminant.SCENE_CAMERA, viewer.illuminant)
        assertEquals("scene_camera", viewer.illuminant.wireName)
        val session = ScannerPublishSession(desk, MemoryStorageAdapter())
        assertThrows(WorkflowException::class.java) {
            session.refusePreview(camera.previewForViewer())
        }
        assertThrows(WorkflowException::class.java) {
            desk.publishViewerToGallery("r1")
        }
        assertEquals(0, desk.galleryItems().size)
    }

    @Test
    fun opticalNpzDoesNotEnterGallery() {
        val session = ScannerPublishSession(ObservationDesk(), MemoryStorageAdapter())
        assertThrows(WorkflowException::class.java) {
            session.commit(
                result,
                "frame.darkroom_negative.npz",
                "image/png",
                byteArrayOf(1, 2, 3),
            )
        }
    }
}
