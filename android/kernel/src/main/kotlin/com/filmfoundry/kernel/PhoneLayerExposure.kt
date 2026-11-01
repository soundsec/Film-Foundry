package com.filmfoundry.kernel

import kotlin.math.*

/** Independent pre-latent layer additions. Bounded grids and O(width+height) edge profiles. */
internal class PhoneLayerExposure(recipe: Map<String,Any?>,private val width: Int,private val height: Int,
    private val layers: Int,private val referencePixels: Float,private val gain: Float,
    private val mapped: (Float,Float,Float,Int)->Float) {
    private val film=recipe["film"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private val look=recipe["look"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private fun n(key: String,default: Float,range: ClosedFloatingPointRange<Float>)=number(film,key,default,range)
    private fun vector(key: String,default: List<Float>,positive: Boolean=false,normalize: Boolean=false): FloatArray {
        val raw=film[key] ?: default;require(raw is List<*> && raw.size==3) { "$key requires three values" }
        val values=FloatArray(3) { i->val v=raw[i];require(v is Number && v.toFloat().isFinite() && v.toFloat()>=0f && v.toFloat()<=8f && (!positive || v.toFloat()>0f)) { "invalid $key" };v.toFloat() }
        val result=if(layers==1) floatArrayOf(values.average().toFloat()) else values
        if(normalize) { val max=result.maxOrNull()!!;if(max>0f) for(i in result.indices) result[i]/=max }
        return result
    }
    private val piping=n("light_piping_strength",0f,0f..2f)
    private val depth=n("light_piping_depth",.035f,.000001f..1f)
    private val edge=film["light_piping_edge_mode"]?.toString() ?: "none"
    private val pipingWeights=vector("light_piping_layer_weights",listOf(1f,.45f,.18f),normalize=true)
    private val halo=if(recipe["enable_halation"]==false || film["halation_return_model"]!="layer_selective") 0f else
        n("halation_strength",0f,0f..2f)*number(look,"halation_multiplier",1f,0f..5f)
    private val haloWeights=vector("halation_layer_return_weights",listOf(1f,.42f,.16f),normalize=true)
    private val spreadWeights=(film["halation_spread_scale_weights"] ?: listOf(0f,1f,0f)).let { raw->
        require(raw is List<*> && raw.size==3)
        FloatArray(3) { val v=raw[it];require(v is Number && v.toFloat().isFinite() && v.toFloat() in 0f..8f);v.toFloat() }.also { values->
            val sum=values.sum();require(sum>0f);for(i in values.indices) values[i]/=sum
        }
    }
    private val threshold=n("halation_threshold",.72f,0f..16f);private val softness=n("halation_softness",.18f,.001f..16f)
    private val core=n("halation_core_radius",.004f,0f.. .25f)*referencePixels
    private val outer=n("halation_outer_radius",.018f,0f.. .25f)*referencePixels
    private val coreMix=n("halation_core_mix",.62f,0f..1f)
    private val peak=n("halation_peak_threshold",.12f,.001f..1f);private val area=n("halation_area_suppression",.75f,0f..1f)
    private val emulsion=n("emulsion_highlight_spread_strength",0f,0f..2f)
    private val emulsionRadius=n("emulsion_highlight_spread_radius",.0014f,.000001f.. .1f)*referencePixels
    private val emulsionThreshold=n("emulsion_highlight_spread_threshold",.72f,0f..16f)
    private val emulsionSoftness=n("emulsion_highlight_spread_softness",.14f,.001f..16f)
    private val emulsionRadii=vector("emulsion_highlight_spread_layer_radius_scales",listOf(.85f,1f,1.15f),positive=true)
    private val emulsionWeights=vector("emulsion_highlight_spread_layer_strengths",listOf(.9f,1f,1.08f))
    val enabled get()=halo>0f || emulsion>0f || piping>0f && edge!="none"
    private val gw=min(width,GRID_EDGE).let { if(width>=height) it else max(1,(width.toLong()*min(height,GRID_EDGE)/height).toInt()) }
    private val gh=if(height>=width) min(height,GRID_EDGE) else max(1,(height.toLong()*gw/width).toInt())
    init { require(edge in EDGES) { "unsupported light_piping_edge_mode: $edge" }
        require(film["halation_return_model"] in listOf(null,"compatibility_rgb","layer_selective")) { "unsupported halation return model" }
        val scale=min(gw.toFloat()/width,gh.toFloat()/height)
        fun supported(sigma: Float) { require(sigma<=96f) { "Phone layer spread exceeds 288 grid-cell support; reduce the radius or layer scale explicitly" } }
        if(halo>0f) {
            if(spreadWeights[0]>0f) supported(core*.35f*scale)
            if(spreadWeights[1]>0f) { if(coreMix>0f) supported(core*scale);if(coreMix<1f) supported(outer*scale) }
            if(spreadWeights[2]>0f) supported(outer*2.4f*scale)
        }
        if(emulsion>0f) for(l in 0 until layers) if(emulsionWeights[l]>0f) supported(emulsionRadius*emulsionRadii[l]*scale)
    }
    fun record()=mapOf("model" to MODEL,"stage" to "pre_latent_layer_exposure","reference_short_edge_px" to referencePixels,
        "work_grid" to if(halo>0f || emulsion>0f) listOf(gh,gw) else null,"work_long_edge_limit" to GRID_EDGE,
        "halation" to if(halo>0f) "phone_layer_selective_return_v1" else "omitted_v0","halation_strength" to halo,
        "halation_layer_return_weights" to haloWeights.toList(),"halation_spread_scale_weights" to spreadWeights.toList(),
        "emulsion_highlight_spread" to if(emulsion>0f) "phone_layer_highlight_grid_v1" else "omitted_v0",
        "emulsion_strength" to emulsion,"emulsion_radius_px" to emulsionRadius,"emulsion_threshold" to emulsionThreshold,
        "emulsion_softness" to emulsionSoftness,"emulsion_layer_radii" to emulsionRadii.toList(),"emulsion_layer_strengths" to emulsionWeights.toList(),
        "light_piping" to if(piping>0f && edge!="none") "phone_declared_edge_exposure_v1" else "omitted_v0",
        "light_piping_strength" to piping,"light_piping_edge_mode" to edge,"light_piping_depth" to depth,"light_piping_layer_weights" to pipingWeights.toList(),
        "boundary" to "clamp_material_edge","gaussian_support_radius_max" to 288,"output_grid" to "unchanged","randomness" to "none")
    fun bind(source: LinearRgbRows): Binding { require(source.width==width && source.height==height);return Binding(source) }
    inner class Binding(private val source: LinearRgbRows): AutoCloseable {
        private var closed=false
        private var haloField: FloatArray?=null
        private var emulsionFields: Array<FloatArray>?=null
        private var horizontal: FloatArray?=null;private var vertical: FloatArray?=null
        init {
            if(piping>0f && edge!="none") {
                val longHorizontal=width>=height
                val top=edge in listOf("top","all_edges") || edge=="long_edges" && longHorizontal || edge=="short_edges" && !longHorizontal
                val bottom=edge in listOf("bottom","all_edges") || edge=="long_edges" && longHorizontal || edge=="short_edges" && !longHorizontal
                val left=edge in listOf("left","all_edges") || edge=="long_edges" && !longHorizontal || edge=="short_edges" && longHorizontal
                val right=edge in listOf("right","all_edges") || edge=="long_edges" && !longHorizontal || edge=="short_edges" && longHorizontal
                val pixels=max(1f,depth*referencePixels)
                horizontal=FloatArray(width) { x->max(if(left) exp(-x/pixels) else 0f,if(right) exp(-(width-1-x)/pixels) else 0f) }
                vertical=FloatArray(height) { y->max(if(top) exp(-y/pixels) else 0f,if(bottom) exp(-(height-1-y)/pixels) else 0f) }
            }
            if(halo>0f || emulsion>0f) build()
        }
        private fun high(value: Float,threshold: Float,softness: Float): Float {
            val excess=max(0f,value-threshold);val t=(excess/softness).coerceIn(0f,1f)
            return excess*t*t*(3-2*t)
        }
        private fun build() {
            val count=IntArray(gw*gh);val mean=if(halo>0f) DoubleArray(gw*gh) else null
            val peaks=if(halo>0f) FloatArray(gw*gh) else null;val excess=if(halo>0f) DoubleArray(gw*gh) else null
            val layerSources=if(emulsion>0f) Array(layers) { DoubleArray(gw*gh) } else null
            val step=max(1,16384/width)
            for(start in 0 until height step step) {
                source.checkActive();val n=min(step,height-start);val rows=source.readRows(start,n)
                require(rows.width==width && rows.height==n)
                for(y in 0 until n) { source.checkActive();for(x in 0 until width) {
                    val i=(y*width+x)*3;val r=max(0f,rows.linearRgb[i]*gain);val g=max(0f,rows.linearRgb[i+1]*gain);val b=max(0f,rows.linearRgb[i+2]*gain)
                    require(r.isFinite() && g.isFinite() && b.isFinite())
                    val cell=((start+y).toLong()*gh/height).toInt()*gw+(x.toLong()*gw/width).toInt();count[cell]++
                    if(mean!=null) { val l=r*.2126f+g*.7152f+b*.0722f;mean[cell]+=l;peaks!![cell]=max(peaks[cell],l);excess!![cell]+=high(l,threshold,softness) }
                    if(layerSources!=null) for(l in 0 until layers) if(emulsionWeights[l]>0f) layerSources[l][cell]+=high(mapped(r,g,b,l),emulsionThreshold,emulsionSoftness)
                } }
            }
            val scale=min(gw.toFloat()/width,gh.toFloat()/height)
            if(mean!=null) {
                val image=FloatArray(mean.size) { (mean[it]/count[it]).toFloat() }
                val local=PhoneExposureOptics.blur(image,gw,gh,1.1f,source::checkActive)
                val energy=FloatArray(mean.size) { i->val p=peaks!![i];val gate=((p-local[i])/max(peak*max(p,1e-6f),1e-6f)).coerceIn(0f,1f)
                    (excess!![i]/count[i]).toFloat()*gate*(1-area*(local[i]/max(p,1e-6f)).coerceIn(0f,1f)) }
                if(energy.any { it>0f }) {
                    val field=FloatArray(energy.size)
                    fun add(sigma: Float,weight: Float) { if(weight>0f) { val blurred=PhoneExposureOptics.blur(energy,gw,gh,max(.6f,sigma*scale),source::checkActive);for(i in field.indices) field[i]+=blurred[i]*weight } }
                    add(core*.35f,spreadWeights[0]);add(core,spreadWeights[1]*coreMix);add(outer,spreadWeights[1]*(1-coreMix));add(outer*2.4f,spreadWeights[2])
                    haloField=field
                }
            }
            if(layerSources!=null) emulsionFields=Array(layers) { l ->
                if(emulsionWeights[l]==0f) FloatArray(count.size) else {
                    val field=FloatArray(count.size) { (layerSources[l][it]/count[it]).toFloat() }
                    PhoneExposureOptics.blur(field,gw,gh,max(.35f,emulsionRadius*emulsionRadii[l]*scale),source::checkActive)
                }
            }
            source.checkActive()
        }
        fun addition(x: Int,y: Int,layer: Int): Float {
            check(!closed)
            var amount=if(horizontal!=null) max(horizontal!![x],vertical!![y])*piping*pipingWeights[layer] else 0f
            if(haloField==null && emulsionFields==null) return amount
            val u=(x+.5f)*gw/width-.5f;val v=(y+.5f)*gh/height-.5f
            haloField?.let { amount+=PhoneExposureOptics.sample(it,gw,gh,u,v)*halo*haloWeights[layer] }
            emulsionFields?.let { amount+=PhoneExposureOptics.sample(it[layer],gw,gh,u,v)*emulsion*emulsionWeights[layer] }
            return amount
        }
        override fun close() { closed=true;haloField=null;emulsionFields=null;horizontal=null;vertical=null }
    }
    companion object {
        const val KEY="phone_layer_exposure"
        const val MODEL="phone_layer_exposure_additions_v1"
        const val GRID_EDGE=384
        val EDGES=com.filmfoundry.interchange.PhoneMaterialPhenomena.LIGHT_PIPING_EDGES
        private fun number(values: Map<*,*>,key: String,default: Float,range: ClosedFloatingPointRange<Float>): Float {
            val v=values[key] ?: return default
            require(v is Number && v.toFloat().isFinite() && v.toFloat() in range) { "$key must be within $range" };return v.toFloat()
        }
    }
}
