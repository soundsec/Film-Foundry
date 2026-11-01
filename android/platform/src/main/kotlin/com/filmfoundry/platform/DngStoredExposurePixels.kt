package com.filmfoundry.platform

import java.io.File

/** Deferred sensor ingest with bounded file pages and RGB rows; original DNG remains read-only. */
class DngStoredExposurePixels(file: File): StoredExposurePixels {
    private val source=DngRawDecoder.open(file)
    private var orientation=CapturePixelOrientation(source.width,source.height,1)
    private var started=false
    override val width get()=orientation.width
    override val height get()=orientation.height
    override fun configureCapture(record: Map<String,Any?>) {
        check(!started)
        // Existing sensor_native_v1 receipts keep their frozen geometry and pixel ordering.
        orientation=CapturePixelOrientation(source.width,source.height,
            if(record["stored_pixel_orientation"]=="dng_exif_v1") source.exifOrientation else 1)
    }
    override fun encodedRows(start: Int,count: Int): FloatArray=throw CaptureRejected("RAW has no encoded sRGB rows")
    override fun linearWindowRows(left: Int,top: Int,windowWidth: Int,count: Int,kind: CaptureKind): FloatArray {
        require(kind==CaptureKind.SENSOR_RAW_DNG) { "DNG source requires sensor_raw_dng" }
        require(left>=0 && top>=0 && windowWidth>0 && count>0 && left.toLong()+windowWidth<=width && top.toLong()+count<=height)
        started=true
        if(orientation.exif==1) return source.readWindowRows(left,top,windowWidth,count)
        val right=left+windowWidth-1;val bottom=top+count-1
        val xs=intArrayOf(orientation.x(left,top),orientation.x(right,top),orientation.x(left,bottom),orientation.x(right,bottom))
        val ys=intArrayOf(orientation.y(left,top),orientation.y(right,top),orientation.y(left,bottom),orientation.y(right,bottom))
        val minX=xs.min();val minY=ys.min();val rw=xs.max()-minX+1;val rh=ys.max()-minY+1
        val raw=source.readWindowRows(minX,minY,rw,rh)
        return FloatArray(Math.multiplyExact(Math.multiplyExact(windowWidth,count),3)).also { out ->
            for(y in 0 until count) for(x in 0 until windowWidth) {
                val offset=((orientation.y(left+x,top+y)-minY)*rw+orientation.x(left+x,top+y)-minX)*3
                raw.copyInto(out,(y*windowWidth+x)*3,offset,offset+3)
            }
        }
    }
    override fun close()=source.close()
}

fun openStoredExposurePixels(file: File): StoredExposurePixels = if(file.name.endsWith(".dng")) DngStoredExposurePixels(file) else AndroidStoredExposurePixels(file)
