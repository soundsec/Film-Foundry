package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.workflow.GalleryExportPolicy
import com.filmfoundry.workflow.WorkflowException

/**
 * MediaStore insert for an explicit light-table projection. NPZ, FFC2,
 * library index, scans, and in-app viewer frames never become gallery rows.
 */
data class MediaStoreInsert(
    val collection: String = DataKernel.PHONE_MEDIASTORE_COLLECTION,
    val relativePath: String = DataKernel.PHONE_MEDIASTORE_RELATIVE_PATH,
    val displayName: String,
    val mimeType: String,
    val kind: String = DataKernel.PHONE_GALLERY_ITEM_KIND,
    val isPending: Boolean = true,
    val byteCount: Int,
) {
    init {
        if (collection != DataKernel.PHONE_MEDIASTORE_COLLECTION) {
            throw WorkflowException("invalid_request", "gallery collection must be MediaStore Images")
        }
        if (relativePath != DataKernel.PHONE_MEDIASTORE_RELATIVE_PATH) {
            throw WorkflowException("invalid_request", "gallery relative path is Pictures/FilmFoundry/")
        }
        if (kind != DataKernel.PHONE_GALLERY_ITEM_KIND) {
            throw WorkflowException("invalid_request", "gallery items must be light-table projections")
        }
    }

    fun publicPath(): String = relativePath + displayName
}

object GalleryPublisher {
    /** byteCount=0 denotes unknown length before the stream is written, not an accepted empty image. */
    fun prepareStream(displayName: String,mimeType: String): MediaStoreInsert {
        GalleryExportPolicy.requireScannerCommit(displayName,mimeType)
        return MediaStoreInsert(displayName=displayName,mimeType=mimeType,byteCount=0)
    }
    fun prepare(displayName: String, mimeType: String, bytes: ByteArray): MediaStoreInsert {
        if (bytes.isEmpty()) {
            throw WorkflowException("invalid_request", "scanner observation payload is empty")
        }
        GalleryExportPolicy.requireScannerCommit(displayName, mimeType)
        return MediaStoreInsert(
            displayName = displayName,
            mimeType = mimeType,
            byteCount = bytes.size,
        )
    }

    fun refusePreview(frame: ViewerPreviewFrame): Nothing {
        throw WorkflowException(
            "invalid_request",
            "viewer preview illuminant does not enter the system gallery",
        )
    }

    fun refuseCapture(source: CaptureSource): Nothing {
        throw WorkflowException(
            "invalid_request",
            "camera stills freeze as latent NPZ; they do not enter the system gallery",
        )
    }
}
