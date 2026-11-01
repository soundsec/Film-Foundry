package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class CameraXCaptureTest {
    private val jpegStill = CameraXImageProxy(
        useCase = CameraXUseCase.IMAGE_CAPTURE,
        format = CameraXBufferFormat.JPEG,
        width = 1,
        height = 1,
        encodedRgb = floatArrayOf(0.8f, 0.4f, 0.2f),
        rotationDegrees = 90,
        iso = 200,
    )

    @Test
    fun jpegImageCaptureBecomesDisplayReferredStill() {
        val source = CameraXCaptureBridge.still(
            jpegStill,
            StillCaptureRequest(),
            CameraCapabilities(),
        )
        val encoded = source as EncodedRgbCapture
        assertEquals(CaptureKind.DISPLAY_REFERRED_SRGB, encoded.kind)
        assertEquals(0, encoded.orientationDegrees)
        assertEquals(200, encoded.iso)
        val working = DefaultCaptureNormalizer().normalize(source)
        assertEquals("display_referred_srgb", working.record.kind.wireName)
        assertTrue("orientation_degrees" !in working.record.toMetadata())
        assertTrue(!working.record.sceneReferredClaim)
    }

    @Test
    fun previewUseCaseCannotFillARoll() {
        val preview = CameraXImageProxy(
            useCase = CameraXUseCase.PREVIEW,
            format = CameraXBufferFormat.YUV_420_888,
            width = 2,
            height = 2,
            yuv = grayYuv(),
        )
        val error = assertThrows(CaptureRejected::class.java) {
            CameraXCaptureBridge.still(preview, StillCaptureRequest(), CameraCapabilities())
        }
        assertTrue(error.message!!.contains("cannot fill a roll"))
    }

    @Test
    fun analysisYuvLightsTheViewerAndIsNotACaptureKind() {
        val proxy = CameraXImageProxy(
            useCase = CameraXUseCase.IMAGE_ANALYSIS,
            format = CameraXBufferFormat.YUV_420_888,
            width = 2,
            height = 2,
            yuv = grayYuv(),
        )
        val frame = CameraXCaptureBridge.preview(proxy)
        assertEquals(2, frame.width)
        assertEquals(ViewerPreviewEncoding.DISPLAY_SRGB, frame.encoding)
        assertTrue(DataKernel.PHONE_VIEWER_PREVIEW_KIND !in DataKernel.CAPTURE_KIND_VALUES)
        assertThrows(CaptureRejected::class.java) {
            DefaultCaptureNormalizer().normalize(PreviewCapture(frame.width, frame.height))
        }
    }

    @Test
    fun imageCaptureCannotBeAViewerLamp() {
        assertThrows(CaptureRejected::class.java) {
            CameraXCaptureBridge.preview(jpegStill)
        }
    }

    @Test
    fun preferRawWithoutDngFailsClosed() {
        val error = assertThrows(CaptureRejected::class.java) {
            CameraXCaptureBridge.still(
                jpegStill,
                StillCaptureRequest(preferRaw = true),
                CameraCapabilities(supportsDng = false),
            )
        }
        assertTrue(error.message!!.contains("DNG"))
    }

    @Test
    fun preferRawMustNotSilentlyKeepJpeg() {
        val error = assertThrows(CaptureRejected::class.java) {
            CameraXCaptureBridge.still(
                jpegStill,
                StillCaptureRequest(preferRaw = true),
                CameraCapabilities(supportsDng = true),
            )
        }
        assertTrue(error.message!!.contains("RAW_SENSOR"))
    }

    @Test
    fun rawSensorStillStaysDngAndDoesNotBecomeJpeg() {
        val source = CameraXCaptureBridge.still(
            CameraXImageProxy(
                useCase = CameraXUseCase.IMAGE_CAPTURE,
                format = CameraXBufferFormat.RAW_SENSOR,
                width = 2,
                height = 2,
                dngBytes = byteArrayOf(1, 2, 3, 4),
            ),
            StillCaptureRequest(preferRaw = true),
            CameraCapabilities(supportsDng = true),
        )
        assertTrue(source is DngCapture)
        assertThrows(CaptureRejected::class.java) {
            DefaultCaptureNormalizer().normalize(source)
        }
        assertThrows(CaptureRejected::class.java) {
            CameraXCaptureBridge.still(
                CameraXImageProxy(
                    useCase = CameraXUseCase.IMAGE_CAPTURE,
                    format = CameraXBufferFormat.RAW_SENSOR,
                    width = 2,
                    height = 2,
                    dngBytes = byteArrayOf(1, 2, 3, 4),
                ),
                StillCaptureRequest(),
                CameraCapabilities(supportsDng = true),
            )
        }
    }

    @Test
    fun jpegBytesWithoutHostDecodeStayNotReady() {
        assertThrows(CaptureNotReady::class.java) {
            CameraXCaptureBridge.still(
                CameraXImageProxy(
                    useCase = CameraXUseCase.IMAGE_CAPTURE,
                    format = CameraXBufferFormat.JPEG,
                    width = 1,
                    height = 1,
                    jpegBytes = byteArrayOf(0xFF.toByte(), 0xD8.toByte()),
                ),
                StillCaptureRequest(),
                CameraCapabilities(),
            )
        }
    }

    @Test
    fun scriptedAdapterUsesTheSameBridge() {
        val camera = ScriptedCameraAdapter(still = jpegStill, preview = analysisProxy())
        val still = camera.captureStill()
        assertTrue(still is EncodedRgbCapture)
        val lamp = camera.previewForViewer()
        assertEquals(2, lamp.width)
    }

    private fun analysisProxy(): CameraXImageProxy {
        return CameraXImageProxy(
            useCase = CameraXUseCase.IMAGE_ANALYSIS,
            format = CameraXBufferFormat.YUV_420_888,
            width = 2,
            height = 2,
            yuv = grayYuv(),
        )
    }

    private fun grayYuv(): Yuv420888 {
        return Yuv420888(
            width = 2,
            height = 2,
            y = ByteArray(4) { 0x80.toByte() },
            u = byteArrayOf(0x80.toByte()),
            v = byteArrayOf(0x80.toByte()),
            yRowStride = 2,
            uRowStride = 1,
            vRowStride = 1,
        )
    }
}
