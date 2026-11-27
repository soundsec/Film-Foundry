package com.filmfoundry.kernel

import kotlin.math.roundToInt

/** Sample the two physical belts once per factory repeat, including both half frames. */
internal class RebateGlow(private val width: Int, private val rows: Int, spread: Double,
    laserGain: Float, hole: (Int,Int,Boolean)->Boolean, mark: (Int,Int,Boolean)->Boolean) {
    private val w = minOf(width, 768)
    private val h = minOf(rows, 128)
    private val bands = Array(2) { side ->
        val holes = FloatArray(w*h)
        val marks = if (laserGain>0) FloatArray(w*h) else null
        for (i in holes.indices) {
            val x = ((i%w+.5)*width/w).toInt()
            val y = ((i/w+.5)*rows/h).toInt()
            if (hole(x,y,side==0)) holes[i]=1f
            if (marks!=null && mark(x,y,side==0)) marks[i]=1f
        }
        val radius = maxOf(1,(rows*.055).roundToInt())
        val rim = blur(holes,radius)
        val skirt = marks?.let { blur(it,maxOf(1,(radius*spread).roundToInt())) }
        FloatArray(w*h) { i ->
            val distance = (h-i/w-.5)*rows/h
            val edge = maxOf(0.0,1-distance/maxOf(1.0,rows*.065)).toFloat()
            val laser = if (skirt!=null) laserGain*maxOf(0f,skirt[i]-marks[i]) else 0f
            (1.6f*rim[i]*(1-holes[i]) + .55f*edge*edge + laser).coerceIn(0f,1f)
        }
    }

    private fun blur(src: FloatArray, radius: Int): FloatArray {
        val pitch=w+1
        val sums=IntArray(pitch*(h+1))
        for (y in 0 until h) {
            var row=0
            for (x in 0 until w) {
                row+=src[y*w+x].toInt()
                sums[(y+1)*pitch+x+1]=sums[y*pitch+x+1]+row
            }
        }
        val dx=(radius.toDouble()*w/width).roundToInt()
        val dy=(radius.toDouble()*h/rows).roundToInt()
        return FloatArray(w*h) { i ->
            val l=maxOf(0,i%w-dx); val r=minOf(w,i%w+dx+1)
            val t=maxOf(0,i/w-dy); val b=minOf(h,i/w+dy+1)
            (sums[b*pitch+r]-sums[b*pitch+l]-sums[t*pitch+r]+sums[t*pitch+l]).toFloat()/((r-l)*(b-t))
        }
    }

    fun at(x: Int,y: Int,top: Boolean): Float {
        val band=bands[if(top) 0 else 1]
        val u=((x+.5f)*w/width-.5f).coerceIn(0f,w-1f)
        val v=((y+.5f)*h/rows-.5f).coerceIn(0f,h-1f)
        val l=u.toInt(); val r=minOf(l+1,w-1); val a=v.toInt()*w; val b=minOf(v.toInt()+1,h-1)*w
        val dx=u-l; val dy=v-v.toInt()
        return (band[a+l]*(1-dx)+band[a+r]*dx)*(1-dy)+(band[b+l]*(1-dx)+band[b+r]*dx)*dy
    }

    companion object {
        val warmRgb=floatArrayOf(.06f,.003f,.0008f)
    }
}
