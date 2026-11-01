package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel

/**
 * CameraX-shaped packets without androidx types. [CameraXImageProxies]
 * copies ImageProxy planes into these values, then this bridge decides
 * still vs viewer lamp. Kernel never sees CameraX.
 */
enum class CameraXUseCase(val wireName: String) {
    IMAGE_CAPTURE("image_capture"),
    PREVIEW("preview"),
    IMAGE_ANALYSIS("image_analysis"),
    VIDEO_CAPTURE("video_capture"),
}

enum class CameraXBufferFormat(val wireName: String) {
    JPEG("jpeg"),
    YUV_420_888("yuv_420_888"),
    RAW_SENSOR("raw_sensor"),
    RGBA_8888("rgba_8888"),
}

data class Yuv420888(
    val width: Int,
    val height: Int,
    val y: ByteArray,
    val u: ByteArray,
    val v: ByteArray,
    val yRowStride: Int,
    val uRowStride: Int,
    val vRowStride: Int,
    val uPixelStride: Int = 1,
    val vPixelStride: Int = 1,
) {
    init {
        if (width <= 0 || height <= 0) {
            throw CaptureRejected("YUV preview dimensions must be positive.")
        }
        if (yRowStride < width || uPixelStride <= 0 || vPixelStride <= 0) {
            throw CaptureRejected("YUV_420_888 strides are invalid.")
        }
        if (y.size < yRowStride * (height - 1) + width) {
            throw CaptureRejected("YUV luma plane is short.")
        }
    }
}

data class CameraXImageProxy(
    val useCase: CameraXUseCase,
    val format: CameraXBufferFormat,
    val width: Int,
    val height: Int,
    val rotationDegrees: Int = 0,
    val encodedRgb: FloatArray? = null,
    val jpegBytes: ByteArray? = null,
    val yuv: Yuv420888? = null,
    val dngBytes: ByteArray? = null,
    val gainMap: FloatArray? = null,
    val exposureTimeNs: Long? = null,
    val iso: Int? = null,
    val hdrGainMap: HdrGainMap? = null,
) {
    init {
        if (width <= 0 || height <= 0) {
            throw CaptureRejected("CameraX buffer dimensions must be positive.")
        }
        encodedRgb?.let { requireRgb(width, height, it, "CameraX encoded RGB") }
        gainMap?.let { requireRgb(width, height, it, "CameraX gain map") }
        if (encodedRgb == null && jpegBytes == null && yuv == null && dngBytes == null) {
            throw CaptureRejected("CameraX buffer has no image payload.")
        }
    }
}

object CameraXCaptureBridge {
    fun still(
        proxy: CameraXImageProxy,
        request: StillCaptureRequest,
        capabilities: CameraCapabilities,
    ): CaptureSource {
        if (proxy.useCase.wireName !in DataKernel.PHONE_CAMERAX_STILL_USE_CASES) {
            throw CaptureRejected("preview and analysis buffers cannot fill a roll.")
        }
        if (request.preferRaw && !capabilities.supportsDng) {
            throw CaptureRejected("preferRaw requires DNG; do not silently capture JPEG.")
        }
        if (request.preferRaw && proxy.format != CameraXBufferFormat.RAW_SENSOR) {
            throw CaptureRejected("preferRaw requires a RAW_SENSOR still, not JPEG.")
        }
        if (!request.preferRaw && proxy.format == CameraXBufferFormat.RAW_SENSOR) {
            throw CaptureRejected("JPEG ImageCapture session produced RAW_SENSOR.")
        }
        return when (proxy.format) {
            CameraXBufferFormat.RAW_SENSOR -> {
                val bytes = proxy.dngBytes ?: throw CaptureRejected("RAW_SENSOR still is missing DNG bytes.")
                DngCapture(bytes, proxy.width, proxy.height)
            }
            CameraXBufferFormat.JPEG -> encodedStill(proxy, request)
            CameraXBufferFormat.YUV_420_888 -> {
                if (request.preferHdr) throw CaptureRejected("Ultra HDR requires a decoded SDR base and gain-map parameters, not a YUV still.")
                val yuv = proxy.yuv ?: throw CaptureRejected("ImageCapture YUV_420_888 is missing planes.")
                EncodedRgbCapture(
                    width = proxy.width,
                    height = proxy.height,
                    encodedRgb = Yuv420.toEncodedRgb(yuv),
                    kind = CaptureKind.DISPLAY_REFERRED_SRGB,
                    ispProcessed = true,
                    exposureTimeNs = proxy.exposureTimeNs,
                    iso = proxy.iso,
                )
            }
            CameraXBufferFormat.RGBA_8888 -> encodedStill(proxy, request)
        }
    }

