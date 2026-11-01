package com.filmfoundry.kernel

import kotlin.math.*

/** Phone material-density model, not display RGB noise. Frozen scales retain material units.
 * Fine layer texture and broader dye clouds use distinct fields. No image-sized noise buffers. */
internal class PhoneLayerDyeCloud(private val width: Int,referencePixels: Float,size: Float,
    scales: List<Float>,weights: List<Float>,private val seed: Long,mu: Float,sigma: Float) {
    private val radii=scales.mapIndexed { i,s -> max(.75f,s*referencePixels*size*if(i==0) .45f else 1f) }
    private val norm=sqrt(weights.sumOf { (it*it).toDouble() }).toFloat()
    private val mix=weights.map { it/norm }
    // Smooth density-dependent amplitude; evaluate the exponential once per material preparation.
    private val response=FloatArray(1025) { i ->
        val t=(i/1024f-mu)/sigma;.06f+.94f*exp(-.5f*t*t)
    }
    private val cloudRadius=max(2.5f,(radii.maxOrNull() ?: 2.5f)*1.6f)
    // Coarse energy comes from the material's scale distribution, not a second strength knob.
    private val cloudMix=if(weights.size==1) .12f else (.12f+.5f*weights.drop(1).sum()/weights.sum()).coerceAtMost(.45f)

    /** Each execution owns its lattice rows; prepared models remain safe to share. */
    inner class Samples(private val cached: Boolean=true) {
        private val fields=Array(radii.size) { i -> Array(4) { c -> Plane(radii[i],seed+11+i*1009+c*1301,cached) } }
        private val clouds=Array(3) { c -> Plane(cloudRadius,seed+19571+c*1709,cached) }
        private var px=Int.MIN_VALUE;private var py=Int.MIN_VALUE
        private val shared=FloatArray(radii.size)
        fun texture(x: Int,y: Int,layer: Int): Float {
            if(px!=x || py!=y) {
                px=x;py=y
                for(i in radii.indices) shared[i]=fields[i][0].sample(x,y)
            }
            var fine=0f
            for(i in radii.indices) fine+=(shared[i]*.32f+fields[i][layer+1].sample(x,y)*.68f)*mix[i]
            return fine+cloudMix*clouds[layer].sample(x,y)
        }
    }
    fun samples(cached: Boolean=true)=Samples(cached)
    fun factor(samples: Samples,x: Int,y: Int,layer: Int,amount: Float,strength: Float): Float {
        val v=amount.coerceIn(0f,1f)*1024f;val i=v.toInt().coerceAtMost(1023);val t=v-i
        val envelope=response[i]*(1-t)+response[i+1]*t
        return 1f+(strength*3.5f*samples.texture(x,y,layer)*envelope).coerceIn(-.42f,.42f)
    }

    /** Two lattice rows per field, O(width/radius) memory. Reordering blocks only refills rows. */
    private inner class Plane(private val radius: Float,private val fieldSeed: Long,private val cached: Boolean) {
        private val offsetX=((fieldSeed ushr 4) and 255)/256f+.17f
        private val offsetY=((fieldSeed ushr 13) and 255)/256f+.31f
        private val columns=ceil(width/radius+offsetX).toInt()+2
        private var row0=Int.MIN_VALUE;private var row1=Int.MIN_VALUE
        private var a=if(cached) FloatArray(columns) else FloatArray(0)
        private var b=if(cached) FloatArray(columns) else FloatArray(0)
        // Reuse the exact reference division/interpolation coordinates, not a sampled noise image.
        // Per execution: O(width) scratch; y is recomputed only when the absolute source row changes.
        private val xCell=if(cached) IntArray(width) { x -> floor(x/radius+offsetX).toInt() } else IntArray(0)
        private val xWeight=if(cached) FloatArray(width) { x ->
            val fx=x/radius+offsetX-xCell[x];fx*fx*(3-2*fx)
        } else FloatArray(0)
        private var sampleRow=Int.MIN_VALUE
        private var yCell=0
        private var yWeight=0f
        fun sample(x: Int,y: Int): Float {
            val ix: Int;val iy: Int;val tx: Float;val ty: Float
            if(cached) {
                if(sampleRow!=y) {
                    val v=y/radius+offsetY;yCell=floor(v).toInt()
                    val fy=v-yCell;yWeight=fy*fy*(3-2*fy);sampleRow=y
                }
                ix=xCell[x];tx=xWeight[x];iy=yCell;ty=yWeight
            } else {
                val u=x/radius+offsetX;val v=y/radius+offsetY
                ix=floor(u).toInt();iy=floor(v).toInt()
                val fx=u-ix;val fy=v-iy;tx=fx*fx*(3-2*fx);ty=fy*fy*(3-2*fy)
            }
            val aa: Float;val ab: Float;val ba: Float;val bb: Float
            if(cached) {
                if(row0!=iy) {
                    if(row1==iy) { val swap=a;a=b;b=swap;row0=row1;row1=Int.MIN_VALUE }
                    else { for(i in a.indices) a[i]=value(i,iy,fieldSeed);row0=iy }
                }
                if(row1!=iy+1) { for(i in b.indices) b[i]=value(i,iy+1,fieldSeed);row1=iy+1 }
                aa=a[ix];ab=a[ix+1];ba=b[ix];bb=b[ix+1]
            } else { aa=value(ix,iy,fieldSeed);ab=value(ix+1,iy,fieldSeed);ba=value(ix,iy+1,fieldSeed);bb=value(ix+1,iy+1,fieldSeed) }
            return (aa*(1-tx)+ab*tx)*(1-ty)+(ba*(1-tx)+bb*tx)*ty
        }
    }
    private fun value(x: Int,y: Int,seed: Long): Float {
        var state=seed+x.toLong()*104729L+y.toLong()*130363L
        state=state*6364136223846793005L+1442695040888963407L
        state=state xor (state ushr 29);state=state*2862933555777941757L+3037000493L
        return ((state ushr 40) and 0xffffff).toFloat()/8388608f-1f
    }
}
