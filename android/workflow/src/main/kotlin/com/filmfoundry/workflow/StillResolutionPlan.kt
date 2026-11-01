package com.filmfoundry.workflow

import com.filmfoundry.interchange.StillFrameFormat

/** Capture planning only. Reuses the frozen aperture geometry; never resamples an exposure. */
object StillResolutionPlan {
    private fun sameSize(a: Pair<Int,Int>,b: Pair<Int,Int>) =
        a == b || a.first == b.second && a.second == b.first

    fun isMaximum(size: Pair<Int,Int>,sizes: List<Pair<Int,Int>>,format: String): Boolean =
        ranked(sizes,format).firstOrNull()?.source?.let { sameSize(size,it) } == true ||
            sizes.maxByOrNull { it.first.toLong()*it.second }?.let { sameSize(size,it) } == true

    /** A lens supplies real pixels. A held exposure never changes its source geometry. */
    fun forLens(requested: Pair<Int,Int>?,sizes: List<Pair<Int,Int>>,format: String,
        followMaximum: Boolean,held: Pair<Int,Int>?=null): Pair<Int,Int>? {
        val available=sizes.filter { it.first>0 && it.second>0 }.distinct()
        if(held!=null) return available.firstOrNull { sameSize(it,held) }
            ?: error("Advance the current frame before selecting a lens with different capture dimensions")
        if(requested==null) return null // Keep the explicit camera-default choice.
        require(available.isNotEmpty()) { "No supported capture dimensions for this lens and file format" }
        if(followMaximum) return ranked(available,format).first().source
        available.firstOrNull { sameSize(it,requested) }?.let { return it }
        // Retain small outputs where possible, without synthesizing unsupported dimensions.
        val budget=requested.first.toLong()*requested.second
        val within=available.filter { it.first.toLong()*it.second<=budget }
        val candidates=within.ifEmpty { listOf(available.minBy { it.first.toLong()*it.second }) }
        val ratio=maxOf(requested.first,requested.second).toDouble()/minOf(requested.first,requested.second)
        return candidates.minWith(compareBy<Pair<Int,Int>> {
            kotlin.math.abs(kotlin.math.ln((maxOf(it.first,it.second).toDouble()/minOf(it.first,it.second))/ratio))
        }.thenByDescending { it.first.toLong()*it.second })
    }
    data class Estimate(val source: Pair<Int,Int>,val gate: StillFrameFormat) {
        val sourcePixels: Long get()=source.first.toLong()*source.second
        val imagePixels: Long get()=gate.width.toLong()*gate.height
        val exposedPixels: Long get()=gate.openArea().let { it[2].toLong()*it[3] }
        val retainedFraction: Double get()=imagePixels.toDouble()/sourcePixels
    }

    fun estimate(size: Pair<Int,Int>,format: String,panorama: Boolean=false,
        model: String=StillFrameFormat.MODEL): Estimate =
        Estimate(size,StillFrameFormat.create(format,panorama,size.first,size.second,model))

    /** Only advertised normal outputs. Largest useful aperture wins, not the source MP label. */
    fun ranked(sizes: List<Pair<Int,Int>>,format: String): List<Estimate> = sizes
        .filter { it.first>0 && it.second>0 }.distinct().map { estimate(it,format) }
        .sortedWith(compareByDescending<Estimate> { it.imagePixels }
            .thenByDescending { it.retainedFraction }.thenBy { it.sourcePixels }
            .thenByDescending { it.source.first }.thenByDescending { it.source.second })
}
