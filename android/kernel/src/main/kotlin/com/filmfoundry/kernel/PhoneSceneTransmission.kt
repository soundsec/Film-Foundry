package com.filmfoundry.kernel

import kotlin.math.pow

/** A fixed film sheet in front of a spatial RGB scene. Display only; no inversion or material edits. */
class PhoneSceneTransmission(optical: OpticalMaster,checkCancel: ()->Unit={}) {
    private val filmWidth=optical.opticalDensityRgb.shape[1]
    private val filmHeight=optical.opticalDensityRgb.shape[0]
    private val transmission=FloatArray(optical.opticalDensityRgb.values.size) { i ->
        if(i%(filmWidth*3)==0) checkCancel()
        10.0.pow((-optical.opticalDensityRgb.values[i].coerceAtLeast(0f)).toDouble()).toFloat().coerceAtLeast(1e-6f)
    }
    private data class Layer(val width: Int,val height: Int,val zoom: Float,val x: Float,val y: Float,
        val left: Float,val top: Float,val fw: Float,val fh: Float,val samples: FloatArray)
    private var cached: Layer?=null
    private fun layer(w: Int,h: Int,zoom: Float,x: Float,y: Float,check: ()->Unit): Layer {
        require(w>0 && h>0 && zoom.isFinite() && zoom in 1f..5f && x.isFinite() && y.isFinite())
        cached?.let { if(it.width==w && it.height==h && it.zoom==zoom && it.x==x && it.y==y) return it }
        val scale=minOf(w*.86f/filmWidth,h*.86f/filmHeight)*zoom
        val fw=filmWidth*scale;val fh=filmHeight*scale;val left=(w-fw)/2;val top=(h-fh)/2
        val px=left+x*w;val py=top+y*h
        val previous=cached;cached=null
        val samples=previous?.takeIf { it.width==w && it.height==h }?.samples?.also { it.fill(-1f) }
            ?: FloatArray(w*h*3) { -1f }
        for(gy in 0 until h) {
            check()
            for(gx in 0 until w) if(gx+.5f>=px && gx+.5f<px+fw && gy+.5f>=py && gy+.5f<py+fh)
                sample(gx,gy,px,py,scale,samples,(gy*w+gx)*3)
        }
        check()
        return Layer(w,h,zoom,x,y,px,py,fw,fh,samples).also { cached=it }
    }
    private fun sample(x: Int,y: Int,left: Float,top: Float,scale: Float,out: FloatArray,index: Int) {
        val sx=((x+.5f-left)/scale-.5f).coerceIn(0f,(filmWidth-1).toFloat())
        val sy=((y+.5f-top)/scale-.5f).coerceIn(0f,(filmHeight-1).toFloat())
        val x0=sx.toInt();val y0=sy.toInt();val fx=sx-x0;val fy=sy-y0
        val a=(y0*filmWidth+x0)*3;val b=(y0*filmWidth+minOf(x0+1,filmWidth-1))*3
        val c=(minOf(y0+1,filmHeight-1)*filmWidth+x0)*3;val d=(minOf(y0+1,filmHeight-1)*filmWidth+minOf(x0+1,filmWidth-1))*3
        for(channel in 0..2) out[index+channel]=transmission[a+channel]*(1-fx)*(1-fy)+transmission[b+channel]*fx*(1-fy)+
            transmission[c+channel]*(1-fx)*fy+transmission[d+channel]*fx*fy
    }
    @Synchronized fun render(scene: LinearRgbRows,zoom: Float=1f,x: Float=0f,y: Float=0f): ScanObservation {
        val w=scene.width;val h=scene.height;val layer=layer(w,h,zoom,x,y,scene::checkActive)
        val out=FloatArray(w*h*3)
        for(start in 0 until h step 32) {
            scene.checkActive();val count=minOf(32,h-start);val rows=scene.readRows(start,count)
            require(rows.width==w && rows.height==count)
            for(y in 0 until count) {
                val gy=start+y
                for(x in 0 until w) {
                    val at=(gy*w+x)*3;val inside=layer.samples[at]>=0f
                    for(channel in 0..2) {
                        val light=rows.linearRgb[(y*w+x)*3+channel]
                        out[at+channel]=if(inside) (layer.samples[at+channel]*light.coerceAtLeast(1e-6f)).coerceIn(1e-6f,1f) else light
                    }
                }
            }
        }
        scene.checkActive();PhoneObserve.encodeRows(out,emptyMap(),true)
        return ScanObservation(com.filmfoundry.interchange.FloatNd(intArrayOf(h,w,3),out),mapOf("observe_mode" to "viewer_light_table",
            "scene_transmission" to mapOf("model" to "phone_live_rgb_film_sheet_v1","film_rect" to listOf(layer.left/w,layer.top/h,layer.fw/w,layer.fh/h),
                "polarity" to "unchanged","light" to "spatial_linear_rgb","display_only" to true)))
    }
    /** Same readout and final 8-bit truncation as the viewer, in a caller-owned packed buffer. */
    @Synchronized fun renderArgb(scene: LinearRgbRows,out: IntArray,zoom: Float=1f,x: Float=0f,y: Float=0f) {
        val w=scene.width;val h=scene.height;require(out.size==w*h)
        val layer=layer(w,h,zoom,x,y,scene::checkActive)
        for(start in 0 until h step 32) {
            scene.checkActive();val count=minOf(32,h-start);val rows=scene.readRows(start,count)
            require(rows.width==w && rows.height==count)
            for(local in 0 until count*w) {
                val at=(start*w+local)*3;val inside=layer.samples[at]>=0f
                var packed=0xff000000.toInt()
                for(c in 0..2) {
                    val light=rows.linearRgb[local*3+c]
                    val linear=if(inside) (layer.samples[at+c]*light.coerceAtLeast(1e-6f)).coerceIn(1e-6f,1f) else light
                    packed=packed or (DisplayByteEncoding.encode(linear) shl ((2-c)*8))
                }
                out[start*w+local]=packed
            }
        }
        scene.checkActive()
    }
    /** Developer fallback: the preceding per-frame bilinear sampling and whole RGB output. */
    internal fun renderReference(scene: LinearRgbRows,zoom: Float=1f,x: Float=0f,y: Float=0f): FloatArray {
        val w=scene.width;val h=scene.height
        val scale=minOf(w*.86f/filmWidth,h*.86f/filmHeight)*zoom
        val fw=filmWidth*scale;val fh=filmHeight*scale;val left=(w-fw)/2+x*w;val top=(h-fh)/2+y*h
        val out=FloatArray(w*h*3);val t=FloatArray(3)
        for(start in 0 until h step 32) {
            scene.checkActive();val count=minOf(32,h-start);val rows=scene.readRows(start,count)
            for(ly in 0 until count) for(gx in 0 until w) {
                val gy=start+ly;val inside=gx+.5f>=left && gx+.5f<left+fw && gy+.5f>=top && gy+.5f<top+fh
                if(inside) sample(gx,gy,left,top,scale,t,0)
                for(c in 0..2) {
                    val light=rows.linearRgb[(ly*w+gx)*3+c]
                    out[(gy*w+gx)*3+c]=if(inside) (t[c]*light.coerceAtLeast(1e-6f)).coerceIn(1e-6f,1f) else light
                }
            }
        }
        PhoneObserve.encodeRows(out,emptyMap(),true);return out
    }
}
