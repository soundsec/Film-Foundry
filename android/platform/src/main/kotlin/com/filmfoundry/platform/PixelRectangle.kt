package com.filmfoundry.platform

internal data class PixelRectangle(val left: Int,val top: Int,val width: Int,val height: Int) {
    val right get()=left+width
    val bottom get()=top+height
    fun contains(other: PixelRectangle)=other.left>=left && other.top>=top && other.right<=right && other.bottom<=bottom
}

/** Extend along the short (streaming) axis only, also for EXIF-rotated rows. */
internal fun cachedPixelStripe(request: PixelRectangle,width: Int,height: Int): PixelRectangle {
    require(request.width>0 && request.height>0 && request.left>=0 && request.top>=0 && request.right<=width && request.bottom<=height)
    return if(request.width>=request.height) {
        val step=(65536/request.width).coerceIn(1,64);val top=request.top/step*step
        PixelRectangle(request.left,top,request.width,minOf(height,maxOf(request.bottom,top+step))-top)
    } else {
        val step=(65536/request.height).coerceIn(1,64);val left=request.left/step*step
        PixelRectangle(left,request.top,minOf(width,maxOf(request.right,left+step))-left,request.height)
    }
}
