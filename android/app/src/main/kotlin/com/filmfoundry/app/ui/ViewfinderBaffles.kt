package com.filmfoundry.app.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Color
import android.view.View

/** Film aperture and opaque panorama leaves; never changes zoom or camera crop. */
class ViewfinderBaffles(context: Context): View(context) {
    var closed=false
        set(value) { if(field!=value) { field=value;invalidate() } }
    private val paint=Paint(Paint.ANTI_ALIAS_FLAG).apply { color=Color.BLACK }
    private var projection: FrameGateProjection.Gate?=null
    private var projectionKey=""
    private var marks=emptyList<OpticalFinderGeometry.Line>()
    var frameStyle="line"
        set(value) { val next=value.takeIf { it in OpticalFinderGeometry.styles } ?: "line";if(field!=next) { field=next;invalidate() } }
    var filmFormat="135"
        set(value) { if(field!=value) { field=value;invalidate() } }
    var gateModel=com.filmfoundry.interchange.StillFrameFormat.MODEL
        set(value) { if(field!=value) { field=value;invalidate() } }
    private var sensorWidth=0
    private var sensorHeight=0
    fun geometry(width: Int,height: Int) { if(sensorWidth!=width || sensorHeight!=height) { sensorWidth=width;sensorHeight=height;invalidate() } }
    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        if(sensorWidth<=0 || sensorHeight<=0 || width<=0 || height<=0) return
        val key="$filmFormat:$closed:$sensorWidth:$sensorHeight:$width:$height:$gateModel"
        if(key!=projectionKey) {
            projectionKey=key;projection=FrameGateProjection.project(filmFormat,closed && filmFormat=="135",sensorWidth,sensorHeight,width,height,gateModel)
            marks=OpticalFinderGeometry.centerMarks(projection!!.exposed)
        }
        val gate=checkNotNull(projection)
        val film=gate.film;val exposed=gate.exposed
        paint.style=Paint.Style.FILL;paint.color=Color.argb(160,0,0,0)
        canvas.drawRect(0f,0f,width.toFloat(),film.top,paint)
        canvas.drawRect(0f,film.bottom,width.toFloat(),height.toFloat(),paint)
        canvas.drawRect(0f,film.top,film.left,film.bottom,paint)
        canvas.drawRect(film.right,film.top,width.toFloat(),film.bottom,paint)
        paint.color=Color.BLACK
        canvas.drawRect(film.left,film.top,film.right,exposed.top,paint)
        canvas.drawRect(film.left,exposed.bottom,film.right,film.bottom,paint)
        paint.style=Paint.Style.STROKE;paint.strokeWidth=resources.displayMetrics.density;paint.color=Color.argb(220,242,243,230)
        fun frame(box: FrameGateProjection.Box) {
            canvas.drawRect(box.left,box.top,box.right,box.bottom,paint)
        }
        frame(film)
        if(closed && filmFormat=="135") {
            canvas.drawLine(exposed.left,exposed.top,exposed.right,exposed.top,paint)
            canvas.drawLine(exposed.left,exposed.bottom,exposed.right,exposed.bottom,paint)
        }
        if(frameStyle=="center") for(line in marks) canvas.drawLine(line.x0,line.y0,line.x1,line.y1,paint)
    }
}
