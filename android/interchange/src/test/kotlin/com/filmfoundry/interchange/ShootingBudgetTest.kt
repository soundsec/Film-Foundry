package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class ShootingBudgetTest {
    @Test
    fun motionPicksTheLargestListedSizeUnderTheCap() {
        assertEquals(640 to 360, ShootingBudget.motion(listOf(1920 to 1080, 640 to 360, 1280 to 720)))
        assertEquals(320 to 240, ShootingBudget.motion(listOf(1920 to 1080, 640 to 480, 320 to 240)))
        assertEquals(640 to 360, ShootingBudget.motion(emptyList()))
    }

    @Test
    fun motionFpsUsesAnAdvertisedRange() {
        assertEquals(36 to 36, ShootingBudget.motionFps(listOf(15 to 60, 36 to 36)))
        assertEquals(15 to 60, ShootingBudget.motionFps(listOf(30 to 30, 15 to 60)))
        assertEquals(24 to 30, ShootingBudget.motionFps(listOf(15 to 24, 24 to 30)))
        assertEquals(0 to 0, ShootingBudget.motionFps(emptyList()))
    }
}
