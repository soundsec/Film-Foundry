package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanOutputSettingsTest {
    @Test fun editingOneFormatsQualityPreservesTheOtherFormatsExactValue() {
        val original=ScanOutputSettings("JPEG",16,95)
        val png=original.select(1,original.qualityIndex).select(1,0)
        assertEquals(8,png.actualBits)
        val jpeg=png.select(0,png.qualityIndex)
        assertEquals(95,jpeg.jpegQuality)
        val fine=jpeg.select(0,1)
        assertEquals(70,fine.jpegQuality)
        assertEquals(8,fine.select(1,fine.qualityIndex).actualBits)
    }
    @Test fun restoredJpegQualityIsNeverSnappedToANearbyPreset() {
        val draft=ScanEditorDraft.saved(mapOf("scanner" to mapOf("output_format" to "JPEG","jpeg_quality" to 95)),"Saved",true)
        val original=ScanOutputSettings.from(draft)
        assertEquals(3,original.qualityIndex)
        assertEquals(original,original.select(original.formatIndex,original.qualityIndex))
        assertEquals(95,original.applyTo(draft).jpegQuality)
        val high=original.select(original.formatIndex,2)
        assertEquals(100,high.jpegQuality)
        val history=ScanEditHistory(draft);history.change(high.applyTo(draft))
        assertEquals(95,history.undo().jpegQuality);assertEquals(100,history.redo().jpegQuality)
        assertEquals(listOf(40,70,100),(0..2).map { original.select(0,it).jpegQuality })
    }
    @Test fun formatChangesRetainOriginalSettingsAndTiffAlwaysReportsSixteenBits() {
        val source=ScanOutputSettings("JPEG",8,83)
        val tiff=source.select(2,0)
        assertEquals(16,tiff.actualBits);assertEquals(1,tiff.qualityIndex)
        assertEquals(tiff,tiff.select(2,0))
        assertEquals(source,tiff.select(0,1))
        assertEquals(8,source.copy(format="PNG").actualBits)
    }
    @Test fun automaticOutputDefaultsAndPhotoHistoryAreIndependentValues() {
        val photo=ScanEditorDraft(outputFormat="JPEG",outputBits=8,jpegQuality=90)
        val defaults=ScanEditorDraft(outputFormat="PNG",outputBits=16)
        val automatic=ScanOutputSettings("TIFF",16,98).applyTo(defaults)
        val history=ScanEditHistory(photo)
        history.change(photo.copy(gain=55));history.undo()
        assertEquals(photo,history.current);assertEquals("TIFF",automatic.outputFormat);assertEquals("PNG",defaults.outputFormat)
    }
}
