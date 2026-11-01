package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class OpticalFinderGeometryTest {
    @Test fun centerFrameStaysInTheActualExposedAreaWithWideBafflesAndDifferentFilmFormats() {
        for(format in listOf("135","half","6x4.5","6x6","6x7","6x9")) for(view in listOf(1200 to 500,640 to 192)) {
            val normal=FrameGateProjection.project(format,false,4000,3000,view.first,view.second)
            val gate=FrameGateProjection.project(format,format=="135",4000,3000,view.first,view.second)
            assertEquals(normal.film,gate.film)
            val box=gate.exposed;val marks=OpticalFinderGeometry.centerMarks(box)
            for(line in marks) for((x,y) in listOf(line.x0 to line.y0,line.x1 to line.y1)) {
                assertTrue(x.isFinite() && y.isFinite());assertTrue(x>box.left && x<box.right);assertTrue(y>box.top && y<box.bottom)
            }
            val cross=marks.last();assertEquals((box.left+box.right)/2,cross.x0,1e-5f)
            assertEquals((box.top+box.bottom)/2,(cross.y0+cross.y1)/2,1e-4f) // FP32 roundoff, far below a display pixel.
        }
    }
}
