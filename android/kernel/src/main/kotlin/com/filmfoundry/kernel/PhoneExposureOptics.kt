package com.filmfoundry.kernel

import kotlin.math.*

/** Explicit phone spatial models: full-grid separable MTF, bounded low-frequency highlight return. */
internal class PhoneExposureOptics(recipe: Map<String,Any?>,val width: Int,val height: Int,private val gain: Float) {
    private val film=recipe["film"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private val look=recipe["look"] as? Map<*,*> ?: emptyMap<Any,Any>()
    private fun number(key: String,default: Float,range: ClosedFloatingPointRange<Float>): Float {
        val v=film[key] ?: return default
        require(v is Number && v.toFloat().isFinite() && v.toFloat() in range) { "$key must be within $range" }
        return v.toFloat()
    }
    private val geometry=PhoneMaterialGeometry.resolve(recipe,height,width)
    private val referencePixels=geometry?.let { it.referenceShortEdgeMm*max(width,height)/it.longEdgeMm }?.toFloat() ?: min(width,height).toFloat()
    private val mtf=if(recipe["enable_mtf"] == false) 0f else (look["emulsion_mtf_strength"] as? Number)?.toFloat()
        ?.also { require(it.isFinite() && it in 0f..1f) } ?: number("emulsion_mtf_strength",0f,0f..1f)
    private val blurFraction=number("emulsion_blur_radius",.0012f,0f..0.05f)
    private val radius=if(mtf == 0f || blurFraction == 0f) 0 else max(1,(blurFraction*referencePixels).roundToInt())
    private val halo=if(recipe["enable_halation"] == false) 0f else number("halation_strength",0f,0f..2f)*
        ((look["halation_multiplier"] as? Number)?.toFloat() ?: 1f).also { require(it.isFinite() && it in 0f..5f) }
    private val threshold=number("halation_threshold",.72f,0f..16f)
    private val softness=number("halation_softness",.18f,.001f..16f)
    private val core=number("halation_core_radius",.004f,0f..0.25f)*referencePixels
    private val outer=number("halation_outer_radius",.018f,0f..0.25f)*referencePixels
    private val mix=number("halation_core_mix",.62f,0f..1f)
    private val peakThreshold=number("halation_peak_threshold",.12f,.001f..1f)
    private val areaSuppression=number("halation_area_suppression",.75f,0f..1f)
    private val color=(film["halation_color"] as? List<*>)?.let { raw ->
        require(raw.size == 3);FloatArray(3) { c -> (raw[c] as? Number)?.toFloat()?.also { require(it.isFinite() && it in 0f..4f) } ?: error("Invalid halation color") }
    } ?: floatArrayOf(1f,.42f,.16f)
    val enabled get()=mtf > 0f || halo > 0f
    init {
        require(radius <= 64) { "Phone MTF support exceeds 64 rows; reduce emulsion_blur_radius explicitly" }
        if(halo>0f) require(film["halation_return_model"] in listOf(null,"compatibility_rgb")) { "Phone halation currently supports compatibility_rgb return" }
    }
    fun record(): Map<String,Any?> = mapOf("model" to "phone_exposure_optics_v1",
        "mtf" to if(mtf>0f) "phone_separable_box_mtf_v1" else "omitted_v0","mtf_strength" to mtf,"mtf_radius_px" to radius,
        "halation" to if(halo>0f) "phone_highlight_return_grid_v1" else "omitted_v0","halation_strength" to halo,
        "halation_threshold" to threshold,"halation_softness" to softness,"halation_core_radius_px" to core,
        "halation_outer_radius_px" to outer,"halation_core_mix" to mix,"halation_color" to color.toList(),
        "halation_peak_threshold" to peakThreshold,"halation_area_suppression" to areaSuppression,
        "reference_short_edge_px" to referencePixels,"exposure_gain" to gain,"halation_return_model" to "compatibility_rgb",
        "halation_grid_long_edge_max" to GRID_EDGE,"boundary" to "clamp_material_edge","output_grid" to "unchanged")

    fun bind(source: LinearRgbRows): Binding {
        require(source.width == width && source.height == height)
        return Binding(source)
    }
    inner class Binding(private val source: LinearRgbRows): LinearRgbRows,AutoCloseable {
        override val width=this@PhoneExposureOptics.width
        override val height=this@PhoneExposureOptics.height
        private val filtered=BoxMtfRows(source,radius,mtf)
        private var haloField: FloatArray?=null
        private var ready=false
        private val gw=min(GRID_EDGE,width).let { if(width>=height) it else max(1,(width.toLong()*min(GRID_EDGE,height)/height).toInt()) }
        private val gh=if(height>=width) min(GRID_EDGE,height) else max(1,(height.toLong()*gw/width).toInt())
        fun preReturnRows(start: Int,count: Int)=filtered.readRows(start,count)
        override fun readRows(start: Int,count: Int): RgbVolume {
            require(start>=0 && count>0 && start+count<=height)
            if(!ready) { if(halo>0f) haloField=buildHalo();ready=true }
            val row=filtered.readRows(start,count)
            val light=haloField ?: return row
            val values=row.linearRgb.copyOf()
            for(y in 0 until count) for(x in 0 until width) {
                val u=(x+.5f)*gw/width-.5f;val v=(start+y+.5f)*gh/height-.5f
                val a=sample(light,gw,gh,u,v)*halo/gain
                val i=(y*width+x)*3
                for(c in 0..2) values[i+c]+=a*color[c]
            }
            return RgbVolume(width,count,linearRgb=values)
        }
        private fun buildHalo(): FloatArray? {
            val sum=DoubleArray(gw*gh);val excess=DoubleArray(gw*gh);val peaks=FloatArray(gw*gh);val counts=IntArray(gw*gh)
            val step=max(1,16384/width)
            for(start in 0 until height step step) {
                val n=min(step,height-start);val rows=filtered.readRows(start,n).linearRgb
                for(y in 0 until n) for(x in 0 until width) {
                    val i=(y*width+x)*3
                    val luma=max(0f,(rows[i]*.2126f+rows[i+1]*.7152f+rows[i+2]*.0722f)*gain)
                    val g=min(gh-1,(start+y)*gh/height)*gw+min(gw-1,x*gw/width)
                    sum[g]+=luma;peaks[g]=max(peaks[g],luma);counts[g]++
                    val high=max(0f,luma-threshold);val gate=(high/softness).coerceIn(0f,1f)
                    excess[g]+=high*gate*gate*(3f-2f*gate)
                }
            }
            val mean=FloatArray(sum.size) { (sum[it]/max(1,counts[it])).toFloat() }
            val local=blur(mean,gw,gh,1.1f,source::checkActive)
            var nonzero=false
            val energy=FloatArray(mean.size) { i ->
                val peak=((peaks[i]-local[i])/max(peakThreshold*max(peaks[i],1e-6f),1e-6f)).coerceIn(0f,1f)
                val area=(local[i]/max(peaks[i],1e-6f)).coerceIn(0f,1f)
                // Cell mean preserves isolated highlight energy rather than expanding its maximum.
                ((excess[i]/max(1,counts[i])).toFloat()*peak*(1f-areaSuppression*area)).also { if(it>0f) nonzero=true }
            }
            if(!nonzero) return null
            val scale=min(gw.toFloat()/width,gh.toFloat()/height)
            val a=blur(energy,gw,gh,max(.6f,core*scale),source::checkActive)
            val b=blur(energy,gw,gh,max(.6f,outer*scale),source::checkActive)
            for(i in a.indices) a[i]=a[i]*mix+b[i]*(1f-mix)
            return a
        }
        override fun close() { filtered.clear();haloField=null }
    }
    companion object {
        const val KEY="phone_exposure_optics"
        const val GRID_EDGE=384
        internal fun sample(v: FloatArray,w: Int,h: Int,x: Float,y: Float): Float {
            val x0=floor(x).toInt();val y0=floor(y).toInt();val tx=x-x0;val ty=y-y0
            fun at(a: Int,b: Int)=v[b.coerceIn(0,h-1)*w+a.coerceIn(0,w-1)]
            return (at(x0,y0)*(1-tx)+at(x0+1,y0)*tx)*(1-ty)+(at(x0,y0+1)*(1-tx)+at(x0+1,y0+1)*tx)*ty
        }
        internal fun blur(source: FloatArray,w: Int,h: Int,sigma: Float,check: ()->Unit): FloatArray {
            val r=ceil(sigma*3).toInt().coerceIn(1,288)
            val weights=DoubleArray(2*r+1) { exp(-((it-r).toDouble().pow(2))/(2*sigma*sigma)) }
            val total=weights.sum();for(i in weights.indices) weights[i]/=total
            val horizontal=FloatArray(source.size);val out=FloatArray(source.size)
            for(y in 0 until h) { check();val row=y*w
                for(x in 0 until minOf(r,w)) { var sum=0.0;for(k in -r..r) sum+=source[row+(x+k).coerceIn(0,w-1)]*weights[k+r];horizontal[row+x]=sum.toFloat() }
                for(x in r until (w-r).coerceAtLeast(r)) { var sum=0.0;val i=row+x;for(k in -r..r) sum+=source[i+k]*weights[k+r];horizontal[i]=sum.toFloat() }
                for(x in maxOf(r,w-r) until w) { var sum=0.0;for(k in -r..r) sum+=source[row+(x+k).coerceIn(0,w-1)]*weights[k+r];horizontal[row+x]=sum.toFloat() }
            }
            for(y in 0 until h) { check()
                if(y<r || y>=h-r) for(x in 0 until w) { var sum=0.0;for(k in -r..r) sum+=horizontal[(y+k).coerceIn(0,h-1)*w+x]*weights[k+r];out[y*w+x]=sum.toFloat() }
                else for(x in 0 until w) { var sum=0.0;for(k in -r..r) sum+=horizontal[(y+k)*w+x]*weights[k+r];out[y*w+x]=sum.toFloat() }
            }
            return out
        }
    }
}

/** Finite separable box PSF with mixed original signal; bounded rows, full-resolution output. */
private class BoxMtfRows(private val source: LinearRgbRows,private val radius: Int,private val strength: Float): LinearRgbRows {
    override val width=source.width;override val height=source.height
    private data class Row(val original: FloatArray,val horizontal: FloatArray)
    private val cache=object: LinkedHashMap<Int,Row>(16,.75f,true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<Int,Row>?)=size>2*radius+4
    }
    fun clear()=cache.clear()
    private fun row(y: Int): Row=cache.getOrPut(y.coerceIn(0,height-1)) {
        val input=source.readRows(y.coerceIn(0,height-1),1).linearRgb
        require(input.all { it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE }) { "Non-finite optical exposure source" }
        val out=FloatArray(input.size);val span=2*radius+1
        for(c in 0..2) {
            var sum=0.0
            for(k in -radius..radius) sum+=input[k.coerceIn(0,width-1)*3+c]
            for(x in 0 until width) {
                out[x*3+c]=(sum/span).toFloat()
                if(x+1<width) { sum-=input[(x-radius).coerceIn(0,width-1)*3+c];sum+=input[(x+radius+1).coerceIn(0,width-1)*3+c] }
            }
        }
        Row(input,out)
    }
    override fun readRows(start: Int,count: Int): RgbVolume {
        if(radius==0 || strength==0f) return source.readRows(start,count)
        val out=FloatArray(width*count*3);val sum=DoubleArray(width*3);val span=2*radius+1
        for(k in -radius..radius) { val v=row(start+k).horizontal;for(i in sum.indices) sum[i]+=v[i] }
        for(y in start until start+count) {
            val center=row(y).original;val offset=(y-start)*width*3
            for(i in sum.indices) out[offset+i]=center[i]*(1f-strength)+(sum[i]/span).toFloat()*strength
            if(y+1<start+count) {
                val left=row(y-radius).horizontal;val right=row(y+radius+1).horizontal
                for(i in sum.indices) { sum[i]-=left[i];sum[i]+=right[i] }
                if(y-radius>=0) cache.remove(y-radius)
            }
        }
        return RgbVolume(width,count,linearRgb=out)
    }
}
