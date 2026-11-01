package com.filmfoundry.platform

import java.io.*
import java.nio.ByteBuffer
import java.nio.ByteOrder

enum class ScanImageFormat(val extension: String,val mime: String) {
    PNG("png","image/png"),JPEG("jpg","image/jpeg"),TIFF("tif","image/tiff")
}

/** TIFF consumes the 16-bit scanner samples, never an up-converted display bitmap. */
object ScanTiffRows {
    // Generated standard sRGB profile (LittleCMS built-in primaries/TRC), not a third-party photo asset.
    private val srgb=java.util.Base64.getDecoder().decode("AAACTGxjbXMEQAAAbW50clJHQiBYWVogB+oACgAfABIAAQAoYWNzcE1TRlQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPbWAAEAAAAA0y1sY21zAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAALZGVzYwAAAQgAAAA2Y3BydAAAAUAAAABMd3RwdAAAAYwAAAAUY2hhZAAAAaAAAAAsclhZWgAAAcwAAAAUYlhZWgAAAeAAAAAUZ1hZWgAAAfQAAAAUclRSQwAAAggAAAAgZ1RSQwAAAggAAAAgYlRSQwAAAggAAAAgY2hybQAAAigAAAAkbWx1YwAAAAAAAAABAAAADGVuVVMAAAAaAAAAHABzAFIARwBCACAAYgB1AGkAbAB0AC0AaQBuAABtbHVjAAAAAAAAAAEAAAAMZW5VUwAAADAAAAAcAE4AbwAgAGMAbwBwAHkAcgBpAGcAaAB0ACwAIAB1AHMAZQAgAGYAcgBlAGUAbAB5WFlaIAAAAAAAAPbWAAEAAAAA0y1zZjMyAAAAAAABDEIAAAXe///zJQAAB5MAAP2Q///7of///aIAAAPcAADAblhZWiAAAAAAAABvoAAAOPUAAAOQWFlaIAAAAAAAACSfAAAPhAAAtsNYWVogAAAAAAAAYpcAALeHAAAY2XBhcmEAAAAAAAMAAAACZmYAAPKnAAANWQAAE9AAAApbY2hybQAAAAAAAwAAAACj1wAAVHsAAEzNAACZmgAAJmYAAA9c")
    fun write(input: InputStream,output: OutputStream,check: ()->Unit={}) {
        var little=ByteArray(0)
        ScanPngRows.read(input,check,{ w,h,depth ->
            require(depth==16) { "TIFF requires a new 16-bit scan; rescan this older photo first" }
            val count=13;val bits=8+2+count*12+4;val samples=bits+6;val profileOffset=samples+6;val pixels=profileOffset+srgb.size
            val length=w.toLong()*h*6;require(length+pixels<0xFFFFFFFFL)
            val b=ByteBuffer.allocate(pixels).order(ByteOrder.LITTLE_ENDIAN)
            b.put('I'.code.toByte()).put('I'.code.toByte()).putShort(42).putInt(8).putShort(count.toShort())
            fun tag(id: Int,type: Int,n: Int,value: Int) { b.putShort(id.toShort()).putShort(type.toShort()).putInt(n).putInt(value) }
            tag(256,4,1,w);tag(257,4,1,h);tag(258,3,3,bits);tag(259,3,1,1);tag(262,3,1,2)
            tag(273,4,1,pixels);tag(274,3,1,1);tag(277,3,1,3);tag(278,4,1,h)
            tag(279,4,1,length.toInt());tag(284,3,1,1);tag(339,3,3,samples);tag(34675,7,srgb.size,profileOffset)
            b.putInt(0);repeat(3) { b.putShort(16) };repeat(3) { b.putShort(1) };b.put(srgb);output.write(b.array());little=ByteArray(w*6)
        },{ _,row ->
            check()
            for(i in row.indices step 2) { little[i]=row[i+1];little[i+1]=row[i] }
            output.write(little)
        })
    }
}

object ScanImageExport {
    fun write(storage: StorageAdapter,path: String,format: ScanImageFormat,angle: Int,output: OutputStream,check: ()->Unit={},jpegQuality: Int=95) {
        require(jpegQuality in 1..100)
        val rotation=OutputOrientation.normalized(angle)
        if(path.endsWith(".jpg",true)) {
            require(format==ScanImageFormat.JPEG) { "JPEG cannot be exported to a higher-quality format" }
            if(rotation==0) { storage.readPrivate(path) { it.copyTo(output,65536) };check();return }
            val bounds=android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds=true }
            storage.readPrivate(path) { android.graphics.BitmapFactory.decodeStream(it,null,bounds) }
            val runtime=Runtime.getRuntime();val free=runtime.maxMemory()-(runtime.totalMemory()-runtime.freeMemory())
            require(bounds.outWidth>0 && bounds.outHeight>0 && bounds.outWidth.toLong()*bounds.outHeight*8+16L*1048576<free) { "Not enough memory to rotate JPEG" }
            val bitmap=storage.readPrivate(path) { android.graphics.BitmapFactory.decodeStream(it) } ?: error("Cannot decode JPEG")
            var rotated: android.graphics.Bitmap?=null
            try {
                check();rotated=android.graphics.Bitmap.createBitmap(bitmap,0,0,bitmap.width,bitmap.height,android.graphics.Matrix().apply { postRotate(rotation.toFloat()) },false)
                require(rotated.compress(android.graphics.Bitmap.CompressFormat.JPEG,jpegQuality,output));check()
            } finally { if(rotated!==bitmap) rotated?.recycle();bitmap.recycle() };return
        }
        val temporary=if(rotation==0) null else File.createTempFile("scan-turn-",".png",storage.rowScratchRoot() ?: error("Temporary storage unavailable"))
        try {
            if(temporary!=null) temporary.outputStream().buffered(65536).use { PngPhotoRotation.write(storage,path,rotation,it,check) }
            fun <T> source(read: (InputStream)->T): T=if(temporary!=null) temporary.inputStream().buffered(65536).use(read) else storage.readPrivate(path,read)
            when(format) {
                ScanImageFormat.PNG -> source { input -> val buffer=ByteArray(65536);while(true) { check();val n=input.read(buffer);if(n<0) break;output.write(buffer,0,n) } }
                ScanImageFormat.TIFF -> source { ScanTiffRows.write(it,output,check) }
                ScanImageFormat.JPEG -> {
                    val bounds=android.graphics.BitmapFactory.Options().apply { inJustDecodeBounds=true }
                    source { android.graphics.BitmapFactory.decodeStream(it,null,bounds) }
                    require(bounds.outWidth>0 && bounds.outHeight>0)
                    val runtime=Runtime.getRuntime();val free=runtime.maxMemory()-(runtime.totalMemory()-runtime.freeMemory())
                    require(bounds.outWidth.toLong()*bounds.outHeight*4+16L*1048576<free) { "Not enough memory for full-resolution JPEG; use PNG or TIFF" }
                    check()
                    val bitmap=source { android.graphics.BitmapFactory.decodeStream(it,null,android.graphics.BitmapFactory.Options().apply { inPreferredConfig=android.graphics.Bitmap.Config.ARGB_8888 }) } ?: error("Cannot decode scan")
                    try { check();require(bitmap.compress(android.graphics.Bitmap.CompressFormat.JPEG,jpegQuality,output));check() } finally { bitmap.recycle() }
                }
            }
        } finally { temporary?.delete() }
    }
}
