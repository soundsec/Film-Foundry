package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class LiveSheetPoseTest {
    @Test fun pinchKeepsFilmPointUnderTheFingerAndPanClampsWithoutMovingTheCameraFrame() {
        val pose=LiveSheetPose().scale(2f,.1f,-.05f,.86f,.57f)
        assertEquals(2f,pose.zoom);assertEquals(-.1f,pose.x,1e-6f);assertEquals(.05f,pose.y,1e-6f)
        assertEquals(.1f,(.1f-pose.x)/pose.zoom,1e-6f)
        assertEquals(-.05f,(-.05f-pose.y)/pose.zoom,1e-6f)
        val moved=pose.move(10f,-10f,.86f,.57f)
        assertEquals(.43f,moved.x,1e-6f);assertEquals(-.285f,moved.y,1e-6f)
        assertEquals(LiveSheetPose(),moved.scale(.1f,.2f,.2f,.86f,.57f))
        assertEquals(5f,pose.scale(20f,0f,0f,.86f,.57f).zoom)
        assertEquals(pose,pose.scale(Float.NaN,0f,0f,.86f,.57f))
    }
}
