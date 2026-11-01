package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.io.File

data class StillCaptureRequest(
    val preferRaw: Boolean = false,
    val preferHdr: Boolean = false,
)

data class CameraCapabilities(
    val maxStillWidth: Int? = null,
    val maxStillHeight: Int? = null,
    val supportsDng: Boolean = false,
    val supportsUltraHdr: Boolean = false,
    val supportsUnprocessedRaw: Boolean = false,
    val previewIsNotCapture: Boolean = true,
    val previewMayIlluminateViewer: Boolean = true,
)

interface CameraAdapter {
    fun capabilities(): CameraCapabilities
    /** True only when each capture transfers private buffers which the caller may consume. */
    fun transfersStillOwnership(): Boolean = false
    fun captureStill(request: StillCaptureRequest = StillCaptureRequest()): CaptureSource
    fun previewForViewer(): ViewerPreviewFrame
}

interface StorageAdapter {
    fun privateDir(): String
    fun writePrivate(relative: String, bytes: ByteArray)
    fun readPrivate(relative: String): ByteArray
    /** Legacy adapters may buffer; file-backed implementations must override these callbacks. */
    fun <T> readPrivate(relative: String, reader: (InputStream) -> T): T =
        ByteArrayInputStream(readPrivate(relative)).use(reader)
    fun writePrivate(relative: String, writer: (OutputStream) -> Unit) {
        val bytes = ByteArrayOutputStream()
        writer(bytes)
        writePrivate(relative, bytes.toByteArray())
    }
    fun writePrivate(relative: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit) {
        writePrivate(relative) { output -> writer(output); beforeCommit() }
    }
    fun rowScratchRoot(): File? = null
    fun privateVersion(relative: String): com.filmfoundry.workflow.RowMediumVersion? = null
    fun samePrivateLocation(a: String, b: String): Boolean = File(a).toPath().normalize() == File(b).toPath().normalize()
    fun existsPrivate(relative: String): Boolean
    fun deletePrivate(relative: String)
    fun exportUserVisible(privatePath: String, displayName: String)
    fun publishGallery(displayName: String, mimeType: String, bytes: ByteArray)
    /** File-backed publishers must stream; legacy adapters fail rather than buffer an unbounded image. */
    fun publishGalleryStream(displayName: String,mimeType: String,check: ()->Unit={},writer: (OutputStream)->Unit) {
        throw CaptureNotReady("Streaming gallery publication is not supported by this storage adapter")
    }
}

interface SensorAdapter {
    // Stirring events are reserved. Do not wire them into develop in v0.
}

class CameraAdapterNotReady : CameraAdapter {
    override fun capabilities(): CameraCapabilities {
        return CameraCapabilities(
            previewIsNotCapture = true,
            previewMayIlluminateViewer = true,
        )
    }

    override fun captureStill(request: StillCaptureRequest): CaptureSource {
        throw CaptureNotReady(
            "CameraX adapter is not wired. Do not substitute a preview frame or a JPEG thumbnail for RAW.",
        )
    }

    override fun previewForViewer(): ViewerPreviewFrame {
        throw CaptureNotReady(
            "CameraX preview is not wired. Inject ViewerPreviewFrame in tests; do not treat preview as a still.",
        )
    }
}

class StorageAdapterNotReady : StorageAdapter {
    override fun privateDir(): String {
        throw CaptureNotReady("storage adapter is not wired.")
    }

    override fun writePrivate(relative: String, bytes: ByteArray) {
        throw CaptureNotReady("storage adapter is not wired.")
    }

    override fun readPrivate(relative: String): ByteArray {
        throw CaptureNotReady("storage adapter is not wired.")
    }

    override fun existsPrivate(relative: String): Boolean {
        throw CaptureNotReady("storage adapter is not wired.")
    }

    override fun deletePrivate(relative: String) {
        throw CaptureNotReady("storage adapter is not wired.")
    }

    override fun exportUserVisible(privatePath: String, displayName: String) {
        throw CaptureNotReady("storage adapter is not wired.")
    }

    override fun publishGallery(displayName: String, mimeType: String, bytes: ByteArray) {
        throw CaptureNotReady("gallery publisher is not wired.")
    }
}

class MemoryStorageAdapter : StorageAdapter {
    private val files = linkedMapOf<String, ByteArray>()
    val galleryInserts = mutableListOf<MediaStoreInsert>()

    override fun privateDir(): String = "memory"

    override fun writePrivate(relative: String, bytes: ByteArray) {
        files[relative] = bytes.copyOf()
    }

    override fun readPrivate(relative: String): ByteArray {
        return files[relative]?.copyOf()
            ?: throw CaptureRejected("private file not found: $relative")
    }

    override fun existsPrivate(relative: String): Boolean {
        return relative in files
    }

    override fun deletePrivate(relative: String) {
        files.remove(relative)
    }

    override fun exportUserVisible(privatePath: String, displayName: String) {
        val bytes = readPrivate(privatePath)
        files["export/$displayName"] = bytes
    }

    override fun publishGallery(displayName: String, mimeType: String, bytes: ByteArray) {
        val insert = GalleryPublisher.prepare(displayName, mimeType, bytes)
        galleryInserts.add(insert.copy(isPending = false))
        files["gallery/${insert.publicPath()}"] = bytes.copyOf()
    }

    fun galleryBytes(displayName: String): ByteArray {
        return files["gallery/${DataKernel.PHONE_MEDIASTORE_RELATIVE_PATH}$displayName"]?.copyOf()
            ?: throw CaptureRejected("gallery file not found: $displayName")
    }

    override fun publishGalleryStream(displayName: String,mimeType: String,check: ()->Unit,writer: (OutputStream)->Unit) {
        GalleryPublisher.prepareStream(displayName,mimeType);check()
        val output=ByteArrayOutputStream();writer(output);check()
        publishGallery(displayName,mimeType,output.toByteArray()) // In-memory fixture only.
    }
}
