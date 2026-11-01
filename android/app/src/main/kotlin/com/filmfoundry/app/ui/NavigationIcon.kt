package com.filmfoundry.app.ui

import android.content.res.ColorStateList
import android.graphics.*
import android.graphics.drawable.Drawable

/** Geometric navigation marks, centred independently of font metrics. */
enum class NavigationIcon { BACK,NEXT,UP,DOWN,EXPAND,COLLAPSE,HISTOGRAM,ROTATE }

class NavigationIconDrawable(private val icon: NavigationIcon,private val colors: ColorStateList): Drawable() {
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG).apply { style=Paint.Style.STROKE;strokeWidth=1.8f;strokeCap=Paint.Cap.ROUND;strokeJoin=Paint.Join.ROUND }
    private val path=Path()
    override fun isStateful()=colors.isStateful
    override fun onStateChange(state: IntArray): Boolean { invalidateSelf();return true }
    override fun draw(canvas: Canvas) {
        val size=minOf(bounds.width(),bounds.height())*.48f
        canvas.save();canvas.translate(bounds.exactCenterX()-size/2,bounds.exactCenterY()-size/2);canvas.scale(size/24,size/24)
        paint.color=colors.getColorForState(state,colors.defaultColor);path.rewind()
        if(icon==NavigationIcon.ROTATE) {
            paint.style=Paint.Style.STROKE;paint.strokeWidth=2.3f
            canvas.drawRect(RectF(4f,10f,14f,21f),paint)
            path.moveTo(9f,6f);path.lineTo(20f,6f);path.lineTo(20f,15f);canvas.drawPath(path,paint);path.rewind()
            paint.style=Paint.Style.FILL;path.moveTo(16f,12f);path.lineTo(20f,17f);path.lineTo(24f,12f);path.close()
        } else if(icon!=NavigationIcon.HISTOGRAM) {
            // Equal sides, with the centroid on the touch target's centre.
            val angle=when(icon) {
                NavigationIcon.BACK -> -90f
                NavigationIcon.NEXT -> 90f
                NavigationIcon.DOWN,NavigationIcon.COLLAPSE -> 180f
                else -> 0f
            }
            canvas.rotate(angle,12f,12f);paint.style=Paint.Style.FILL
            path.moveTo(12f,4f);path.lineTo(5.071797f,16f);path.lineTo(18.928204f,16f);path.close()
        } else {
                paint.style=Paint.Style.STROKE
                path.moveTo(3f,20f);path.lineTo(3f,14f);path.lineTo(7f,14f);path.lineTo(7f,8f);path.lineTo(11f,8f)
                path.lineTo(11f,4f);path.lineTo(15f,4f);path.lineTo(15f,11f);path.lineTo(19f,11f);path.lineTo(19f,16f);path.lineTo(21f,16f);path.lineTo(21f,20f)
        }
        canvas.drawPath(path,paint);canvas.restore()
    }
    override fun setAlpha(alpha: Int) { paint.alpha=alpha;invalidateSelf() }
    override fun setColorFilter(filter: ColorFilter?) { paint.colorFilter=filter;invalidateSelf() }
    @Suppress("DEPRECATION","OVERRIDE_DEPRECATION") override fun getOpacity()=PixelFormat.TRANSLUCENT
}
