package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class CaptureNormalizerTest {
    private val normalizer = DefaultCaptureNormalizer()

    @Test
    fun explicitOwnedIngestReusesPrivateBuffersAndMatchesCopyingSdrAndHdr() {
        val original=floatArrayOf(0f,.04045f,.5f,1f,1.5f,3f)
        val gain=HdrGainMap(1,1,FloatArray(3){1f},FloatArray(3){1f},FloatArray(3){4f},
            FloatArray(3){1f},FloatArray(3),FloatArray(3))
        for(hdr in listOf(false,true)) {
            val untouched=original.copyOf()
            val privatePixels=original.copyOf()
            val kind=if(hdr) CaptureKind.ULTRA_HDR_GAINMAP else CaptureKind.DISPLAY_REFERRED_SRGB
            val reference=normalizer.normalize(EncodedRgbCapture(2,1,untouched,kind=kind,hdrGainMap=if(hdr) gain else null))
            val owned=normalizer.normalizeOwned(EncodedRgbCapture(2,1,privatePixels,kind=kind,hdrGainMap=if(hdr) gain else null))
            org.junit.jupiter.api.Assertions.assertArrayEquals(original,untouched)
            org.junit.jupiter.api.Assertions.assertSame(privatePixels,owned.linearRgb)
            org.junit.jupiter.api.Assertions.assertArrayEquals(reference.linearRgb,owned.linearRgb)
            assertEquals(reference.record,owned.record)
        }
        val privateLinear=original.copyOf()
        org.junit.jupiter.api.Assertions.assertSame(privateLinear,
            normalizer.normalizeOwned(LinearRgbCapture(2,1,privateLinear)).linearRgb)
    }

    @Test
    fun displaySrgbUsesDesktopInverseEotfAndIsNotSceneReferred() {
        val encoded = floatArrayOf(0.5f, 0.5f, 0.5f)
        val exposure = normalizer.normalize(
            EncodedRgbCapture(width = 1, height = 1, encodedRgb = encoded),
        )
        val expected = TransferFunctions.srgbToLinear(0.5f)
        assertEquals(expected, exposure.linearRgb[0], 1e-6f)
        assertTrue(exposure.linearRgb[0] < 0.5f)
        assertEquals(CaptureKind.DISPLAY_REFERRED_SRGB, exposure.record.kind)
        assertEquals(DataKernel.CAPTURE_WORKING_SPACE, exposure.record.workingSpace)
        assertTrue(exposure.record.ispProcessed)
        assertFalse(exposure.record.sceneReferredClaim)
        assertEquals("absent", exposure.record.hdrGainmap)
        assertEquals("display_referred_srgb", exposure.record.toMetadata()["kind"])
    }

    @Test
    fun ultraHdrDoesNotApplyGainMap() {
        val encoded = floatArrayOf(0.4f, 0.4f, 0.4f)
        val gain = floatArrayOf(2f, 2f, 2f)
        val exposure = normalizer.normalize(
            EncodedRgbCapture(
                width = 1,
                height = 1,
                encodedRgb = encoded,
                kind = CaptureKind.ULTRA_HDR_GAINMAP,
                gainMap = gain,
            ),
        )
        assertEquals(TransferFunctions.srgbToLinear(0.4f), exposure.linearRgb[0], 1e-6f)
        assertEquals("present_not_applied", exposure.record.hdrGainmap)
        assertFalse(exposure.record.sceneReferredClaim)
        assertEquals(CaptureKind.ULTRA_HDR_GAINMAP, exposure.record.kind)
    }

    @Test
    fun linearWorkingRgbDoesNotClipHighlights() {
        val linear = floatArrayOf(4f, 2f, 1.5f)
        val exposure = normalizer.normalize(
            LinearRgbCapture(
                width = 1,
                height = 1,
                linearRgb = linear,
                claimedSceneReferred = true,
                ispProcessed = false,
            ),
        )
        assertEquals(4f, exposure.linearRgb[0])
        assertEquals(2f, exposure.linearRgb[1])
        assertTrue(exposure.record.sceneReferredClaim)
        assertFalse(exposure.record.ispProcessed)
    }

    @Test
    fun ispProcessedCannotClaimSceneReferred() {
        assertThrows(CaptureRejected::class.java) {
            normalizer.normalize(
                LinearRgbCapture(
                    width = 1,
                    height = 1,
                    linearRgb = floatArrayOf(0.2f, 0.2f, 0.2f),
                    claimedSceneReferred = true,
                    ispProcessed = true,
                ),
            )
        }
    }

    @Test
    fun dngFailsClosedWithoutThumbnailFallback() {
        val error = assertThrows(CaptureRejected::class.java) {
            normalizer.normalize(DngCapture(byteArrayOf(1, 2, 3, 4)))
        }
        assertTrue(error.message!!.contains("JPEG thumbnail"))
    }

    @Test
    fun previewIsRejected() {
        val error = assertThrows(CaptureRejected::class.java) {
            normalizer.normalize(PreviewCapture(8, 8))
        }
        assertTrue(error.message!!.contains("preview"))
    }

    @Test
    fun exposureStackAddsInLinearInsteadOfBlendingEncodings() {
        val half = EncodedRgbCapture(
            width = 1,
            height = 1,
            encodedRgb = floatArrayOf(0.5f, 0.5f, 0.5f),
        )
        val stacked = normalizer.normalize(
            ExposureStackCapture(listOf(half, half), floatArrayOf(0f, 0f)),
        )
        val one = TransferFunctions.srgbToLinear(0.5f)
        assertEquals(2f * one, stacked.linearRgb[0], 1e-5f)
        assertEquals(CaptureKind.EXPOSURE_STACK, stacked.record.kind)
        assertEquals(2, stacked.record.frameCount)
        assertFalse(stacked.record.sceneReferredClaim)
        assertTrue(stacked.record.ispProcessed)
    }

    @Test
    fun rawFileNameIsDngKindAndJpegIsDisplay() {
        assertEquals(CaptureKind.SENSOR_RAW_DNG, CaptureFiles.kindForFileName("scene.dng"))
        assertEquals(CaptureKind.SENSOR_RAW_DNG, CaptureFiles.kindForFileName("IMG.CR3"))
        assertEquals(CaptureKind.DISPLAY_REFERRED_SRGB, CaptureFiles.kindForFileName("frame.jpg"))
        assertEquals(CaptureKind.DISPLAY_REFERRED_SRGB, CaptureFiles.kindForFileName("frame.heic"))
    }

    @Test
    fun cameraAdapterDoesNotHandOutAFakeStill() {
        val camera = CameraAdapterNotReady()
        assertTrue(camera.capabilities().previewIsNotCapture)
        assertTrue(camera.capabilities().previewMayIlluminateViewer)
        assertThrows(CaptureNotReady::class.java) {
            camera.captureStill(StillCaptureRequest(preferRaw = true))
        }
        val previewError = assertThrows(CaptureNotReady::class.java) {
            camera.previewForViewer()
        }
        assertTrue(previewError.message!!.contains("preview"))
    }
}
