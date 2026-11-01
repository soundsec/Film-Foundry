package com.filmfoundry.app.ui

/** Statistics of the exact 8-bit, display-encoded preview, after all scan adjustments. */
class DisplayHistogram private constructor(val width: Int,val height: Int,private val bins: Array<IntArray>) {
    val pixelCount: Int get()=width*height
    fun count(channel: Int,bin: Int)=bins[channel][bin]
    fun peak(channel: Int)=bins[channel].maxOrNull() ?: 0

    /** Display scale only: counts and the horizontal exposure axis are unchanged. */
    fun heights(channel: Int,logarithmic: Boolean=false,referencePeak: Int=peak(channel)): FloatArray {
        val peak=referencePeak.coerceAtLeast(1).toDouble()
        val divisor=if(logarithmic) kotlin.math.ln1p(peak) else peak
        return FloatArray(256) { bin ->
            val count=count(channel,bin).toDouble()
            ((if(logarithmic) kotlin.math.ln1p(count) else count)/divisor).toFloat().coerceIn(0f,1f)
        }
    }

    /** Share RGB heights. Endpoint spikes have separate indicators and cannot flatten the body. */
    fun plotPeak(channels: IntRange=0..3): Int {
        require(channels.first>=0 && channels.last<=3 && !channels.isEmpty())
        var interior=0;var all=0
        for(channel in channels) {
            for(bin in 1..254) interior=maxOf(interior,count(channel,bin))
            all=maxOf(all,peak(channel))
        }
        return (if(interior>0) interior else all).coerceAtLeast(1)
    }
    fun clippedChannels(highlights: Boolean): Int {
        val bin=if(highlights) 255 else 0
        return (if(count(1,bin)>0) 0xFF0000 else 0) or (if(count(2,bin)>0) 0x00FF00 else 0) or (if(count(3,bin)>0) 0x0000FF else 0)
    }

    class Builder(private val width: Int,private val height: Int) {
        private val bins=Array(4) { IntArray(256) }
        private var pixels=0
        init { require(width>0 && height>0 && width.toLong()*height<=Int.MAX_VALUE) }
        fun addArgb(pixel: Int) {
            val r=(pixel ushr 16) and 255;val g=(pixel ushr 8) and 255;val b=pixel and 255
            // Encoded brightness, matching the tone editor's channel weights; not scene luminance.
            bins[0][(2126*r+7152*g+722*b+5000)/10000]++
            bins[1][r]++;bins[2][g]++;bins[3][b]++;pixels++
        }
        fun addEncodedRgb(rgb: FloatArray,index: Int): Int=encodedPreviewArgb(rgb,index).also(::addArgb)
        fun finish(): DisplayHistogram {
            check(pixels==width*height) { "Incomplete display histogram" }
            return DisplayHistogram(width,height,Array(4) { bins[it].copyOf() })
        }
    }
}

/** The same final display quantization feeds both the visible bitmap and its histogram. */
internal fun encodedPreviewArgb(rgb: FloatArray,index: Int): Int {
    fun channel(c: Int)=(rgb[index+c].coerceIn(0f,1f)*255).toInt()
    return (255 shl 24) or (channel(0) shl 16) or (channel(1) shl 8) or channel(2)
}
