package com.filmfoundry.platform

import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class DngRawDecoderTest {
    @Test fun orientedRawRowsMatchPixelPermutationWhileLegacyReceiptsKeepSensorCoordinates() {
        val root=java.nio.file.Files.createTempDirectory("raw-orientation-").toFile()
        try {
            for(exif in 1..8) {
                val bytes=fixture(orientation=exif,gradient=true)
                val file=java.io.File(root,"$exif.dng").apply { writeBytes(bytes) }
                val native=DngRawDecoder.open(file).use { it.readRows(0,8) }
                val width=if(exif>=5) 8 else 6;val height=if(exif>=5) 6 else 8
                assertEquals(width to height,DngRawDecoder.storedDimensions(file,true))
                assertEquals(6 to 8,DngRawDecoder.storedDimensions(file))
                val expected=FloatArray(width*height*3)
                for(y in 0 until height) for(x in 0 until width) {
                    val (sx,sy)=when(exif) {
                        1->x to y;2->5-x to y;3->5-x to 7-y;4->x to 7-y
                        5->y to x;6->y to 7-x;7->5-y to 7-x;else->5-y to x
                    }
                    native.copyInto(expected,(y*width+x)*3,(sy*6+sx)*3,(sy*6+sx)*3+3)
                }
                DngStoredExposurePixels(file).use { rows ->
                    rows.configureCapture(mapOf("stored_pixel_orientation" to "dng_exif_v1"))
                    assertEquals(width,rows.width);assertEquals(height,rows.height)
                    for(y in 0 until height step 2) {
                        val count=minOf(2,height-y)
                        assertArrayEquals(expected.copyOfRange(y*width*3,(y+count)*width*3),
                            rows.linearWindowRows(0,y,width,count,CaptureKind.SENSOR_RAW_DNG),0f)
                    }
                    val crop=rows.linearWindowRows(1,1,width-2,2,CaptureKind.SENSOR_RAW_DNG)
                    for(y in 0..1) assertArrayEquals(expected.copyOfRange(((y+1)*width+1)*3,((y+2)*width-1)*3),
                        crop.copyOfRange(y*(width-2)*3,(y+1)*(width-2)*3),0f)
                }
                DngStoredExposurePixels(file).use { rows ->
                    rows.configureCapture(mapOf("stored_pixel_orientation" to "sensor_native_v1"))
                    assertEquals(6,rows.width);assertEquals(8,rows.height)
                    assertArrayEquals(native,rows.linearWindowRows(0,0,6,8,CaptureKind.SENSOR_RAW_DNG),0f)
                }
                assertArrayEquals(bytes,file.readBytes())
            }
        } finally { root.deleteRecursively() }
    }
    @Test fun savedRawIsDurableRecoverableAndDecodedByRowsWithoutReadingTheFileIntoTheHeap() {
        val root=java.nio.file.Files.createTempDirectory("raw-source-").toFile()
        try {
            val store=CaptureReceiptStore(root);val path=store.path("roll",1);var calls=0
            val original=fixture()
            val camera=object: StoredStillCamera {
                override fun sourceFileName()="source.dng"
                override fun saveStill(target: java.io.File): StoredStillInfo {
                    calls++;target.writeBytes(original)
                    val dimensions=DngRawDecoder.storedDimensions(target)
                    return StoredStillInfo(dimensions.first,dimensions.second,CaptureRecord(CaptureKind.SENSOR_RAW_DNG,false,sourceEncoding="stored_sensor_dng_v1"))
                }
            }
            val receipt=store.append(path,"roll",1,"frame",emptyMap(),true,0.0,camera,com.filmfoundry.workflow.WorkflowControl())
            assertEquals(1,calls);assertTrue(receipt.sources.single().path.endsWith(".dng"))
            val file=store.sourceFile(path,receipt.sources.single());assertArrayEquals(original,file.readBytes())
            java.io.File(root,path).delete()
            assertEquals(receipt.sources,store.recover(path)!!.sources)
            DngStoredExposurePixels(file).use { rows ->
                assertEquals(6,rows.width);assertEquals(8,rows.height)
                assertTrue(rows.linearWindowRows(1,2,3,2,CaptureKind.SENSOR_RAW_DNG).all { kotlin.math.abs(it-2f)<.001f })
                assertThrows(CaptureRejected::class.java) { rows.encodedRows(0,1) }
            }
            assertArrayEquals(original,file.readBytes())
            val bad=java.io.File(root,"unsupported.dng").apply { writeBytes(fixture(compression=7)) }
            assertEquals(6 to 8,DngRawDecoder.storedDimensions(bad))
            assertThrows(CaptureNotReady::class.java) { DngStoredExposurePixels(bad) }
            assertTrue(bad.isFile)
        } finally { root.deleteRecursively() }
    }
    @Test fun pagedInputCrossesPageBoundariesAndPreservesBothEndianBitPatterns() {
        val root=java.nio.file.Files.createTempDirectory("raw-pages-").toFile()
        try {
            val bytes=ByteArray(65536*6+11) { (it*17).toByte() };val file=java.io.File(root,"pages.dng").apply { writeBytes(bytes) }
            DngInput.file(file).use { input -> for(order in listOf(ByteOrder.BIG_ENDIAN,ByteOrder.LITTLE_ENDIAN)) {
                input.order(order);val reference=ByteBuffer.wrap(bytes).order(order)
                for(offset in listOf(65533,262143,1,327679,65535,131071)) assertEquals(reference.getDouble(offset).toRawBits(),input.getDouble(offset).toRawBits())
            } }
        } finally { root.deleteRecursively() }
    }
    private fun fixture(order: ByteOrder=ByteOrder.LITTLE_ENDIAN,pattern: IntArray=intArrayOf(0,1,1,2),
        compression: Int=1,reduced: Boolean=false,requiredOpcode: Boolean=false,orientation: Int=1,gradient: Boolean=false): ByteArray {
        fun shorts(vararg v: Int)=ByteBuffer.allocate(v.size*2).order(order).also { b -> v.forEach { b.putShort(it.toShort()) } }.array()
        fun longs(vararg v: Int)=ByteBuffer.allocate(v.size*4).order(order).also { b -> v.forEach { b.putInt(it) } }.array()
        fun rational(v: DoubleArray)=ByteBuffer.allocate(v.size*8).order(order).also { b -> v.forEach { b.putInt(kotlin.math.round(it*1000000).toInt());b.putInt(1000000) } }.array()
        val tags=sortedMapOf<Int,Pair<Int,ByteArray>>()
        tags[254]=4 to longs(if(reduced) 1 else 0)
        tags[256]=4 to longs(6);tags[257]=4 to longs(8);tags[258]=3 to shorts(16)
        tags[259]=3 to shorts(compression);tags[262]=3 to shorts(32803)
        tags[274]=3 to shorts(orientation)
        tags[273]=4 to longs(0);tags[277]=3 to shorts(1);tags[278]=4 to longs(8);tags[279]=4 to longs(6*8*2)
        tags[33421]=3 to shorts(2,2);tags[33422]=1 to pattern.map(Int::toByte).toByteArray()
        tags[50706]=1 to byteArrayOf(1,4,0,0)
        tags[50713]=3 to shorts(2,2);tags[50714]=5 to rational(doubleArrayOf(64.0,128.0,192.0,256.0))
        tags[50717]=4 to longs(65535)
        tags[50721]=10 to rational(doubleArrayOf(1.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,1.0))
        tags[50728]=5 to rational(doubleArrayOf(.95047*.25,.25,1.08883*.25))
        if(requiredOpcode) tags[51008]=7 to ByteBuffer.allocate(20).order(ByteOrder.BIG_ENDIAN).putInt(1).putInt(1).putInt(0x01030000).putInt(0).putInt(0).array()
        val base=8+2+tags.size*12+4
        val imageOffset=base+tags.values.sumOf { if(it.second.size > 4) it.second.size else 0 }
        tags[273]=4 to longs(imageOffset)
        val output=ByteBuffer.allocate(imageOffset+6*8*2).order(order)
        output.put(if(order == ByteOrder.LITTLE_ENDIAN) byteArrayOf(73,73) else byteArrayOf(77,77))
        output.putShort(42);output.putInt(8);output.putShort(tags.size.toShort())
        var payload=base
        for((key,field) in tags) {
            val(type,value)=field
            val stride=when(type){3->2;4->4;5,10->8;else->1}
            output.putShort(key.toShort());output.putShort(type.toShort());output.putInt(value.size/stride)
            if(value.size <= 4) { output.put(value);repeat(4-value.size){output.put(0)} }
            else { output.putInt(payload);System.arraycopy(value,0,output.array(),payload,value.size);payload+=value.size }
        }
        output.putInt(0);output.position(imageOffset)
        val neutral=doubleArrayOf(.95047,1.0,1.08883)
        for(y in 0 until 8) for(x in 0 until 6) {
            val index=(y%2)*2+x%2
            val black=64.0*(index+1)
            val exposure=if(gradient) .05+.02*x+.04*y else .5
            output.putShort(kotlin.math.round(black+exposure*neutral[pattern[index]]*(65535-black)).toInt().toShort())
        }
        return output.array()
    }

    @Test fun actualRawSamplesDemosaicAllBayerOrdersEndiannessAndKeepHdrAcrossRowBoundaries() {
        for(order in listOf(ByteOrder.LITTLE_ENDIAN,ByteOrder.BIG_ENDIAN)) for(pattern in listOf(
            intArrayOf(0,1,1,2),intArrayOf(2,1,1,0),intArrayOf(1,0,2,1),intArrayOf(1,2,0,1))) {
            val bytes=fixture(order,pattern);val before=bytes.copyOf()
            val source=DngCapture(bytes)
            val rows=DngRawDecoder.open(source)
            val working=DefaultCaptureNormalizer().normalize(source)
            assertEquals(6,working.width);assertEquals(8,working.height)
            assertEquals(CaptureKind.SENSOR_RAW_DNG,working.record.kind)
            assertFalse(working.record.ispProcessed)
            assertEquals(DngRawDecoder.MODEL,working.record.sourceEncoding)
            assertTrue(working.linearRgb.all { kotlin.math.abs(it-2f) < .001f })
            for(start in 0 until rows.height step 3) {
                val n=minOf(3,rows.height-start)
                assertArrayEquals(working.linearRgb.copyOfRange(start*6*3,(start+n)*6*3),rows.readRows(start,n),0f)
            }
            assertArrayEquals(before,bytes)
        }
    }

    @Test fun unsupportedStorageAndPreviewOnlyDngFailWithoutJpegFallback() {
        assertThrows(CaptureNotReady::class.java){DngRawDecoder.decode(DngCapture(fixture(compression=7)))}
        assertThrows(CaptureNotReady::class.java){DngRawDecoder.decode(DngCapture(fixture(reduced=true)))}
        assertThrows(CaptureNotReady::class.java){DngRawDecoder.decode(DngCapture(fixture(requiredOpcode=true)))}
        val bytes=fixture()
        assertThrows(CaptureRejected::class.java){DngRawDecoder.decode(DngCapture(bytes.copyOf(bytes.size-3)))}
        assertThrows(CaptureRejected::class.java){DngRawDecoder.open(DngCapture(bytes,7,8))}
    }
}
