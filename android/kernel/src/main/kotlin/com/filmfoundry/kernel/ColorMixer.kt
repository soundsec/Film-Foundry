package com.filmfoundry.kernel

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** Output-referred HSL mixer v1. Eight circular, smoothly interpolated color bands. */
class ColorMixer private constructor(private val rows: Array<FloatArray>) {
    fun apply(values: FloatArray) {
        for(i in values.indices step 3) {
            val r=values[i].coerceIn(0f,1f);val g=values[i+1].coerceIn(0f,1f);val b=values[i+2].coerceIn(0f,1f)
            val hi=max(r,max(g,b));val lo=min(r,min(g,b));val d=hi-lo
            if(d==0f) continue
            var h=(when(hi) { r->(g-b)/d;g->(b-r)/d+2f;else->(r-g)/d+4f }*60f+360f)%360f
            var l=(hi+lo)*.5f
            var s=d/max(1f-abs(2f*l-1f),1e-12f)
            var band=0
            while(band<7 && h>=centers[band+1]) band++
            var t=(h-centers[band])/(centers[band+1]-centers[band]);t=t*t*(3f-2f*t)
            val a=rows[band];val z=rows[(band+1)%8]
            h=(h+a[0]*(1f-t)+z[0]*t+360f)%360f
            s=(s*(1f+(a[1]*(1f-t)+z[1]*t)/100f)).coerceIn(0f,1f)
            l=(l+(a[2]*(1f-t)+z[2]*t)*.0025f*min(d/.05f,1f)).coerceIn(0f,1f)
            val c=(1f-abs(2f*l-1f))*s;val sector=h/60f
            val x=c*(1f-abs(sector%2f-1f));val m=l-c*.5f
            val rr: Float;val gg: Float;val bb: Float
            when(sector.toInt()) {
                0->{rr=c;gg=x;bb=0f};1->{rr=x;gg=c;bb=0f};2->{rr=0f;gg=c;bb=x}
                3->{rr=0f;gg=x;bb=c};4->{rr=x;gg=0f;bb=c};else->{rr=c;gg=0f;bb=x}
            }
            values[i]=(rr+m).coerceIn(0f,1f);values[i+1]=(gg+m).coerceIn(0f,1f);values[i+2]=(bb+m).coerceIn(0f,1f)
        }
    }
    companion object {
        private val centers=floatArrayOf(0f,30f,60f,120f,180f,240f,270f,300f,360f)
        fun parse(value: Any?): ColorMixer? {
            val rows=com.filmfoundry.interchange.ColorMixerSettings.normalize(value) ?: return null
            return ColorMixer(rows.map { it.toFloatArray() }.toTypedArray())
        }
    }
}
