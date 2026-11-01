package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel

enum class CaptureKind(val wireName: String) {
    DISPLAY_REFERRED_SRGB("display_referred_srgb"),
    ULTRA_HDR_GAINMAP("ultra_hdr_gainmap"),
    SENSOR_RAW_DNG("sensor_raw_dng"),
    LINEAR_WORKING_RGB("linear_working_rgb"),
    EXPOSURE_STACK("exposure_stack"),
    ;

    companion object {
        fun fromWire(name: String): CaptureKind {
            return entries.firstOrNull { it.wireName == name }
                ?: throw CaptureRejected("unknown capture kind: $name")
        }
    }
}

open class CaptureException(message: String) : RuntimeException(message)

class CaptureNotReady(message: String) : CaptureException(message)

class CaptureRejected(message: String) : CaptureException(message)

data class CaptureRecord(
    val kind: CaptureKind,
    val ispProcessed: Boolean,
    val workingSpace: String = DataKernel.CAPTURE_WORKING_SPACE,
    val sourceEncoding: String,
    val bitDepth: Int? = null,
    val hdrGainmap: String = "absent",
    val sceneReferredClaim: Boolean = false,
    val orientationDegrees: Int = 0,
    val exposureTimeNs: Long? = null,
    val iso: Int? = null,
    val frameCount: Int = 1,
) {
    init {
        if (kind.wireName !in DataKernel.CAPTURE_KIND_VALUES) {
            throw CaptureRejected("unknown capture kind: ${kind.wireName}")
        }
        if (hdrGainmap !in DataKernel.CAPTURE_HDR_GAINMAP_VALUES) {
            throw CaptureRejected("unknown hdr_gainmap value: $hdrGainmap")
        }
        if (frameCount <= 0) {
            throw CaptureRejected("frame_count must be positive.")
        }
    }

    fun toMetadata(): Map<String, Any?> {
        val payload = linkedMapOf<String, Any?>(
            "kind" to kind.wireName,
            "isp_processed" to ispProcessed,
            "working_space" to workingSpace,
            "source_encoding" to sourceEncoding,
            "hdr_gainmap" to hdrGainmap,
            "scene_referred_claim" to sceneReferredClaim,
            "frame_count" to frameCount,
        )
        bitDepth?.let { payload["bit_depth"] = it }
        exposureTimeNs?.let { payload["exposure_time_ns"] = it }
        iso?.let { payload["iso"] = it }
        return payload
    }
}

data class WorkingExposure(
    val width: Int,
    val height: Int,
    val linearRgb: FloatArray,
    val record: CaptureRecord,
) {
    init {
        if (width <= 0 || height <= 0) {
            throw CaptureRejected("working exposure dimensions must be positive.")
        }
        val expected = Math.multiplyExact(Math.multiplyExact(width, height), 3)
        if (linearRgb.size != expected) {
            throw CaptureRejected("linear RGB size ${linearRgb.size} does not match $expected.")
        }
    }
}

sealed class CaptureSource {
    abstract val width: Int
    abstract val height: Int
}

data class EncodedRgbCapture(
    override val width: Int,
    override val height: Int,
    val encodedRgb: FloatArray,
    val kind: CaptureKind = CaptureKind.DISPLAY_REFERRED_SRGB,
    val bitDepth: Int = 8,
    val ispProcessed: Boolean = true,
    val gainMap: FloatArray? = null,
    val orientationDegrees: Int = 0,
    val exposureTimeNs: Long? = null,
    val iso: Int? = null,
    val hdrGainMap: HdrGainMap? = null,
) : CaptureSource() {
    init {
        requireRgb(width, height, encodedRgb, "encoded RGB")
        gainMap?.let { requireRgb(width, height, it, "HDR gain map") }
        if (kind != CaptureKind.DISPLAY_REFERRED_SRGB && kind != CaptureKind.ULTRA_HDR_GAINMAP) {
            throw CaptureRejected("encoded RGB capture kind must be display-referred or ultra_hdr_gainmap.")
        }
        if (kind == CaptureKind.DISPLAY_REFERRED_SRGB && (gainMap != null || hdrGainMap != null)) {
            throw CaptureRejected("gain map belongs on ultra_hdr_gainmap, not display_referred_srgb.")
        }
    }
}

data class LinearRgbCapture(
    override val width: Int,
    override val height: Int,
    val linearRgb: FloatArray,
    val originKind: CaptureKind = CaptureKind.LINEAR_WORKING_RGB,
    val claimedSceneReferred: Boolean = false,
    val ispProcessed: Boolean = false,
    val bitDepth: Int? = 32,
    val orientationDegrees: Int = 0,
    val exposureTimeNs: Long? = null,
    val iso: Int? = null,
) : CaptureSource() {
    init {
        requireRgb(width, height, linearRgb, "linear RGB")
        if (originKind != CaptureKind.LINEAR_WORKING_RGB) {
            throw CaptureRejected("linear RGB capture origin must be linear_working_rgb.")
        }
    }
}

data class DngCapture(
    val bytes: ByteArray,
    override val width: Int = 0,
    override val height: Int = 0,
) : CaptureSource() {
    init {
        if (bytes.isEmpty()) {
            throw CaptureRejected("DNG payload is empty.")
        }
    }

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is DngCapture) return false
        return width == other.width && height == other.height && bytes.contentEquals(other.bytes)
    }

    override fun hashCode(): Int {
        var result = bytes.contentHashCode()
        result = 31 * result + width
        result = 31 * result + height
        return result
    }
}

data class PreviewCapture(
    override val width: Int,
    override val height: Int,
) : CaptureSource()

data class ExposureStackCapture(
    val frames: List<CaptureSource>,
    val relativeEv: FloatArray,
) : CaptureSource() {
    init {
        if (frames.isEmpty()) {
            throw CaptureRejected("exposure stack is empty.")
        }
        if (relativeEv.size != frames.size) {
            throw CaptureRejected("relativeEv length must match the frame count.")
        }
    }

    override val width: Int get() = frames.first().width
    override val height: Int get() = frames.first().height
}

object CaptureFiles {
    val DISPLAY_EXTENSIONS = setOf(
        "jpg", "jpeg", "png", "tif", "tiff", "bmp", "webp", "heic", "heif",
    )
    val RAW_EXTENSIONS = setOf(
        "dng", "cr2", "cr3", "nef", "arw", "rw2", "orf", "raf", "pef",
    )

    fun kindForFileName(name: String): CaptureKind {
        val ext = name.substringAfterLast('.', "").lowercase()
        return when {
            ext in RAW_EXTENSIONS -> CaptureKind.SENSOR_RAW_DNG
            ext in DISPLAY_EXTENSIONS -> CaptureKind.DISPLAY_REFERRED_SRGB
            else -> throw CaptureRejected("unsupported capture file '.$ext'")
        }
    }
}

private fun requireRgb(width: Int, height: Int, values: FloatArray, label: String) {
    if (width <= 0 || height <= 0) {
        throw CaptureRejected("$label dimensions must be positive.")
    }
    val expected = Math.multiplyExact(Math.multiplyExact(width, height), 3)
    if (values.size != expected) {
        throw CaptureRejected("$label size ${values.size} does not match $expected.")
    }
}
