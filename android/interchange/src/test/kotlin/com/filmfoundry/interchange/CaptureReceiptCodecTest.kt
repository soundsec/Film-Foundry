package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class CaptureReceiptCodecTest {
    @Test fun rawReceiptRequiresDngAndSensorProvenanceAndCanMixWithHdrInTheSameFrame() {
        val raw=mapOf("kind" to "sensor_raw_dng","working_space" to DataKernel.CAPTURE_WORKING_SPACE,"isp_processed" to false,"hdr_gainmap" to "absent")
        val source=StoredExposure("exposure_000001/source.dng",17,13,0.0,raw)
        val receipt=CaptureReceipt("roll",1,"roll:1",emptyMap(),false,listOf(source))
        assertEquals(source,CaptureReceiptCodec.decode(CaptureReceiptCodec.encode(receipt)).sources.single())
        assertThrows(IllegalArgumentException::class.java) { CaptureReceiptCodec.encode(receipt.copy(sources=listOf(source.copy(path="exposure_000001/source.jpg")))) }
        assertThrows(IllegalArgumentException::class.java) { CaptureReceiptCodec.encode(receipt.copy(sources=listOf(source.copy(record=raw+("isp_processed" to true))))) }
        val hdr=source.copy(path="exposure_000002/source.jpg",record=raw+mapOf("kind" to "ultra_hdr_gainmap","isp_processed" to true,"hdr_gainmap" to "present_not_applied"))
        assertEquals(2,CaptureReceiptCodec.decode(CaptureReceiptCodec.encode(receipt.copy(sources=listOf(source,hdr)))).sources.size)
    }
    @Test fun savedHdrRetainsNativeProvenanceAndCannotPretendItsGainHasAlreadyBeenApplied() {
        val record=mapOf("kind" to "ultra_hdr_gainmap","working_space" to DataKernel.CAPTURE_WORKING_SPACE,"hdr_gainmap" to "present_not_applied",
            "native_camera" to mapOf("camera_id" to "rear","requested_physical_id" to "wide"),"native_zoom_ratio" to 1.5)
        val source=StoredExposure("exposure_000001/source.jpg",17,13,0.0,record)
        val receipt=CaptureReceipt("roll",1,"roll:1",emptyMap(),false,listOf(source))
        val decoded=CaptureReceiptCodec.decode(CaptureReceiptCodec.encode(receipt))
        assertEquals(RecipeShareCodec.canonicalJson(record),RecipeShareCodec.canonicalJson(decoded.sources.single().record))
        assertThrows(IllegalArgumentException::class.java) { CaptureReceiptCodec.encode(receipt.copy(sources=listOf(source.copy(record=record+("hdr_gainmap" to "applied"))))) }
        assertThrows(IllegalArgumentException::class.java) { CaptureReceiptCodec.encode(receipt.copy(sources=listOf(source.copy(record=record+("kind" to "sensor_raw_dng"))))) }
    }
    @Test fun receiptRoundTripKeepsPerExposureParametersAndRejectsDifferentDimensions() {
        val record=mapOf("kind" to "display_referred_srgb","working_space" to DataKernel.CAPTURE_WORKING_SPACE,
            "exposure_conditions" to mapOf("camera" to mapOf("seed" to 17,"dust" to true)))
        val a=StoredExposure("exposure_000001/source.jpg",17,13,-1.5,record)
        val b=a.copy(path="exposure_000002/source.jpg",ev=-2.0)
        val receipt=CaptureReceipt("roll",1,"roll:1",mapOf("contrast" to 1.1),false,listOf(a,b))
        assertEquals(receipt.copy(film=MovieMasterCodec.snapshot(receipt.film)),CaptureReceiptCodec.decode(CaptureReceiptCodec.encode(receipt)))
        assertThrows(IllegalArgumentException::class.java) { CaptureReceiptCodec.encode(receipt.copy(sources=listOf(a,b.copy(width=18)))) }
        assertThrows(IllegalArgumentException::class.java) { CaptureReceiptCodec.encode(receipt.copy(sources=listOf(a.copy(ev=Double.NaN)))) }
        val bundle=StoredExposureBundle("roll",1,"roll:1",emptyMap(),a)
        assertEquals(bundle,CaptureReceiptCodec.decodeBundle(CaptureReceiptCodec.encodeBundle(bundle)))
    }
}
