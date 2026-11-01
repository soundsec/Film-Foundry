package com.filmfoundry.platform

import java.io.*
import javax.imageio.ImageIO
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.util.concurrent.*
import com.filmfoundry.workflow.DevelopedResult

class ScanPrecisionTest {
    @TempDir lateinit var root: File
    @Test fun scanSamplesKeepMoreThanEightBitsThroughRotationAndTiffDelivery() {
        val w=257;val h=3
        val values=FloatArray(w*h*3) { (it+123)/4096f };val png=ByteArrayOutputStream()
        RgbPngRows(png,bitDepth=16).use { it.begin(w,h);it.rows(0,h,values);it.finish() }
        val memory=MemoryStorageAdapter();val storage=object: StorageAdapter by memory { override fun rowScratchRoot()=root }
        storage.writePrivate("scan.png",png.toByteArray())
        val scans=PrivateScanStore(storage)
        val saved=scans.save(DevelopedResult("r","roll",1,"film.darkroom_negative.npz",emptyMap(),true),w,h,emptyMap()) { it.write(png.toByteArray()) }
        assertEquals(16,saved.precisionBits);assertEquals(16,PrivateScanStore(storage).listNewest().single().precisionBits)
        assertEquals(saved,scans.findNewest("r",saved.recipeShare,minimumBits=16))
        for(angle in listOf(0,90,180,270)) {
            val output=ByteArrayOutputStream();ScanImageExport.write(storage,"scan.png",ScanImageFormat.TIFF,angle,output)
            val image=ImageIO.read(output.toByteArray().inputStream());assertEquals(16,image.sampleModel.sampleSize[0])
            for(y in 0 until h) for(x in 0 until w) {
                val (dx,dy)=when(angle) { 90->h-1-y to x;180->w-1-x to h-1-y;270->y to w-1-x;else->x to y }
                for(c in 0..2) assertEquals((values[(y*w+x)*3+c]*65535).toInt(),image.raster.getSample(dx,dy,c))
            }
        }
        assertTrue(root.listFiles().orEmpty().isEmpty());assertArrayEquals(png.toByteArray(),memory.readPrivate("scan.png"))
    }
    @Test fun oldEightBitPhotosCannotMasqueradeAsHighPrecisionTiff() {
        val png=ByteArrayOutputStream();RgbPngRows(png).use { it.begin(2,1);it.rows(0,1,FloatArray(6){.4f});it.finish() }
        val error=assertThrows(IllegalArgumentException::class.java) { ScanTiffRows.write(png.toByteArray().inputStream(),ByteArrayOutputStream()) }
        assertTrue(error.message!!.contains("16-bit"))
    }
    @Test fun galleryCanReadCommittedPhotosWhileNextFrameIsStillRendering() {
        val memory=MemoryStorageAdapter();val scans=PrivateScanStore(memory)
        val source=DevelopedResult("result","roll",1,"film.darkroom_negative.npz",emptyMap(),true)
        val first=scans.save(source,2,1,emptyMap()) { it.write(byteArrayOf(1)) }
        val started=CountDownLatch(1);val release=CountDownLatch(1);val threads=Executors.newFixedThreadPool(2)
        try {
            val render=threads.submit<SavedScan> { scans.save(source,2,1,emptyMap()) { started.countDown();check(release.await(3,TimeUnit.SECONDS));it.write(byteArrayOf(2)) } }
            assertTrue(started.await(2,TimeUnit.SECONDS))
            val catalog=threads.submit<List<SavedScan>> { scans.listNewest() }
            assertEquals(listOf(first),catalog.get(1,TimeUnit.SECONDS));release.countDown();render.get(2,TimeUnit.SECONDS)
            assertEquals(2,scans.count());assertArrayEquals(byteArrayOf(1),memory.readPrivate(first.imagePath))
        } finally { release.countDown();threads.shutdownNow() }
    }
}
