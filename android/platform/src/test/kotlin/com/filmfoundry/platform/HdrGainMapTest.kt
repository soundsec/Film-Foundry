package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.pow

class HdrGainMapTest {
    @Test fun croppedRowsUseWholeFrameGainCoordinatesWithExactlyTheSameValues() {
        val gain=HdrGainMap(3,2,FloatArray(18) { (it%7)/7f },floatArrayOf(.5f,.7f,1f),floatArrayOf(2f,3f,4f),floatArrayOf(.8f,1f,1.2f),FloatArray(3){.01f},FloatArray(3){.02f})
        val full=FloatArray(11*7*3) { (it%19)/10f };val before=full.copyOf();gain.applyRowsInPlace(full,11,0,7)
        for(y in 0 until 7 step 2) {
            val count=minOf(2,7-y);val window=FloatArray(5*count*3)
            for(row in 0 until count) before.copyInto(window,row*15,((y+row)*11+2)*3,((y+row)*11+7)*3)
            gain.applyWindowRowsInPlace(window,11,7,2,y,5)
            for(row in 0 until count) assertArrayEquals(full.copyOfRange(((y+row)*11+2)*3,((y+row)*11+7)*3),window.copyOfRange(row*15,(row+1)*15),0f)
        }
    }
    private fun map(width: Int=1,height: Int=1,samples: FloatArray=FloatArray(width*height*3){1f},
        gamma: FloatArray=FloatArray(3){1f}, epsS: FloatArray=FloatArray(3),epsH: FloatArray=FloatArray(3)) =
        HdrGainMap(width,height,samples,FloatArray(3){1f},FloatArray(3){8f},gamma,epsS,epsH)

    @Test fun fullHdrReconstructionPreservesHighlightsSourceOwnershipAndCaptureProvenance() {
        val encoded = floatArrayOf(.5f,.5f,.5f)
        val source = EncodedRgbCapture(1,1,encoded,kind=CaptureKind.ULTRA_HDR_GAINMAP,hdrGainMap=map())
        val result = DefaultCaptureNormalizer().normalize(source)
        val expected = TransferFunctions.srgbToLinear(.5f)*8
        assertArrayEquals(FloatArray(3){expected},result.linearRgb,1e-6f)
        assertTrue(result.linearRgb.all { it > 1f })
        assertArrayEquals(floatArrayOf(.5f,.5f,.5f),encoded,0f)
        assertEquals("applied",result.record.hdrGainmap)
        assertEquals("ultra_hdr_gainmap_full_linear_srgb_v1",result.record.sourceEncoding)
        assertFalse(result.record.sceneReferredClaim)
        val stack = DefaultCaptureNormalizer().normalize(ExposureStackCapture(listOf(source,source),floatArrayOf(0f,0f)))
        assertEquals("applied",stack.record.hdrGainmap)
        assertArrayEquals(FloatArray(3){expected*2},stack.linearRgb,2e-6f)
    }

    @Test fun gammaOffsetsAndLowerResolutionBilinearSamplesMatchWholeAndRowCoordinates() {
        val gain = map(2,2,FloatArray(12){(it%5)/4f},floatArrayOf(1f,2f,.5f),
            floatArrayOf(.01f,.02f,.03f),floatArrayOf(.03f,.02f,.01f))
        val width=5; val height=7
        val original=FloatArray(width*height*3){.2f+(it%11)*.03f}
        val whole=original.copyOf()
        gain.applyRowsInPlace(whole,width,0,height)
        val joined=FloatArray(whole.size)
        for (start in 0 until height step 3) {
            val stop=minOf(start+3,height)
            val block=original.copyOfRange(start*width*3,stop*width*3)
            gain.applyRowsInPlace(block,width,start,height)
            block.copyInto(joined,start*width*3)
        }
        assertArrayEquals(whole,joined,0f)
        // Top left clamps to the first gain sample: gamma and offsets are channel-specific.
        for (c in 0..2) {
            val sample=gain.samples[c].toDouble()
            val expected=(original[c]+gain.epsilonSdr[c])*exp(ln(8.0)*sample.pow(gain.gamma[c].toDouble()))-gain.epsilonHdr[c]
            assertEquals(expected.toFloat(),whole[c],1e-7f)
        }
        assertArrayEquals(FloatArray(original.size){.2f+(it%11)*.03f},original,0f)
    }

    @Test fun hdrBridgeRequiresRealParametersAndMalformedMapsFailClosed() {
        val proxy=CameraXImageProxy(CameraXUseCase.IMAGE_CAPTURE,CameraXBufferFormat.JPEG,1,1,
            encodedRgb=floatArrayOf(.5f,.5f,.5f),gainMap=floatArrayOf(2f,2f,2f))
        assertThrows(CaptureNotReady::class.java) { CameraXCaptureBridge.still(proxy,StillCaptureRequest(preferHdr=true),CameraCapabilities(supportsUltraHdr=true)) }
        assertThrows(CaptureRejected::class.java) { CameraXCaptureBridge.still(proxy.copy(format=CameraXBufferFormat.YUV_420_888),StillCaptureRequest(preferHdr=true),CameraCapabilities(supportsUltraHdr=true)) }
        val captured=CameraXCaptureBridge.still(proxy.copy(hdrGainMap=map()),StillCaptureRequest(preferHdr=true),CameraCapabilities(supportsUltraHdr=true))
        assertEquals("applied",DefaultCaptureNormalizer().normalize(captured).record.hdrGainmap)
        assertThrows(CaptureRejected::class.java) { map(samples=floatArrayOf(Float.NaN,0f,0f)) }
        assertThrows(CaptureRejected::class.java) { map(gamma=floatArrayOf(0f,1f,1f)) }
        assertThrows(CaptureRejected::class.java) { EncodedRgbCapture(1,1,FloatArray(3),hdrGainMap=map()) }
    }
}
