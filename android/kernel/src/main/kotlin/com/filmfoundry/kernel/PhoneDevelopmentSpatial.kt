package com.filmfoundry.kernel

import kotlin.math.*

/** Parameter-only validation for independent material and processing editors. */
fun validatePhoneDevelopmentSpatial(film: Map<String,Any?> = emptyMap(),develop: Map<String,Any?> = emptyMap()) =
    PhoneDevelopmentSpatial.validate(film,develop)

/** Declared phone reduction: provisional chemistry on a bounded, full-frame reaction grid.
 * Source pools are read only. Only the later authoritative program consumes material.
 * No scanner adjustment, per-tile statistics, platform SDK or full-size auxiliary fields.
 */
internal class PhoneDevelopmentSpatial(
    film: Map<String,Any?>,develop: Map<String,Any?>,private val sourceMetadata: Map<String,Any?>,
    private val width: Int,private val height: Int,private val layers: Int,
    private val color: Boolean,private val program: PhoneProcessProgram,
    private val effects: PhoneFormationEffects,
) {
    private val matrix=matrix(film)
    private val adjacency=number(develop,"development_adjacency_strength",0.0,0.0..1.0).toFloat()
    private val adjacencyRadius=number(develop,"development_adjacency_radius",.0025,.000001..1.0)
    private val dirRadius=number(film,"dir_diffusion_radius",.003,.000001..1.0)
    private val effectiveness=number(develop,"dir_process_effectiveness",if(program.reversal) 0.0 else 1.0,0.0..1.0).toFloat()
    private val dirActive=color && program.hasColor && effectiveness>0f && matrix.any { row->row.any { it>0f } }
    private val adjacencyActive=adjacency>0f && program.hasDevelopment
    val enabled get()=dirActive || adjacencyActive
    val requested get()=adjacency>0f || matrix.any { row->row.any { it>0f } }
    private val dirReason get()=when {
        !color -> "no_color_coupling"
        !program.hasColor -> "no_color_development"
        effectiveness==0f -> if(program.reversal && "dir_process_effectiveness" !in developParameters) "reversal_default_off" else "effectiveness_zero"
        matrix.all { row->row.all { it==0f } } -> "zero_matrix"
        else -> "active"
    }
    private val developParameters=develop
    fun inactiveRecord()=mapOf("model" to MODEL,"enabled" to false,"dir_reason" to dirReason,
        "adjacency_reason" to if(!program.hasDevelopment) "no_development" else "strength_zero",
        "work_grid" to null,"provisional_pool_consumptions" to 0)
    private val gw=min(width,MAX_EDGE)
    private val gh=min(height,MAX_EDGE).let { if(width>=height) max(1,(height.toLong()*gw/width).toInt()) else it }
    private val gridWidth=if(height>width) max(1,(width.toLong()*gh/height).toInt()) else gw
    private val referencePixels=(sourceMetadata["material_geometry"] as? Map<*,*>)?.let {
        (it["reference_short_edge_mm"] as Number).toDouble()*max(width,height)/(it["frame_long_edge_mm"] as Number).toDouble()
    } ?: min(width,height).toDouble()
    private val gridScale=min(gridWidth.toDouble()/width,gh.toDouble()/height)
    private val amplitude=(.28*adjacency*(1.4/(.4+program.firstDevelopmentAgitation)).coerceIn(.45,1.5)).toFloat()

    class Fields(private val width: Int,private val height: Int,private val gw: Int,private val gh: Int,
        private var adjacency: FloatArray?,private var inhibitor: Array<FloatArray>?,val record: Map<String,Any?>): AutoCloseable {
        private var closed=false
        // The same absolute coordinate is sampled for every layer and field. Preserve
        // the original double expression, preparing only O(width + height) coordinates.
        private class Axis(size: Int,grid: Int) {
            val index=IntArray(size);val weight=FloatArray(size)
            init { for(i in 0 until size) {
                val f=((i+.5)*grid/size-.5).coerceIn(0.0,(grid-1).toDouble())
                index[i]=f.toInt();weight[i]=(f-index[i]).toFloat()
            } }
        }
        private val columns=Axis(width,gw)
        private val rows=Axis(height,gh)
        private fun sample(a: FloatArray,x: Int,y: Int): Float {
            check(!closed) { "development spatial binding is closed" }
            val ix=columns.index[x];val iy=rows.index[y];val tx=columns.weight[x];val ty=rows.weight[y]
            val nx=min(ix+1,gw-1);val ny=min(iy+1,gh-1)
            return (a[iy*gw+ix]*(1-tx)+a[iy*gw+nx]*tx)*(1-ty)+(a[ny*gw+ix]*(1-tx)+a[ny*gw+nx]*tx)*ty
        }
        fun firstRate(x: Int,y: Int): Float { check(!closed);return adjacency?.let { 1f+sample(it,x,y) } ?: 1f }
        fun colorRate(x: Int,y: Int,layer: Int): Float { check(!closed);return inhibitor?.let { 1f-sample(it[layer],x,y).coerceIn(0f,1f) } ?: 1f }
        override fun close() { closed=true;adjacency=null;inhibitor=null }
    }

    fun bind(source: LatentRows): Fields {
        require(source.width==width && source.height==height)
        val size=gridWidth*gh
        val counts=IntArray(size)
        val sums=Array(if(dirActive) 3 else 0) { DoubleArray(size) }
        val silver=if(adjacencyActive) DoubleArray(size) else null
        val p=PhoneProcessProgram.Pools();val projection=PhoneProcessProgram.Projection()
        val step=source.readRowsPerPass.coerceIn(1,height)
        for(start in 0 until height step step) {
            source.checkActive()
            val count=min(step,height-start);val block=source.readRows(start,count)
            require(block.metadata==sourceMetadata) { "latent source metadata changed during spatial preparation" }
            fun values(key: String,required: Boolean=true): FloatArray? {
                val a=block.arrays[key]
                require(!required || a!=null) { "missing latent pool $key" }
                if(a!=null) require(a.shape.contentEquals(intArrayOf(count,width,layers)) && a.values.all { it.isFinite() && it>=0f }) { "invalid latent pool $key" }
                return a?.values
            }
            val h=values("halide")!!;val a=values("developability")!!;val s=values("metallic_silver")!!
            val c=values("coupler",color);val d=values("dye",color);val b=values("bleached_halide",false)
            for(yy in 0 until count) {
                source.checkActive();val y=start+yy;val gy=(y.toLong()*gh/height).toInt()
                for(x in 0 until width) {
                    val cell=gy*gridWidth+(x.toLong()*gridWidth/width).toInt();counts[cell]++
                    val local=effects.completion(1f,x,y)
                    for(l in 0 until layers) {
                        val i=(yy*width+x)*layers+l
                        p.halide=h[i];p.activation=a[i];p.silver=s[i];p.coupler=c?.get(i) ?: 0f;p.dye=d?.get(i) ?: 0f;p.bleached=b?.get(i) ?: 0f
                        program.apply(p,color,local,l,projection=projection)
                        if(silver!=null) silver[cell]+=(if(h[i]>1e-8f) projection.firstSilver/h[i] else 0f)/layers
                        if(dirActive) sums[l][cell]+=(if((c?.get(i) ?: 0f)>1e-8f) (projection.firstColorDye-(d?.get(i) ?: 0f))/c!![i] else 0f).coerceIn(0f,1f).toDouble()
                    }
                }
            }
        }
        source.checkActive()
        val dir=if(dirActive) Array(3) { FloatArray(size) } else null
        if(dir!=null) for(l in 0..2) {
            val field=FloatArray(size) { (sums[l][it]/counts[it]).toFloat() }
            val blurred=blur(field,dirRadius*referencePixels*gridScale,source::checkActive)
            for(t in 0..2) for(i in 0 until size) dir[t][i]+=blurred[i]*matrix[l][t]*effectiveness
        }
        var rms=0.0
        val edge=if(silver!=null) {
            val field=FloatArray(size) { (silver[it]/counts[it]).toFloat() }
            val blurred=blur(field,adjacencyRadius*referencePixels*gridScale,source::checkActive)
            val residual=DoubleArray(size) { (field[it]-blurred[it]).toDouble() }
            val mean=residual.average();for(i in residual.indices) residual[i]-=mean
            rms=sqrt(residual.sumOf { it*it }/size)
            if(rms<=1e-7) FloatArray(size) else {
                for(i in residual.indices) residual[i]=tanh(residual[i]/(2*rms))
                val center=residual.average();for(i in residual.indices) residual[i]-=center
                val peak=residual.maxOf { abs(it) }.coerceAtLeast(1e-12)
                FloatArray(size) { (residual[it]/peak*amplitude).toFloat() }
            }
        } else null
        source.checkActive()
        return Fields(width,height,gridWidth,gh,edge,dir,mapOf(
            "model" to MODEL,"enabled" to true,"work_grid" to listOf(gh,gridWidth),"work_long_edge_limit" to MAX_EDGE,
            "source" to "read_only_provisional_program","authoritative_pool_passes" to 1,
            "provisional_pool_consumptions" to 0,
            "boundary" to "reflect101","sampling" to "cell_mean_bilinear_absolute_coordinates",
            "dir" to mapOf("enabled" to dirActive,"reason" to dirReason,"stage" to "first_color_development","effectiveness" to effectiveness,
                "matrix_source_to_target" to matrix.map { it.toList() },"radius_reference_short_edge" to dirRadius),
            "adjacency" to mapOf("enabled" to adjacencyActive,"stage" to "first_development","strength" to adjacency,
                "radius_reference_short_edge" to adjacencyRadius,"residual_rms" to rms,"amplitude" to amplitude,
                "effective_first_bath_agitation" to program.firstDevelopmentAgitation)))
    }
    private fun blur(input: FloatArray,sigmaInput: Double,checkActive: ()->Unit): FloatArray {
        val sigma=sigmaInput.coerceAtLeast(.45);val radius=ceil(3*sigma).toInt().coerceAtLeast(1)
        val weights=DoubleArray(2*radius+1) { exp(-.5*(it-radius)*(it-radius)/(sigma*sigma)) }
        val norm=weights.sum();for(i in weights.indices) weights[i]/=norm
        fun reflected(index: Int,n: Int): Int { if(n==1) return 0;val period=2*(n-1);val p=((index%period)+period)%period;return if(p<n) p else period-p }
        val tmp=FloatArray(input.size);val output=FloatArray(input.size)
        for(y in 0 until gh) { checkActive();for(x in 0 until gridWidth) {
            var sum=0.0;for(k in -radius..radius) sum+=input[y*gridWidth+reflected(x+k,gridWidth)]*weights[k+radius]
            tmp[y*gridWidth+x]=sum.toFloat()
        } }
        for(y in 0 until gh) { checkActive();for(x in 0 until gridWidth) {
            var sum=0.0;for(k in -radius..radius) sum+=tmp[reflected(y+k,gh)*gridWidth+x]*weights[k+radius]
            output[y*gridWidth+x]=sum.toFloat()
        } }
        return output
    }
    companion object {
        const val MODEL="phone_provisional_grid_reactions_v1"
        const val MAX_EDGE=512
        private fun number(values: Map<String,Any?>,key: String,default: Double,range: ClosedFloatingPointRange<Double>): Double {
            val raw=values[key] ?: return default
            require(raw is Number && raw.toDouble().isFinite() && raw.toDouble() in range) { "$key must be in $range" }
            return raw.toDouble()
        }
        private fun matrix(film: Map<String,Any?>): Array<FloatArray> {
            val raw=film["dir_material_response_matrix"] ?: return Array(3) { FloatArray(3) }
            require(raw is List<*> && raw.size==3) { "DIR requires a 3×3 source-to-target matrix" }
            val result=Array(3) { y ->
                val row=raw[y];require(row is List<*> && row.size==3)
                FloatArray(3) { x -> val n=row[x];require(n is Number && n.toDouble().isFinite() && n.toDouble() in 0.0..0.95);n.toFloat() }
            }
            for(t in 0..2) require((0..2).sumOf { result[it][t].toDouble() }<=.950001) { "DIR target column sum exceeds .95" }
            return result
        }
        fun validate(film: Map<String,Any?> = emptyMap(),develop: Map<String,Any?> = emptyMap()) {
            matrix(film);number(film,"dir_diffusion_radius",.003,.000001..1.0)
            number(develop,"development_adjacency_strength",0.0,0.0..1.0)
            number(develop,"development_adjacency_radius",.0025,.000001..1.0)
            number(develop,"dir_process_effectiveness",1.0,0.0..1.0)
        }
    }
}
