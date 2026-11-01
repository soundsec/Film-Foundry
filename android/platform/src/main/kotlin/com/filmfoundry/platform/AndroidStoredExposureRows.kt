package com.filmfoundry.platform

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import java.io.File

interface StoredExposurePixels: AutoCloseable {
    val width: Int
    val height: Int
    fun encodedRows(start: Int,count: Int): FloatArray
    fun configureCapture(record: Map<String,Any?>)=Unit
    fun encodedWindowRows(left: Int,top: Int,windowWidth: Int,count: Int): FloatArray {
        require(left >= 0 && left+windowWidth <= width && top >= 0 && top+count <= height)
        if(left == 0 && windowWidth == width) return encodedRows(top,count)
        val rows=encodedRows(top,count)
        return FloatArray(windowWidth*count*3).also { out ->
            for(y in 0 until count) rows.copyInto(out,y*windowWidth*3,(y*width+left)*3,(y*width+left+windowWidth)*3)
        }
    }
    fun linearWindowRows(left: Int,top: Int,windowWidth: Int,count: Int,kind: CaptureKind): FloatArray {
        require(kind==CaptureKind.DISPLAY_REFERRED_SRGB) { "This source reader cannot reconstruct HDR or RAW" }
        return encodedWindowRows(left,top,windowWidth,count).also { rgb -> for(i in rgb.indices) rgb[i]=TransferFunctions.srgbToLinear(rgb[i]) }
    }
}

/** Deferred source I/O only. SDR uses a bounded native stripe; HDR preserves the native gainmap. */
class AndroidStoredExposurePixels(private val file: File,private val compute: DeferredPixelCompute=DeferredPixelCompute(),
    private val memory: ()->ProcessingMemorySnapshot={ AndroidProcessingMemory.snapshot() },
    private val bounded: Boolean=true,private val report: (String)->Unit={},private val releaseCompute: Boolean=true): StoredExposurePixels {
    private val sourceBounds=BitmapFactory.Options().apply { inJustDecodeBounds=true;inScaled=false }.also {
        BitmapFactory.decodeFile(file.path,it)
        require(it.outWidth>0 && it.outHeight>0) { "Cannot read saved exposure dimensions" }
    }
    private var orientation=CapturePixelOrientation(sourceBounds.outWidth,sourceBounds.outHeight,1)
    private var bitmap: Bitmap?=null
    private var region: AndroidRegionPixels?=null
    private var regionAttempted=false
    private var needsHdr=false
    private var hdr: HdrGainSource?=null
    private var started=false
    private var closed=false
    override val width get()=orientation.width
    override val height get()=orientation.height
    override fun configureCapture(record: Map<String,Any?>) {
        check(!started && !closed)
        needsHdr=record["kind"]==CaptureKind.ULTRA_HDR_GAINMAP.wireName
        if(record["stored_pixel_orientation"]=="exif_v1") {
            val exif=android.media.ExifInterface(file.path).getAttributeInt(android.media.ExifInterface.TAG_ORIENTATION,1).let { if(it in 1..8) it else 1 }
            orientation=CapturePixelOrientation(sourceBounds.outWidth,sourceBounds.outHeight,exif)
        }
    }
    private fun fullBitmap(): Bitmap {
        check(!closed)
        bitmap?.let { return it }
        region?.close();region=null;regionAttempted=true
        memory().requireAllocation(sourceBounds.outWidth.toLong()*sourceBounds.outHeight*8+16L*1048576,"Saved exposure decode")
        return (BitmapFactory.decodeFile(file.path,BitmapFactory.Options().apply {
            inPreferredConfig=Bitmap.Config.ARGB_8888;inScaled=false
        }) ?: throw CaptureRejected("Cannot decode saved exposure")).also { bitmap=it;report("reference_full_bitmap_v1") }
    }
    private fun readEncoded(request: PixelRectangle,pixels: IntArray) {
        if(bounded && !needsHdr && bitmap==null && sourceBounds.outMimeType=="image/jpeg" && !regionAttempted) {
            regionAttempted=true
            memory().requireAllocation(2L*1048576,"Source region decoder")
            try {
                val reader=AndroidRegionPixels(file,memory)
                if(reader.width!=sourceBounds.outWidth || reader.height!=sourceBounds.outHeight) { reader.close();throw CaptureRejected("Source region geometry mismatch") }
                region=reader;report("sdr_region_argb8888_v1")
            } catch(error: java.io.IOException) { report("region_unavailable_reference_fallback_v1") }
        }
        region?.let { it.read(request,pixels);return }
        fullBitmap().getPixels(pixels,0,request.width,request.left,request.top,request.width,request.height)
    }
    override fun encodedRows(start: Int,count: Int)=encodedWindowRows(0,start,width,count)
    override fun close() {
        if(closed) return
        closed=true
        try { if(releaseCompute) compute.close() } finally { hdr=null;try { region?.close() } finally { region=null;bitmap?.recycle();bitmap=null } }
    }
    override fun linearWindowRows(left: Int,top: Int,windowWidth: Int,count: Int,kind: CaptureKind): FloatArray {
        if(kind==CaptureKind.DISPLAY_REFERRED_SRGB) return compute.linearize(encodedWindowRows(left,top,windowWidth,count))
        require(kind==CaptureKind.ULTRA_HDR_GAINMAP) { "Unsupported saved capture kind" }
        require(left>=0 && left+windowWidth<=width && top>=0 && top+count<=height)
        needsHdr=true
        val gain=hdr ?: run {
            val base=fullBitmap()
            val result=if(bounded) {
                memory().requireAllocation(1048576,"HDR gain sample cache")
                AndroidHdrGainMaps.openRows(base,orientation.exif).also { report("hdr_native_base_tiled_gain_v1") }
            } else AndroidHdrGainMaps.read(base) { bytes -> memory().requireAllocation(bytes,"HDR gain reconstruction") }?.let(orientation::orientGain)
            (result ?: throw CaptureRejected("Saved Ultra HDR has no usable gain map; SDR fallback is disabled")).also { hdr=it }
        }
        val rows=compute.linearize(encodedWindowRows(left,top,windowWidth,count))
        gain.applyWindowRowsInPlace(rows,width,height,left,top,windowWidth)
        return rows
    }
    override fun encodedWindowRows(left: Int,top: Int,windowWidth: Int,count: Int): FloatArray {
        check(!closed);require(left>=0 && top>=0 && windowWidth>0 && count>0 && left.toLong()+windowWidth<=width && top.toLong()+count<=height)
        started=true
        return ArgbRowConverter.convert(windowWidth,count) { pixels,row,rows ->
            if(orientation.exif==1) readEncoded(PixelRectangle(left,top+row,windowWidth,rows),pixels)
            else {
                val bottom=top+row+rows-1;val right=left+windowWidth-1
                val xs=intArrayOf(orientation.x(left,top+row),orientation.x(right,top+row),orientation.x(left,bottom),orientation.x(right,bottom))
                val ys=intArrayOf(orientation.y(left,top+row),orientation.y(right,top+row),orientation.y(left,bottom),orientation.y(right,bottom))
                val minX=xs.min();val minY=ys.min();val rw=xs.max()-minX+1;val rh=ys.max()-minY+1
                val raw=IntArray(rw*rh);readEncoded(PixelRectangle(minX,minY,rw,rh),raw)
                for(y in 0 until rows) for(x in 0 until windowWidth) pixels[y*windowWidth+x]=raw[(orientation.y(left+x,top+row+y)-minY)*rw+orientation.x(left+x,top+row+y)-minX]
            }
        }
    }
}
