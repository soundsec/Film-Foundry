package com.filmfoundry.platform

import com.filmfoundry.interchange.*
import com.filmfoundry.workflow.*
import com.filmfoundry.kernel.RgbVolume
import java.io.*
import java.nio.file.Path
import javax.imageio.ImageIO
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class RgbPngRowsTest {
    @TempDir lateinit var root: Path
    @Test fun decodedPixelsMatchFormerBitmapQuantizationAcrossChunkAndRowBoundaries() {
        val w=257;val h=193;val random=java.util.Random(37)
        val rgb=FloatArray(w*h*3){random.nextFloat()*1.4f-.2f};val before=rgb.copyOf()
        val out=ByteArrayOutputStream()
        RgbPngRows(out).use { png ->
            png.begin(w,h)
            for(y in 0 until h step 7) { val count=minOf(7,h-y);png.rows(y,count,rgb.copyOfRange(y*w*3,(y+count)*w*3)) }
            png.finish()
        }
        val image=ImageIO.read(ByteArrayInputStream(out.toByteArray()))
        assertEquals(w,image.width);assertEquals(h,image.height)
        for(y in 0 until h) for(x in 0 until w) {
            val i=(y*w+x)*3
            fun c(k: Int)=(rgb[i+k].coerceIn(0f,1f)*255).toInt()
            assertEquals((255 shl 24) or (c(0) shl 16) or (c(1) shl 8) or c(2),image.getRGB(x,y))
        }
        assertArrayEquals(before,rgb,0f)
    }
    @Test fun twelveMegapixelsAreWrittenWithoutAWholeOutputBuffer() {
        val w=4000;val h=3000;var total=0L;var largest=0
        val sink=object: OutputStream() {
            override fun write(value: Int) { total++ }
            override fun write(bytes: ByteArray,offset: Int,length: Int) { total+=length;largest=maxOf(largest,length) }
        }
        val row=FloatArray(w*3){(it%257)/256f}
        RgbPngRows(sink).use { png -> png.begin(w,h);for(y in 0 until h) png.rows(y,1,row);png.finish() }
        assertTrue(total>1000);assertTrue(largest<=65536)
    }
    @Test fun incompleteCancelledAndFailedOutputsNeverBecomeLibraryEntries() {
        val output=ByteArrayOutputStream();var cancelled=false
        RgbPngRows(output) { if(cancelled) throw InterruptedException("cancelled") }.use { png ->
            png.begin(2,2);png.rows(0,1,FloatArray(6))
            assertThrows(IllegalStateException::class.java) { png.finish() }
            cancelled=true
            assertThrows(InterruptedException::class.java) { png.rows(1,1,FloatArray(6)) }
        }
        val memory=MemoryStorageAdapter();val scans=PrivateScanStore(memory)
        val result=DevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap())
        assertThrows(IOException::class.java) {
            scans.saveStreamed(result,emptyMap()) { stream -> stream.write(byteArrayOf(1));throw IOException("failed encoder") }
        }
        assertEquals(0,scans.count())
    }
    @Test fun realRowScanSavesAReadablePhotoAtTheSamePixelsAndLeavesTheOpticalSourceUntouched() {
        val kernel=com.filmfoundry.kernel.PhoneReducedKernel();val w=13;val h=9
        val medium=kernel.develop(kernel.expose(RgbVolume(w,h,linearRgb=FloatArray(w*h*3){(it%31)/17f}),emptyMap()),emptyMap())
        val source=NpzMediumArchive().writeDevelopedOptical(DevelopedOpticalArchive(medium.opticalDensityRgb,medium.metadata,medium.clearBaseOpticalDensityRgb))
        val files=PrivateMediumFiles(root.toFile());val memory=MemoryStorageAdapter()
        val storage=object: StorageAdapter by memory {
            override fun rowScratchRoot()=root.resolve("scratch").toFile()
            override fun <T> readPrivate(path: String,reader: (InputStream)->T)=files.read(path,reader)
            override fun writePrivate(path: String,check: ()->Unit,writer: (OutputStream)->Unit)=files.write(path,check,writer)
        }
        val result=DevelopedResult("result","roll",1,"source.darkroom_negative.npz",emptyMap())
        files.write(result.opticalPath) { it.write(source) }
        val store=MediumStore(storage);val scans=PrivateScanStore(storage)
        val recipe=mapOf("scanner" to mapOf("scan_saturation" to 1.2,"print_mapping_mode" to "printlike"))
        val expected=kernel.observe(medium,recipe).outputRgb.values
        val saved=scans.saveStreamed(result,recipe) { store.scanPng(result.opticalPath,recipe,it,memoryBudgetBytes=20L*1048576) }
        val image=files.read(saved.imagePath) { ImageIO.read(it) }
        for(y in 0 until h) for(x in 0 until w) for(c in 0..2) assertEquals((expected[(y*w+x)*3+c].coerceIn(0f,1f)*255).toInt(),(image.getRGB(x,y) ushr (16-c*8)) and 255)
        assertArrayEquals(source,files.read(result.opticalPath){it.readBytes()})
        assertTrue(storage.rowScratchRoot().listFiles().orEmpty().isEmpty());assertEquals(1,scans.count())
    }
}
