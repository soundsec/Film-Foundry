package com.filmfoundry.app.ui

/** Display allocation and touch geometry only; saved pixels and scan settings are untouched. */
object PhotoDisplayPolicy {
    private const val MAX_BITMAP_BYTES=64L*1024*1024
    fun sample(width: Int,height: Int,availableBytes: Long): Int {
        require(width>0 && height>0)
        // Leave room for the outgoing image, decoder scratch and platform display resources.
        val pixels=(availableBytes.coerceAtLeast(0)/3).coerceAtMost(MAX_BITMAP_BYTES).div(4).coerceAtLeast(1)
        var sample=1
        while(((width.toLong()+sample-1)/sample)*((height.toLong()+sample-1)/sample)>pixels && sample<(1 shl 30)) sample*=2
        return sample
    }
    /** Avoid rounding a requested 1200-pixel preview down to 844 pixels. */
    fun sampleForEdge(width: Int,height: Int,edge: Int): Int {
        require(width>0 && height>0 && edge>0)
        var sample=1
        while(sample<(1 shl 30) && maxOf(width,height).toLong()/(sample.toLong()*2)>=edge) sample*=2
        return sample
    }
    data class Pan(val x: Float,val y: Float,val overflowX: Float)
    fun pan(x: Float,y: Float,moveX: Float,moveY: Float,boundX: Float,boundY: Float): Pan {
        val raw=x+moveX;val clamped=raw.coerceIn(-boundX,boundX)
        return Pan(clamped,(y+moveY).coerceIn(-boundY,boundY),raw-clamped)
    }
    fun commitPage(distance: Float,speed: Float,width: Int,zoomed: Boolean,touchSlop: Float): Boolean {
        if(width<=0 || distance==0f) return false
        val direction=if(distance>0) 1 else -1
        val minimum=if(zoomed) maxOf(touchSlop*3,width*.06f) else touchSlop
        return kotlin.math.abs(distance)>width*.2f || kotlin.math.abs(distance)>minimum && speed*direction>600f
    }
}
