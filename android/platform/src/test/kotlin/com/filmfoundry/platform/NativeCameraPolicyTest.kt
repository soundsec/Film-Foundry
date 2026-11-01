package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class NativeCameraPolicyTest {
    @Test fun finderFlashShortcutPreservesOtherControlsAndCannotBypassManualExposureRules() {
        val original=NativeCaptureSettings(lockAwb=true,whiteBalance=5,focusDiopters=2f,format="raw")
        val torch=original.withIllumination("torch")
        assertEquals(original.copy(torch=true),torch)
        assertEquals("off",torch.flash);assertEquals(original,torch.withIllumination("off"))
        val flash=torch.withIllumination("auto");assertFalse(flash.torch);assertEquals("auto",flash.flash)
        assertEquals(listOf("off"),original.illuminationChoices(false))
        val manual=NativeCaptureSettings(iso=400,exposureNs=10_000_000)
        assertEquals(listOf("off","torch"),manual.illuminationChoices(true))
        assertThrows(IllegalArgumentException::class.java) { manual.withIllumination("on") }
        assertEquals(400,manual.withIllumination("torch").iso)
        assertFalse(original.torch)
    }
    @Test fun exifDirectionsAndHdrSamplesUseTheSameCoordinateMapWithoutTouchingTheSource() {
        val expected=listOf(listOf(1,2,3,4,5,6),listOf(3,2,1,6,5,4),listOf(6,5,4,3,2,1),listOf(4,5,6,1,2,3),
            listOf(1,4,2,5,3,6),listOf(4,1,5,2,6,3),listOf(6,3,5,2,4,1),listOf(3,6,2,5,1,4))
        val samples=FloatArray(18){(it/3+1)/6f};val before=samples.copyOf()
        val gain=HdrGainMap(3,2,samples,FloatArray(3){1f},FloatArray(3){2f},FloatArray(3){1f},FloatArray(3),FloatArray(3))
        val native=FloatArray(18){.25f};gain.applyRowsInPlace(native,3,0,2)
        for(exif in 1..8) {
            val map=CapturePixelOrientation(3,2,exif)
            assertEquals(if(exif<5) 3 else 2,map.width);assertEquals(if(exif<5) 2 else 3,map.height)
            val ordered=(0 until map.height).flatMap { y -> (0 until map.width).map { x -> map.y(x,y)*3+map.x(x,y)+1 } }
            assertEquals(expected[exif-1],ordered)
            val transformed=gain.let(map::orientGain)
            for(i in ordered.indices) assertEquals(ordered[i]/6f,transformed.samples[i*3])
            val hdr=FloatArray(18){.25f};transformed.applyRowsInPlace(hdr,map.width,0,map.height)
            for(i in ordered.indices) for(c in 0..2) assertEquals(native[(ordered[i]-1)*3+c],hdr[i*3+c],0f)
        }
        assertArrayEquals(before,samples,0f)
    }
    @Test fun legacyPixelReadersRejectHdrRatherThanSilentlyExposingTheSdrBase() {
        var reads=0
        val pixels=object: StoredExposurePixels {
            override val width=2;override val height=2
            override fun encodedRows(start: Int,count: Int): FloatArray { reads++;return FloatArray(2*count*3){.5f} }
            override fun close()=Unit
        }
        assertThrows(IllegalArgumentException::class.java) { pixels.linearWindowRows(0,0,2,2,CaptureKind.ULTRA_HDR_GAINMAP) }
        assertEquals(0,reads)
        assertTrue(pixels.linearWindowRows(0,0,2,2,CaptureKind.DISPLAY_REFERRED_SRGB).all { it>0f && it<.5f })
        assertEquals(1,reads)
    }
    @Test fun manualRequestsNeverCombineConflictingAutomaticControlsOrInventMissingFocalData() {
        assertThrows(IllegalArgumentException::class.java) { NativeCaptureSettings(iso=100) }
        assertThrows(IllegalArgumentException::class.java) { NativeCaptureSettings(iso=100,exposureNs=10_000_000,lockAe=true) }
        assertThrows(IllegalArgumentException::class.java) { NativeCaptureSettings(flash="auto",iso=100,exposureNs=10_000_000) }
        assertThrows(IllegalArgumentException::class.java) { NativeCaptureSettings(flash="on",torch=true) }
        assertThrows(IllegalArgumentException::class.java) { NativeCaptureSettings(focusDiopters=Float.NaN) }
        assertEquals(100,NativeCaptureSettings(iso=100,exposureNs=10_000_000).metadata()["iso"])
        assertEquals(50.0,NativeCameraCatalog.referenceEquivalent(50.0,36.0,24.0)!!,1e-12)
        assertNull(NativeCameraCatalog.referenceEquivalent(4.0,0.0,4.0))
        assertNull(NativeCameraCatalog.referenceEquivalent(Double.NaN,6.0,4.0))
    }
}
