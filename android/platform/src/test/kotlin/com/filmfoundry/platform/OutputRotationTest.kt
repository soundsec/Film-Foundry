package com.filmfoundry.platform

import java.awt.image.BufferedImage
import java.io.*
import javax.imageio.ImageIO
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class OutputRotationTest {
    @TempDir lateinit var root: File
    private fun image(alpha: Boolean=false)=BufferedImage(257,19,if(alpha) BufferedImage.TYPE_4BYTE_ABGR else BufferedImage.TYPE_3BYTE_BGR).apply {
        for(y in 0 until height) for(x in 0 until width) setRGB(x,y,0xFF000000.toInt() or ((x*17+y*31)%256 shl 16) or ((x*13+y*7)%256 shl 8) or ((x*3+y*43)%256))
    }
    private fun assertRotation(source: BufferedImage,encoded: ByteArray,angle: Int) {
        val actual=ImageIO.read(encoded.inputStream())
        assertEquals(if(angle%180==0) source.width else source.height,actual.width)
        assertEquals(if(angle%180==0) source.height else source.width,actual.height)
        for(y in 0 until source.height) for(x in 0 until source.width) {
            val (dx,dy)=when(angle) { 90->source.height-1-y to x;180->source.width-1-x to source.height-1-y;else->y to source.width-1-x }
            assertEquals(source.getRGB(x,y),actual.getRGB(dx,dy),"$angle at $x,$y")
        }
    }
    @Test fun streamedRowsRotateEveryPixelIncludingPartialStripsAndCleanTemporaryFiles() {
        val source=image()
        for(angle in listOf(90,180,270)) {
            val bytes=ByteArrayOutputStream()
            RgbPngRows(bytes).use { png ->
                RotatingPngRows(root,png,angle).use { rotated ->
                    rotated.begin(source.width,source.height)
                    for(y in 0 until source.height) {
                        val row=FloatArray(source.width*3)
                        for(x in 0 until source.width) for(c in 0..2) row[x*3+c]=((source.getRGB(x,y) ushr (16-c*8)) and 255)/255f
                        rotated.rows(y,1,row)
                    }
                    rotated.finish()
                }
                png.finish()
            }
            assertRotation(source,bytes.toByteArray(),angle)
            assertTrue(root.listFiles().orEmpty().isEmpty())
        }
    }
    @Test fun existingRgbAndRgbaPhotosExportWithIdenticalPixelsAndUntouchedSource() {
        val memory=MemoryStorageAdapter();val storage=object: StorageAdapter by memory { override fun rowScratchRoot()=root }
        for(alpha in listOf(false,true)) {
            val source=image(alpha);val original=ByteArrayOutputStream().also { ImageIO.write(source,"png",it) }.toByteArray()
            storage.writePrivate("image.png",original)
            for(angle in listOf(90,180,270)) {
                val output=ByteArrayOutputStream();val size=PngPhotoRotation.write(storage,"image.png",angle,output)
                assertEquals(OutputOrientation.size(source.width,source.height,angle),size)
                assertRotation(source,output.toByteArray(),angle)
            }
            assertArrayEquals(original,storage.readPrivate("image.png"));assertTrue(root.listFiles().orEmpty().isEmpty())
        }
    }
    @Test fun malformedOrCancelledPhotoCannotLeaveRotationScratchBehind() {
        val original=ByteArrayOutputStream().also { ImageIO.write(image(),"png",it) }.toByteArray()
        val memory=MemoryStorageAdapter();val storage=object: StorageAdapter by memory { override fun rowScratchRoot()=root }
        val badCrc=original.copyOf().also { it[it.size-1]=(it.last().toInt() xor 1).toByte() }
        for(bytes in listOf(original.copyOf(original.size-6),badCrc,original+byteArrayOf(1))) {
            storage.writePrivate("image.png",bytes)
            assertThrows(Exception::class.java) { PngPhotoRotation.write(storage,"image.png",90,ByteArrayOutputStream()) }
            assertTrue(root.listFiles().orEmpty().isEmpty())
        }
        storage.writePrivate("image.png",original);var calls=0
        assertThrows(InterruptedException::class.java) {
            PngPhotoRotation.write(storage,"image.png",270,ByteArrayOutputStream()) { if(++calls==30) throw InterruptedException("cancel") }
        }
        assertTrue(root.listFiles().orEmpty().isEmpty());assertArrayEquals(original,storage.readPrivate("image.png"))
    }
}
