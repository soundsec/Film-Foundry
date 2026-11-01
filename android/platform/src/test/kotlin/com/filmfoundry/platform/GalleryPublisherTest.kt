package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.workflow.WorkflowException
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class GalleryPublisherTest {
    @Test
    fun scannerPngBecomesMediaStoreInsert() {
        val bytes = byteArrayOf(137.toByte(), 80, 78, 71)
        val insert = GalleryPublisher.prepare("frame.png", "image/png", bytes)
        assertEquals(DataKernel.PHONE_MEDIASTORE_COLLECTION, insert.collection)
        assertEquals(DataKernel.PHONE_MEDIASTORE_RELATIVE_PATH, insert.relativePath)
        assertEquals("Pictures/FilmFoundry/frame.png", insert.publicPath())
        assertEquals(DataKernel.PHONE_GALLERY_ITEM_KIND, insert.kind)
        assertTrue(insert.isPending)
        val storage = MemoryStorageAdapter()
        storage.publishGallery("frame.png", "image/png", bytes)
        assertEquals(false, storage.galleryInserts.single().isPending)
        assertTrue(storage.galleryBytes("frame.png").contentEquals(bytes))
    }

    @Test
    fun npzJsonFfc2AndViewerStayOut() {
        val png = byteArrayOf(1, 2, 3)
        assertThrows(WorkflowException::class.java) {
            GalleryPublisher.prepare("frame.darkroom_negative.npz", "image/png", png)
        }
        assertThrows(WorkflowException::class.java) {
            GalleryPublisher.prepare(DataKernel.PHONE_LIBRARY_INDEX_FILENAME, "application/json", png)
        }
        assertThrows(WorkflowException::class.java) {
            GalleryPublisher.prepare(DataKernel.PHONE_RECIPE_CATALOG_FILENAME, "application/json", png)
        }
        assertThrows(WorkflowException::class.java) {
            GalleryPublisher.prepare("FFC2:abc", "image/png", png)
        }
        val frame = ViewerPreviewFrame(1, 1, floatArrayOf(1f, 1f, 1f), ViewerPreviewEncoding.LINEAR)
        val previewError = assertThrows(WorkflowException::class.java) {
            GalleryPublisher.refusePreview(frame)
        }
        assertTrue(previewError.message!!.contains("viewer preview"))
        val stillError = assertThrows(WorkflowException::class.java) {
            GalleryPublisher.refuseCapture(EncodedRgbCapture(1, 1, floatArrayOf(0.2f, 0.2f, 0.2f)))
        }
        assertTrue(stillError.message!!.contains("latent"))
    }

    @Test
    fun emptyObservationFailsClosed() {
        assertThrows(WorkflowException::class.java) {
            GalleryPublisher.prepare("frame.png", "image/png", byteArrayOf())
        }
    }
}
