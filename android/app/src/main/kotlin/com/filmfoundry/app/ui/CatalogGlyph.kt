package com.filmfoundry.app.ui

import android.graphics.*
import android.graphics.drawable.Drawable

/** Code-drawn icons share a 24-unit canvas; no font-dependent star or heart glyphs. */
class CatalogGlyph(private val kind: String,private val filled: Boolean=false,private val color: Int=Color.WHITE): Drawable() {
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG)
    override fun draw(canvas: Canvas) {
        val size=minOf(bounds.width(),bounds.height()).toFloat()
        canvas.save();canvas.translate(bounds.left+(bounds.width()-size)/2,bounds.top+(bounds.height()-size)/2);canvas.scale(size/24f,size/24f)
        paint.color=color;paint.strokeWidth=1.5f;paint.strokeJoin=Paint.Join.ROUND;paint.style=if(filled) Paint.Style.FILL else Paint.Style.STROKE
        val path=Path()
        when(kind) {
            "star" -> { for(i in 0..9) { val angle=Math.PI*i/5-Math.PI/2;val r=if(i%2==0) 9.0 else 4.1
                val x=(12+kotlin.math.cos(angle)*r).toFloat();val y=(12+kotlin.math.sin(angle)*r).toFloat();if(i==0) path.moveTo(x,y) else path.lineTo(x,y) };path.close();canvas.drawPath(path,paint) }
            "heart" -> { path.moveTo(12f,21f);path.cubicTo(1f,14f,0f,7f,5f,4f);path.cubicTo(8f,2f,11f,4f,12f,6f);path.cubicTo(13f,4f,16f,2f,19f,4f);path.cubicTo(24f,7f,23f,14f,12f,21f);path.close();canvas.drawPath(path,paint) }
            "list" -> for(y in listOf(4f,10f,16f)) { canvas.drawRect(2f,y,6f,y+4,paint);canvas.drawLine(9f,y+2,22f,y+2,paint) }
            else -> { val n=if(kind=="grid5") 5 else 3;val cell=20f/n;paint.strokeWidth=if(n==5) .9f else 1.3f
                for(y in 0 until n) for(x in 0 until n) canvas.drawRect(2+x*cell,2+y*cell,2+(x+1)*cell-1.3f,2+(y+1)*cell-1.3f,paint) }
        }
        canvas.restore()
    }
    override fun setAlpha(alpha: Int) { paint.alpha=alpha;invalidateSelf() }
    override fun setColorFilter(filter: ColorFilter?) { paint.colorFilter=filter;invalidateSelf() }
    @Suppress("DEPRECATION","OVERRIDE_DEPRECATION") override fun getOpacity()=PixelFormat.TRANSLUCENT
}
