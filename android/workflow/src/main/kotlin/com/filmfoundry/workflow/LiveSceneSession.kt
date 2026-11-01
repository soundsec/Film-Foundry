package com.filmfoundry.workflow

import com.filmfoundry.interchange.OpticalPreview
import com.filmfoundry.kernel.*

/** Task-owned fixed transmittance. Moving camera frames never become media or scans. */
class LiveSceneSession(preview: OpticalPreview,control: WorkflowControl) {
    private val width=preview.sourceWidth
    private val height=preview.sourceHeight
    private val film=PhoneSceneTransmission(OpticalMaster(preview.archive.opticalDensityRgb,preview.archive.metadata),control::checkCancel)
    val filmAspect=preview.archive.opticalDensityRgb.shape[1].toFloat()/preview.archive.opticalDensityRgb.shape[0]
    fun renderArgb(scene: RgbRows,out: IntArray,zoom: Float=1f,x: Float=0f,y: Float=0f)=film.renderArgb(scene,out,zoom,x,y)
    fun render(scene: RgbRows): OpticalPreviewSession.Observation {
        val seen=film.render(scene)
        return OpticalPreviewSession.Observation(seen.outputRgb.shape[1],seen.outputRgb.shape[0],seen.outputRgb.values,width,height,seen.metadata)
    }
}
