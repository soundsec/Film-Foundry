package com.filmfoundry.platform

/** Reference encoded gain samples; retained for the in-memory capture API. */
class HdrGainMap(
    override val width: Int,
    override val height: Int,
    val samples: FloatArray,
    override val ratioMin: FloatArray,
    override val ratioMax: FloatArray,
    override val gamma: FloatArray,
    override val epsilonSdr: FloatArray,
    override val epsilonHdr: FloatArray,
): HdrGainSource {
    init {
        if(width<=0 || height<=0 || samples.size!=Math.multiplyExact(Math.multiplyExact(width,height),3))
            throw CaptureRejected("HDR gain-map dimensions do not match RGB samples.")
        if(samples.any { !it.isFinite() || it !in 0f..1f })
            throw CaptureRejected("HDR gain samples must be finite encoded values in [0,1].")
        validateHdrParameters(this)
    }
    override fun sample(x: Int,y: Int,channel: Int)=samples[(y*width+x)*3+channel]
}