    fun preview(proxy: CameraXImageProxy,borrowedRgb: FloatArray?=null): ViewerPreviewFrame {
        if (proxy.useCase.wireName !in DataKernel.PHONE_CAMERAX_PREVIEW_USE_CASES) {
            throw CaptureRejected("ImageCapture stills fill rolls; viewer lamps use preview or analysis.")
        }
        if (proxy.format == CameraXBufferFormat.RAW_SENSOR) {
            throw CaptureRejected("RAW_SENSOR cannot illuminate the viewer.")
        }
        val rgb = proxy.encodedRgb
            ?: proxy.yuv?.let { Yuv420.toEncodedRgb(it,borrowedRgb) }
            ?: throw CaptureNotReady(
                "JPEG preview bytes need a host decoder before lighting the viewer.",
            )
        return ViewerPreviewFrame(
            width = proxy.width,
            height = proxy.height,
            rgb = rgb,
            encoding = ViewerPreviewEncoding.DISPLAY_SRGB,
            rotationDegrees = proxy.rotationDegrees,
        )
    }

    private fun encodedStill(proxy: CameraXImageProxy, request: StillCaptureRequest): CaptureSource {
        val rgb = proxy.encodedRgb ?: throw CaptureNotReady(
            "JPEG still bytes need a host decoder before expose; do not treat the JPEG file as a latent.",
        )
        if (request.preferHdr && proxy.hdrGainMap == null) {
            throw CaptureNotReady("Ultra HDR requires decoded gain samples and reconstruction parameters; no silent SDR fallback.")
        }
        val ultraHdr = request.preferHdr && proxy.hdrGainMap != null
        return EncodedRgbCapture(
            width = proxy.width,
            height = proxy.height,
            encodedRgb = rgb,
            kind = if (ultraHdr) CaptureKind.ULTRA_HDR_GAINMAP else CaptureKind.DISPLAY_REFERRED_SRGB,
            ispProcessed = true,
            gainMap = if (ultraHdr) proxy.gainMap else null,
            exposureTimeNs = proxy.exposureTimeNs,
            iso = proxy.iso,
            hdrGainMap = if (ultraHdr) proxy.hdrGainMap else null,
        )
    }
}

/**
 * Test stand-in for a CameraX adapter. Production [CameraXCameraAdapter]
 * fills [CameraXImageProxy] from ImageProxy and calls the same bridge.
 */
class ScriptedCameraAdapter(
    private val capabilities: CameraCapabilities = CameraCapabilities(
        previewIsNotCapture = true,
        previewMayIlluminateViewer = true,
    ),
    private val still: CameraXImageProxy? = null,
    private val preview: CameraXImageProxy? = null,
) : CameraAdapter {
    override fun capabilities(): CameraCapabilities = capabilities

    override fun captureStill(request: StillCaptureRequest): CaptureSource {
        val proxy = still ?: throw CaptureNotReady(
            "CameraX adapter is not wired. Do not substitute a preview frame or a JPEG thumbnail for RAW.",
        )
        return CameraXCaptureBridge.still(proxy, request, capabilities)
    }

    override fun previewForViewer(): ViewerPreviewFrame {
        val proxy = preview ?: throw CaptureNotReady(
            "CameraX preview is not wired. Inject ViewerPreviewFrame in tests; do not treat preview as a still.",
        )
        return CameraXCaptureBridge.preview(proxy)
    }
}

object Yuv420 {
    fun toEncodedRgb(yuv: Yuv420888,output: FloatArray?=null): FloatArray {
        val size=Math.multiplyExact(Math.multiplyExact(yuv.width,yuv.height),3)
        val rgb=output?.also { require(it.size==size) } ?: FloatArray(size)
        var index = 0
        for (row in 0 until yuv.height) {
            for (column in 0 until yuv.width) {
                val yValue = yuv.y[row * yuv.yRowStride + column].toInt() and 0xFF
                val chromaRow = row / 2
                val chromaCol = column / 2
                val uValue = yuv.u[chromaRow * yuv.uRowStride + chromaCol * yuv.uPixelStride].toInt() and 0xFF
                val vValue = yuv.v[chromaRow * yuv.vRowStride + chromaCol * yuv.vPixelStride].toInt() and 0xFF
                val yf = (yValue - 16) * 1.164f
                val rf = yf + 1.596f * (vValue - 128)
                val gf = yf - 0.813f * (vValue - 128) - 0.391f * (uValue - 128)
                val bf = yf + 2.018f * (uValue - 128)
                rgb[index] = (rf / 255f).coerceIn(0f, 1f)
                rgb[index + 1] = (gf / 255f).coerceIn(0f, 1f)
                rgb[index + 2] = (bf / 255f).coerceIn(0f, 1f)
                index += 3
            }
        }
        return rgb
    }
}

private fun requireRgb(width: Int, height: Int, values: FloatArray, label: String) {
    val expected = Math.multiplyExact(Math.multiplyExact(width, height), 3)
    if (values.size != expected) {
        throw CaptureRejected("$label size ${values.size} does not match $expected.")
    }
}
