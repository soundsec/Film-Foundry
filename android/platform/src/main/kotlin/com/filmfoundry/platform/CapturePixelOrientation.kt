package com.filmfoundry.platform

/** EXIF output-to-encoded coordinate map. No image allocation or device assumptions. */
class CapturePixelOrientation(val encodedWidth: Int,val encodedHeight: Int,val exif: Int) {
    init { require(encodedWidth>0 && encodedHeight>0 && exif in 1..8) }
    val width get()=if(exif>=5) encodedHeight else encodedWidth
    val height get()=if(exif>=5) encodedWidth else encodedHeight
    fun x(x: Int,y: Int): Int=when(exif) { 2,3 -> encodedWidth-1-x;5,6 -> y;7,8 -> encodedWidth-1-y;else -> x }
    fun y(x: Int,y: Int): Int=when(exif) { 3,4 -> encodedHeight-1-y;5,8 -> x;6,7 -> encodedHeight-1-x;else -> y }
    fun orientGain(source: HdrGainMap): HdrGainMap {
        if(exif==1) return source
        val map=CapturePixelOrientation(source.width,source.height,exif)
        val output=FloatArray(source.samples.size)
        for(y in 0 until map.height) for(x in 0 until map.width) {
            val start=(map.y(x,y)*source.width+map.x(x,y))*3
            source.samples.copyInto(output,(y*map.width+x)*3,start,start+3)
        }
        return HdrGainMap(map.width,map.height,output,source.ratioMin,source.ratioMax,source.gamma,source.epsilonSdr,source.epsilonHdr)
    }
}
