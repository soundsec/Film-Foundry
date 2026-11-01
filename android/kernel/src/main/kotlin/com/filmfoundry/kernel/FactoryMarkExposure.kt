package com.filmfoundry.kernel

import kotlin.math.*

/** Bounded, film-relative exposure footprint. No scan-space blur or full-frame scratch. */
internal class FactoryMarkExposure(width: Int,height: Int,private val rebate: Int,private val film: Map<String,Any?>,
    spread: Double,mtf: Boolean,halation: Boolean,bandHeight: Double,private val mask: (Int,Int,Boolean)->Boolean) {
    private val fullWidth=width
    private val w=minOf(width,512);private val h=minOf(rebate,96)
    private val levels=16
    private fun number(key: String,fallback: Double)=(film[key] as? Number)?.toDouble()?.takeIf { it.isFinite() } ?: fallback
    // A restrained stylistic scatter budget for small factory lettering. Reusing
    // full scene-highlight strengths swamps the narrow strokes after development.
    private val factoryScatter=.25
    private val strength=if(halation) number("halation_strength",.12).coerceIn(0.0,3.0)*factoryScatter else 0.0
    private val mtfMix=if(mtf) number("emulsion_mtf_strength",.25).coerceIn(0.0,1.0)*factoryScatter else 0.0
    private val color=(film["halation_color"] as? List<*>)?.takeIf { it.size==3 }?.map { (it as? Number)?.toDouble()?.takeIf(Double::isFinite) ?: 0.0 }
        ?: listOf(1.0,.42,.16)
    val sampleRgb=FloatArray(levels*levels*3) { i ->
        val core=(i/3/levels)/(levels-1.0);val halo=((i/3%levels)/(levels-1.0)).pow(2)
        (1.8*(core+strength*halo*color[i%3].coerceIn(0.0,3.0))).toFloat()
    }
    private val fields=Array(2) { edge ->
        val sharp=FloatArray(w*h) { i -> mask(minOf(width-1,((i%w+.5)*width/w).toInt()),minOf(rebate-1,((i/w+.5)*rebate/h).toInt()),edge==0).let { if(it) 1f else 0f } }
        val short=minOf(width,height).toDouble()
        val coreRadius=if(mtf) number("emulsion_blur_radius",.0012).coerceIn(0.0,.05)*short*spread else 0.0
        val core=blur(sharp,coreRadius*w/width,coreRadius*h/rebate)
        // A factory stencil occupies a narrow band. Keep its scatter local instead of
        // borrowing the radius of a scene-sized highlight as a broad text outline.
        val haloRadius=minOf(number("halation_outer_radius",.018).coerceIn(0.0,.2)*short,bandHeight*.12)*spread
        val halo=if(strength>0 && spread>0) blur(sharp,haloRadius*w/width,haloRadius*h/rebate) else FloatArray(w*h)
        FloatArray(w*h*2) { i ->
            val p=i/2
            if(i%2==0) core[p]*(levels-1)
            else (sqrt(halo[p].toDouble().coerceIn(0.0,1.0))*(levels-1)).toFloat()
        }
    }
    /** Interpolate both the footprint and formed material table; no stepped palette. */
    fun writePixel(x: Int,outward: Int,top: Boolean,source: FloatArray,channels: Int,target: FloatArray,offset: Int) {
        val field=fields[if(top) 0 else 1]
        val u=((x+.5f)*w/fullWidth-.5f).coerceIn(0f,(w-1).toFloat())
        val v=((outward+.5f)*h/rebate-.5f).coerceIn(0f,(h-1).toFloat())
        val x0=u.toInt();val y0=v.toInt();val x1=minOf(x0+1,w-1);val y1=minOf(y0+1,h-1)
        val dx=u-x0;val dy=v-y0
        fun coordinate(c: Int): Float {
            val a=field[(y0*w+x0)*2+c]*(1-dx)+field[(y0*w+x1)*2+c]*dx
            val b=field[(y1*w+x0)*2+c]*(1-dx)+field[(y1*w+x1)*2+c]*dx
            return (a*(1-dy)+b*dy).coerceIn(0f,(levels-1).toFloat())
        }
        // Preserve the native stencil boundary. Only its scattered component uses
        // the small grid; interpolating the binary beam itself would blur every glyph.
        val sharp=if(mask(x,outward,top)) (levels-1).toFloat() else 0f
        val beam=(sharp*(1-mtfMix)+coordinate(0)*mtfMix).toFloat();val glow=coordinate(1)
        val b0=beam.toInt();val g0=glow.toInt();val b1=minOf(b0+1,levels-1);val g1=minOf(g0+1,levels-1)
        val db=beam-b0;val dg=glow-g0
        val a=(b0*levels+g0)*channels;val b=(b1*levels+g0)*channels
        val c=(b0*levels+g1)*channels;val d=(b1*levels+g1)*channels
        for(channel in 0 until channels) {
            val low=source[a+channel]*(1-db)+source[b+channel]*db
            val high=source[c+channel]*(1-db)+source[d+channel]*db
            target[offset+channel]=low*(1-dg)+high*dg
        }
    }
    private fun blur(source: FloatArray,sigmaX: Double,sigmaY: Double): FloatArray {
        fun pass(input: FloatArray,sigma: Double,horizontal: Boolean): FloatArray {
            if(sigma<.25) return input
            val radius=ceil(sigma*3).toInt().coerceIn(1,32)
            val weights=DoubleArray(radius*2+1) { exp(-((it-radius).toDouble()/sigma).pow(2)/2) };val sum=weights.sum()
            for(i in weights.indices) weights[i]/=sum
            return FloatArray(w*h) { i ->
                val x=i%w;val y=i/w;var value=0.0
                for(d in -radius..radius) {
                    val px=if(horizontal) x+d else x;val py=if(horizontal) y else y+d
                    if(px in 0 until w && py in 0 until h) value+=input[py*w+px]*weights[d+radius]
                }
                value.toFloat()
            }
        }
        return pass(pass(source,sigmaX,true),sigmaY,false)
    }
}
