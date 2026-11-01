package com.filmfoundry.platform

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.BitmapRegionDecoder
import android.graphics.Rect
import java.io.File

/** A native codec with one bounded stripe Bitmap; no whole decoded SDR source. */
internal class AndroidRegionPixels(file: File,private val memory: ()->ProcessingMemorySnapshot): AutoCloseable {
    @Suppress("DEPRECATION")
    private val decoder=BitmapRegionDecoder.newInstance(file.path,false) ?: throw java.io.IOException("Cannot open region decoder")
    val width get()=decoder.width
    val height get()=decoder.height
    private var bounds: PixelRectangle?=null
    private var bitmap: Bitmap?=null
    fun read(request: PixelRectangle,pixels: IntArray) {
        if(bounds?.contains(request)!=true) {
            bitmap?.recycle();bitmap=null;bounds=null
            val stripe=cachedPixelStripe(request,width,height)
            memory().requireAllocation(stripe.width.toLong()*stripe.height*8+2L*1048576,"Source decode stripe")
            val next=decoder.decodeRegion(Rect(stripe.left,stripe.top,stripe.right,stripe.bottom),BitmapFactory.Options().apply {
                inPreferredConfig=Bitmap.Config.ARGB_8888;inSampleSize=1;inScaled=false
            }) ?: throw CaptureRejected("Cannot decode source stripe; the original file was retained")
            if(next.width!=stripe.width || next.height!=stripe.height) { next.recycle();throw CaptureRejected("Region codec changed source geometry") }
            bitmap=next;bounds=stripe
        }
        val tile=checkNotNull(bounds)
        checkNotNull(bitmap).getPixels(pixels,0,request.width,request.left-tile.left,request.top-tile.top,request.width,request.height)
    }
    override fun close() { bitmap?.recycle();bitmap=null;bounds=null;decoder.recycle() }
}
