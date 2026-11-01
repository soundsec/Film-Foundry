package com.filmfoundry.app.session

import com.filmfoundry.platform.CameraAdapter
import com.filmfoundry.platform.GalleryPublisher
import com.filmfoundry.platform.MediaStoreInsert
import com.filmfoundry.platform.StorageAdapter
import com.filmfoundry.platform.ViewerIlluminantSampler
import com.filmfoundry.platform.ViewerPreviewFrame
import com.filmfoundry.workflow.DevelopedResult
import com.filmfoundry.workflow.GalleryItem
import com.filmfoundry.workflow.ObservationDesk
import com.filmfoundry.workflow.ViewerSession

class ViewerLampSession(
    private val camera: CameraAdapter,
) {
    fun sampleScene() = ViewerIlluminantSampler.sample(camera.previewForViewer())

    fun apply(desk: ObservationDesk, resultId: String): ViewerSession {
        return desk.illuminateWithScene(resultId, sampleScene())
    }
}

class ScannerPublishSession(
    private val desk: ObservationDesk,
    private val storage: StorageAdapter,
) {
    fun commitStream(result: DevelopedResult,displayName: String,mimeType: String,
        control: com.filmfoundry.workflow.WorkflowControl=com.filmfoundry.workflow.WorkflowControl(),
        writer: (java.io.OutputStream)->Unit): GalleryItem {
        GalleryPublisher.prepareStream(displayName,mimeType);control.checkCancel()
        storage.publishGalleryStream(displayName,mimeType,control::checkCancel,writer)
        return desk.commitProjectionObservation(result,displayName,mimeType)
    }
    fun commit(
        result: DevelopedResult,
        displayName: String,
        mimeType: String,
        bytes: ByteArray,
    ): Pair<GalleryItem, MediaStoreInsert> {
        val insert = GalleryPublisher.prepare(displayName, mimeType, bytes)
        storage.publishGallery(displayName, mimeType, bytes)
        val item = desk.commitProjectionObservation(result, displayName, mimeType)
        return item to insert.copy(isPending = false)
    }

    fun refusePreview(frame: ViewerPreviewFrame): Nothing {
        GalleryPublisher.refusePreview(frame)
    }
}
