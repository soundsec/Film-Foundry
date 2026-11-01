package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ViewerFrameRowsTest {
    @Test fun borrowedRowScratchProducesTheSamePackedFramesAcrossRotationAndFrameChanges() {
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(2,3,3),FloatArray(18) { (it%7)*.13f }),emptyMap())
        val film=LiveSceneSession(OpticalPreview(archive,3,2,1),WorkflowControl());val scratch=ViewerRowScratch()
        for(rotation in listOf(0,90,180,270,0)) {
            val pixels=FloatArray(43*35*3) { (it%113)/112f };val original=pixels.copyOf()
            val frame=ViewerPreviewFrame(43,35,pixels,ViewerPreviewEncoding.DISPLAY_SRGB,rotation)
            val reader=ViewerFrameRows(frame,scratch=scratch)
            val reference=film.render(ViewerFrameRows(frame).rows());val packed=IntArray(reader.width*reader.height)
            film.renderArgb(reader.rows(),packed)
            for(i in packed.indices) {
                fun c(k: Int)=(reference.rgb[i*3+k]*255).toInt()
                assertEquals(0xff000000.toInt() or (c(0) shl 16) or (c(1) shl 8) or c(2),packed[i])
            }
            assertArrayEquals(original,pixels,0f)
        }
    }
    @Test fun cameraRotationAndDisplayLinearizationStayOrientedWithoutChangingTheFrame() {
        val archive=DevelopedOpticalArchive(FloatNd(intArrayOf(1,1,3),FloatArray(3)),emptyMap())
        val film=LiveSceneSession(OpticalPreview(archive,1,1,1),WorkflowControl())
        val pixels=FloatArray(18) { .05f+it*.04f };val original=pixels.copyOf()
        for(rotation in listOf(0,90,180,270)) {
            val frame=ViewerPreviewFrame(3,2,pixels,ViewerPreviewEncoding.DISPLAY_SRGB,rotation)
            val actual=film.render(ViewerFrameRows(frame).rows())
            val orientation=CapturePixelOrientation(3,2,when(rotation) { 90->6;180->3;270->8;else->1 })
            assertEquals(orientation.width,actual.width);assertEquals(orientation.height,actual.height)
            for(y in 0 until actual.height) for(x in 0 until actual.width) for(c in 0..2) {
                val source=(orientation.y(x,y)*3+orientation.x(x,y))*3+c
                assertEquals(pixels[source],actual.rgb[(y*actual.width+x)*3+c],2e-6f)
            }
        }
        assertArrayEquals(original,pixels,0f)
    }
}
