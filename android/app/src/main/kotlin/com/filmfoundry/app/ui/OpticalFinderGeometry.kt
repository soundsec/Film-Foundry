package com.filmfoundry.app.ui

/** Decoration inside the real exposed aperture. It cannot change the material or preview crop. */
internal object OpticalFinderGeometry {
    val styles=listOf("line","center")
    data class Line(val x0: Float,val y0: Float,val x1: Float,val y1: Float)
    fun centerMarks(box: FrameGateProjection.Box): List<Line> {
        val cx=(box.left+box.right)*.5f;val cy=(box.top+box.bottom)*.5f
        val side=minOf(box.right-box.left,box.bottom-box.top)
        val rx=side*.085f;val ry=side*.06f;val tick=side*.018f
        return listOf(Line(cx-rx,cy-ry,cx+rx,cy-ry),Line(cx+rx,cy-ry,cx+rx,cy+ry),
            Line(cx+rx,cy+ry,cx-rx,cy+ry),Line(cx-rx,cy+ry,cx-rx,cy-ry),
            Line(cx-tick,cy,cx+tick,cy),Line(cx,cy-tick,cx,cy+tick))
    }
}
