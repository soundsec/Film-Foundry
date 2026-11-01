package com.filmfoundry.app.ui

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import kotlin.math.exp

/** Flat cassette colors and motifs. Drawn in code; not a photograph or a medium field. */
object FilmFace {
    data class Palette(val edge: Int,val body: Int,val core: Int,val label: Int,val ink: Int,val motif: Int)
    fun of(film: Map<String,Any?>,salt: String): Palette {
        val base=film["film_base_density_rgb"] as? List<*>
        val mono=film["color_process"]?.toString()=="monochrome"
        fun channel(index: Int): Int {
            val density=(base?.getOrNull(index) as? Number)?.toFloat() ?: if(mono) .4f else .28f
            return (255.0*exp(-density*1.6)).toInt().coerceIn(36,236)
        }
        val body=Color.rgb(channel(0),channel(1),channel(2))
        fun shade(color: Int,amount: Float): Int {
            fun mix(part: Int)=(part+(if(amount>0) 255-part else part)*amount).toInt().coerceIn(0,255)
            return Color.rgb(mix(Color.red(color)),mix(Color.green(color)),mix(Color.blue(color)))
        }
        val name=film["name"]?.toString().orEmpty()
        return Palette(shade(body,-.28f),body,shade(body,.16f),shade(body,.42f),shade(body,-.62f),Math.floorMod((salt+name).hashCode(),4))
    }
    fun drawMotif(canvas: Canvas,paint: Paint,left: Float,top: Float,right: Float,bottom: Float,motif: Int,color: Int) {
        paint.shader=null;paint.style=Paint.Style.FILL;paint.color=color
        val width=right-left;val height=bottom-top
        when(motif) {
            0 -> { canvas.drawRect(left,top+height*.18f,right,top+height*.30f,paint);canvas.drawRect(left,top+height*.62f,right,top+height*.74f,paint) }
            1 -> { var x=left+width*.12f;val y=top+height*.58f;while(x<right-width*.08f) { canvas.drawRect(x,y,x+width*.08f,y+height*.16f,paint);x+=width*.16f } }
            2 -> { var x=left+width*.08f;while(x<right-width*.1f) { canvas.drawRect(x,top+height*.2f,x+width*.05f,bottom-height*.15f,paint);x+=width*.18f } }
            else -> { canvas.drawCircle(left+width*.28f,top+height*.62f,minOf(width,height)*.12f,paint);canvas.drawRect(left+width*.48f,top+height*.52f,right-width*.12f,top+height*.72f,paint) }
        }
    }
    fun swatch(film: Map<String,Any?>,salt: String): Bitmap {
        val face=of(film,salt)
        val bitmap=Bitmap.createBitmap(144,96,Bitmap.Config.ARGB_8888)
        val canvas=Canvas(bitmap);val paint=Paint(Paint.ANTI_ALIAS_FLAG)
        paint.color=face.body;canvas.drawRect(0f,0f,144f,96f,paint)
        paint.color=face.label;canvas.drawRoundRect(18f,16f,126f,80f,8f,8f,paint)
        drawMotif(canvas,paint,18f,16f,126f,80f,face.motif,face.ink)
        return bitmap
    }
}
