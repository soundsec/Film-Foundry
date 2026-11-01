package com.filmfoundry.kernel

/** Exact existing Float sRGB -> truncated byte, using monotonic Float boundaries. */
internal object DisplayByteEncoding {
    private fun reference(value: Float)=(PhoneObserve.encodeDisplaySample(value)*255).toInt()
    private val thresholds=IntArray(255) { n ->
        var lo=0;var hi=1f.toRawBits()
        while(lo<hi) {
            val mid=lo+(hi-lo)/2
            if(reference(Float.fromBits(mid))>=n+1) hi=mid else lo=mid+1
        }
        lo
    }
    private val coarse=IntArray(4097) { reference(it/4096f) }
    fun encode(linear: Float): Int {
        if(linear.isNaN() || linear<=0f) return 0
        if(linear>=1f) return 255
        val bits=linear.toRawBits()
        var code=coarse[(linear*4096).toInt()]
        while(code>0 && bits<thresholds[code-1]) code--
        while(code<255 && bits>=thresholds[code]) code++
        return code
    }
}
