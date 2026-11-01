package com.filmfoundry.app.ui

import com.filmfoundry.interchange.StillFrameFormat

/** Display-only projection of the same integer aperture used by deferred exposure. */
internal object FrameGateProjection {
    data class Box(val left: Float,val top: Float,val right: Float,val bottom: Float)
    data class Gate(val film: Box,val exposed: Box)
    fun project(format: String,panorama: Boolean,sourceWidth: Int,sourceHeight: Int,viewWidth: Int,viewHeight: Int,model: String=StillFrameFormat.MODEL): Gate {
        require(viewWidth>0 && viewHeight>0)
        val gate=StillFrameFormat.create(format,panorama,sourceWidth,sourceHeight,model)
        val scale=minOf(viewWidth.toFloat()/sourceWidth,viewHeight.toFloat()/sourceHeight)
        val offsetX=(viewWidth-sourceWidth*scale)/2;val offsetY=(viewHeight-sourceHeight*scale)/2
        fun box(left: Int,top: Int,width: Int,height: Int)=Box(offsetX+left*scale,offsetY+top*scale,offsetX+(left+width)*scale,offsetY+(top+height)*scale)
        val open=gate.openArea()
        return Gate(box(gate.left,gate.top,gate.width,gate.height),box(gate.left+open[0],gate.top+open[1],open[2],open[3]))
    }
}
