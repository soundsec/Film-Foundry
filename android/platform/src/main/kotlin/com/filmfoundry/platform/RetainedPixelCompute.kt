package com.filmfoundry.platform

/** One optional GPU linearizer for the formation worker. Each source file borrows it and does not release it. */
class RetainedPixelCompute(private val requested: ()->Boolean,private val report: (String)->Unit={}) {
    private var compute: DeferredPixelCompute?=null
    fun open(): DeferredPixelCompute {
        val current=compute
        if(current!=null) return current
        return DeferredPixelCompute(requested(),report=report).also { compute=it }
    }
    fun close() {
        val current=compute
        compute=null
        current?.close()
    }
}
