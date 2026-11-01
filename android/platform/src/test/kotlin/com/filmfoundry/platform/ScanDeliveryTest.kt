package com.filmfoundry.platform

import com.filmfoundry.workflow.DevelopedResult
import java.io.*
import javax.imageio.ImageIO
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanDeliveryTest {
    @Test fun declaredQualityAndDimensionsMustMatchCommittedPixels() {
        val memory=MemoryStorageAdapter();val scans=PrivateScanStore(memory)
        for(recipe in listOf(mapOf("output_format" to "PNG","output_bits" to 8),mapOf("output_format" to "PNG","output_bits" to 16))) {
            assertThrows(IllegalArgumentException::class.java) {
                scans.save(result,if(recipe["output_bits"]==8) 7 else 8,3,mapOf("scanner" to recipe)) { it.write(png()) }
            }
        }
        assertEquals(0,scans.count())
    }
    private val result=DevelopedResult("result","roll",1,"film.darkroom_negative.npz",emptyMap(),true)
    private fun png(): ByteArray=ByteArrayOutputStream().also { out -> RgbPngRows(out,bitDepth=16).use {
        it.begin(7,3);it.rows(0,3,FloatArray(63){ n->(n+11)/103f });it.finish()
    } }.toByteArray()
    @Test fun jpegCommitRetainsOnlyLossyPixelsAndPreventsEveryLosslessUpgrade() {
        val memory=MemoryStorageAdapter();var quality=0
        val scans=PrivateScanStore(memory) { path,format,q,out,_->
            assertEquals(ScanImageFormat.JPEG,format);quality=q
            val decoded=ImageIO.read(memory.readPrivate(path).inputStream());val image=java.awt.image.BufferedImage(decoded.width,decoded.height,java.awt.image.BufferedImage.TYPE_INT_RGB)
            image.createGraphics().let { g -> try { g.drawImage(decoded,0,0,null) } finally { g.dispose() } };check(ImageIO.write(image,"jpeg",out))
        }
        val recipe=mapOf("scanner" to mapOf("output_format" to "JPEG","jpeg_quality" to 90))
        val saved=scans.save(result,7,3,recipe) { it.write(png()) }
        assertEquals(90,quality);assertEquals(8,saved.precisionBits);assertTrue(saved.imagePath.endsWith(".jpg"))
        assertFalse(memory.existsPrivate("scans/${saved.id}/image.png"));assertEquals(7,ImageIO.read(memory.readPrivate(saved.imagePath).inputStream()).width)
        val loaded=PrivateScanStore(memory).listNewest().single();assertEquals(saved,loaded)
        ScanDelivery.requireExport(loaded,ScanImageFormat.JPEG)
        for(format in listOf(ScanImageFormat.PNG,ScanImageFormat.TIFF)) assertThrows(IllegalArgumentException::class.java) { ScanDelivery.requireExport(loaded,format) }
        assertEquals(loaded,PrivateScanStore(memory).findNewest(result.resultId,saved.recipeShare))
    }
    @Test fun tiffCommitKeepsNativeTiffAndExactDataForDisplayAndLosslessConversion() {
        val memory=MemoryStorageAdapter();val scans=PrivateScanStore(memory)
        val recipe=mapOf("scanner" to mapOf("output_format" to "TIFF"));val original=png()
        val saved=scans.save(result,7,3,recipe) { it.write(original) }
        assertEquals(ScanImageFormat.TIFF,saved.format);assertEquals(16,saved.precisionBits)
        assertEquals(16,ImageIO.read(memory.readPrivate(saved.imagePath).inputStream()).sampleModel.sampleSize[0])
        assertArrayEquals(original,memory.readPrivate(saved.displayPath))
        ScanImageFormat.entries.forEach { ScanDelivery.requireExport(saved,it) }
        assertEquals(saved,PrivateScanStore(memory).listNewest().single())
    }
    @Test fun failedEncodingPublishesNothingAndLeavesPreviousPhotoIntact() {
        val memory=MemoryStorageAdapter();val old=PrivateScanStore(memory).save(result,7,3,emptyMap()) { it.write(png()) }
        val failed=PrivateScanStore(memory) { _,_,_,_,_->throw IOException("encoder failed") }
        assertThrows(IOException::class.java) { failed.save(result,7,3,mapOf("scanner" to mapOf("output_format" to "JPEG"))) { it.write(png()) } }
        assertEquals(listOf(old),PrivateScanStore(memory).listNewest());assertArrayEquals(png(),memory.readPrivate(old.imagePath))
    }
}
