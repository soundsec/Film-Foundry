package com.filmfoundry.app.ui

import com.filmfoundry.interchange.PiecewiseCurve
import com.filmfoundry.interchange.RecipeJson

/** Small editable curve state, detached from the scanner definition and image buffers. */
class ScannerCurveState {
    data class Draft(val curves: List<List<com.filmfoundry.interchange.CurvePoint>>,val toneEnabled: Boolean,val rgbEnabled: Boolean)
    private var curves=Array(4) { PiecewiseCurve.identity() }
    var channel=0;private set
    private var toneEnabled=false
    private var rgbEnabled=false
    val curve get()=curves[channel]
    val enabled get()=if(channel==0) toneEnabled else rgbEnabled
    fun snapshot()=Draft(curves.map { it.points.toList() },toneEnabled,rgbEnabled)
    fun restore(draft: Draft) { curves=draft.curves.map(PiecewiseCurve::from).toTypedArray();toneEnabled=draft.toneEnabled;rgbEnabled=draft.rgbEnabled }
    /** A missing session draft means initialize from its recipe, not keep another session's disabled points. */
    fun restore(scanner: Map<String,Any?>,draft: Draft?) {
        if(draft==null) bind(scanner,force=true) else restore(draft)
    }
    fun select(channel: Int) { require(channel in 0..3);this.channel=channel }
    fun edit(curve: PiecewiseCurve) { curves[channel]=curve;enable(true) }
    fun enable(enabled: Boolean) { if(channel==0) toneEnabled=enabled else rgbEnabled=enabled }
    fun payload(): Map<String,Any?> = mapOf("tone_curve" to if(toneEnabled) curves[0].payload() else null,
        "color_curve_rgb" to if(rgbEnabled) (1..3).map { curves[it].payload() } else null)
    fun bind(scanner: Map<String,Any?>,force: Boolean=false): Boolean {
        val selected=mapOf("tone_curve" to scanner["tone_curve"],"color_curve_rgb" to scanner["color_curve_rgb"])
        if(!force && RecipeJson.equivalent(payload(),selected)) return false
        val next=Array(4) { PiecewiseCurve.identity() }
        scanner["tone_curve"]?.let { next[0]=PiecewiseCurve.parse(it) }
        scanner["color_curve_rgb"]?.let { raw ->
            val channels=raw as? List<*> ?: error("RGB curves need three channels")
            require(channels.size==3);repeat(3) { next[it+1]=PiecewiseCurve.parse(channels[it],true) }
        }
        curves=next;toneEnabled=scanner["tone_curve"]!=null;rgbEnabled=scanner["color_curve_rgb"]!=null
        return true
    }
}
