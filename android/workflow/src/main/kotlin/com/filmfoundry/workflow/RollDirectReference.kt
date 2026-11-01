package com.filmfoundry.workflow

/**
 * A roll-owned display thumbnail of the camera's first direct still.
 * It is not a medium, a latent, or a scan. Recommended finish may use its
 * display brightness and color spread; film black and white points stay measured
 * from the negative being scanned.
 */
object RollDirectReference {
    const val MODEL="phone_roll_direct_reference_v1"
    fun relativePaths(rollId: String): List<String> {
        require(rollId.isNotBlank() && rollId.none { it in "/\\:." }) { "roll id cannot be a path" }
        return listOf("$rollId/direct_reference.jpg","$rollId/direct_reference.json")
    }
    fun fromSrgb(argb: IntArray,width: Int,height: Int): Map<String,Any?> {
        require(width>0 && height>0 && argb.size==width*height)
        var red=0.0;var green=0.0;var blue=0.0
        val luma=DoubleArray(argb.size)
        for(i in argb.indices) {
            val r=((argb[i] shr 16) and 255)/255.0
            val g=((argb[i] shr 8) and 255)/255.0
            val b=(argb[i] and 255)/255.0
            red+=r;green+=g;blue+=b
            luma[i]=0.2126*r+0.7152*g+0.0722*b
        }
        val n=argb.size.toDouble();luma.sort()
        fun at(fraction: Double): Double {
            val x=fraction.coerceIn(0.0,1.0)*(luma.size-1)
            val i=x.toInt();val f=x-i
            return if(i>=luma.lastIndex) luma.last() else luma[i]*(1-f)+luma[i+1]*f
        }
        return mapOf("model" to MODEL,"width" to width,"height" to height,
            "mean_rgb" to listOf(red/n,green/n,blue/n),"luma_p10" to at(.10),"luma_p50" to at(.50),"luma_p90" to at(.90))
    }
    /** Nudge only the recommended print. Existing exposure or saturation choices stay. */
    fun recommend(scanner: Map<String,Any?>,look: Map<String,Any?>,reference: Map<String,Any?>,lockExposure: Boolean,lockColor: Boolean): Pair<Map<String,Any?>,Map<String,Any?>> {
        if(reference["model"] != MODEL) return scanner to look
        val p50=(reference["luma_p50"] as Number).toDouble()
        val mean=reference["mean_rgb"] as List<*>
        require(p50.isFinite() && mean.size==3) { "direct reference is incomplete" }
        val channels=mean.map { (it as Number).toDouble().also { n -> require(n.isFinite()) } }
        val ev=((p50-0.45)*0.8).coerceIn(-0.5,0.5)
        val chroma=channels.maxOrNull()!!-channels.minOrNull()!!
        val gain=1.0+(chroma-0.06).coerceIn(-0.05,0.10)
        val nextLook=if(lockExposure || "print_exposure_ev" in look) look else look+("print_exposure_ev" to ev)
        val saturation=((scanner["scan_saturation"] as? Number)?.toDouble() ?: 1.18)
        val nextScanner=if(lockColor) scanner else scanner+("scan_saturation" to (saturation*gain).coerceIn(0.0,3.0))
        return nextScanner to nextLook
    }
}
