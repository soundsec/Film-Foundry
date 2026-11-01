package com.filmfoundry.app.ui

/** Film-only transform in normalized scene coordinates. Camera framing never changes. */
data class LiveSheetPose(val zoom: Float=1f,val x: Float=0f,val y: Float=0f) {
    fun scale(factor: Float,focusX: Float,focusY: Float,filmWidth: Float,filmHeight: Float): LiveSheetPose {
        if(!factor.isFinite() || factor<=0f) return this
        val next=(zoom*factor).coerceIn(1f,5f);val ratio=next/zoom
        return LiveSheetPose(next,(x-focusX)*ratio+focusX,(y-focusY)*ratio+focusY).bounded(filmWidth,filmHeight)
    }
    fun move(dx: Float,dy: Float,filmWidth: Float,filmHeight: Float)=copy(x=x+dx,y=y+dy).bounded(filmWidth,filmHeight)
    private fun bounded(w: Float,h: Float): LiveSheetPose {
        if(zoom==1f) return LiveSheetPose()
        val bx=w*(zoom-1)/2;val by=h*(zoom-1)/2
        return copy(x=x.coerceIn(-bx,bx),y=y.coerceIn(-by,by))
    }
}
