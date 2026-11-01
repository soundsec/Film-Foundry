package com.filmfoundry.workflow

import com.filmfoundry.interchange.FloatNd

/** Synthetic samples only. Borrowed arrays are read-only; no latent or library resource is retained.
 * The most recent effect and optional clean comparison share one parameter preparation per stock.
 */
class CameraEffectsPreviewCache(private val budgetBytes: Long=12L*1024*1024) {
    init { require(budgetBytes>0) }
    private data class Key(val camera: Map<String,Any?>,val ev: Double)
    private class Entry(val request: CameraEffectsPreview.Request) {
        val pipeline=CameraEffectsPreview.Pipeline(request)
        var effect: Pair<Key,FloatNd>?=null
        var clean: Pair<Double,FloatNd>?=null
        val retainedBytes get()=pipeline.sampleBytes+(effect?.second?.values?.size ?: 0)*4L+(clean?.second?.values?.size ?: 0)*4L
    }
    private val entries=ArrayList<Entry>()
    internal var computations=0;private set
    val retainedBytes: Long get()=synchronized(entries) { entries.sumOf { it.retainedBytes } }
    fun clear()=synchronized(entries) { entries.clear() }
    private fun trim()=synchronized(entries) {
        while(entries.size>2 || entries.sumOf { it.retainedBytes }>budgetBytes) entries.removeAt(0)
    }
    /** Computation happens on the caller's serial preview worker, outside the cache lock. */
    fun render(request: CameraEffectsPreview.Request,camera: Map<String,Any?>,exposureEv: Double=0.0,
        comparison: Boolean=false,check: ()->Unit={}): FloatNd {
        require(exposureEv.isFinite() && exposureEv in -4.0..4.0)
        check()
        val entry=synchronized(entries) {
            val found=entries.indexOfFirst { it.request.matches(request) }
            (if(found>=0) entries.removeAt(found) else Entry(request)).also { entries.add(it) }
        }
        try {
            val key=Key(if(comparison) emptyMap() else frozenRecipe(camera),exposureEv)
            val result=if(comparison || key.camera.isEmpty()) {
                entry.clean?.takeIf { it.first==exposureEv }?.second ?: entry.pipeline.image(emptyMap(),exposureEv,true,check).also {
                    computations++;entry.clean=exposureEv to it
                }
            } else {
                entry.effect?.takeIf { it.first==key }?.second ?: entry.pipeline.image(key.camera,exposureEv,true,check).also {
                    computations++;entry.effect=key to it
                }
            }
            check();return result
        } finally {
            trim()
        }
    }
}
