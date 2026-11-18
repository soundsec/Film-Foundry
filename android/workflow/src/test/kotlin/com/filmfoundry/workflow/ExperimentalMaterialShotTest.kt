package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ExperimentalMaterialShotTest {
    @Test fun easterEggCodeIsExactAndTheEntryStaysHidden() {
        assertTrue(ExperimentalMaterialAccess.matches("  ffc2:open-materials \n"))
        assertFalse(ExperimentalMaterialAccess.matches("FFC2:OPEN-MATERIAL"))
        assertFalse(ExperimentalMaterialAccess.visible(developmentPath = false, unlocked = false))
        assertTrue(ExperimentalMaterialAccess.visible(developmentPath = true, unlocked = false))
        assertTrue(ExperimentalMaterialAccess.visible(developmentPath = false, unlocked = true))
    }

    @Test fun quarterTurnAndLongEdgeFitKeepThePixelsAddressable() {
        val turned = ExperimentalMaterialShot.rotateClockwise(2, 1, floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f), 90)
        assertEquals(1, turned.width); assertEquals(2, turned.height)
        assertEquals(1f, turned.values[0]); assertEquals(1f, turned.values[4])
        val fitted = ExperimentalMaterialShot.fit(8, 4, FloatArray(8 * 4 * 3) { 1f }, longEdge = 4)
        assertEquals(4, fitted.width); assertEquals(2, fitted.height)
        assertTrue(fitted.values.all { it == 1f })
    }

    @Test fun rotationsPreserveOrientationOfAnAsymmetricImage() {
        // Rows [1, 2, 3] / [4, 5, 6] distinguish rotation from transposition.
        val rgb = FloatArray(6 * 3) { (it / 3 + 1).toFloat() }
        val clockwise = ExperimentalMaterialShot.rotateClockwise(3, 2, rgb, 90)
        val half = ExperimentalMaterialShot.rotateClockwise(3, 2, rgb, 180)
        val counterclockwise = ExperimentalMaterialShot.rotateClockwise(3, 2, rgb, 270)
        fun red(image: StillPixels) = FloatArray(6) { image.values[it * 3] }
        assertEquals(2, clockwise.width); assertEquals(3, clockwise.height)
        assertEquals(3, half.width); assertEquals(2, half.height)
        assertEquals(2, counterclockwise.width); assertEquals(3, counterclockwise.height)
        assertArrayEquals(floatArrayOf(4f, 1f, 5f, 2f, 6f, 3f), red(clockwise))
        assertArrayEquals(floatArrayOf(6f, 5f, 4f, 3f, 2f, 1f), red(half))
        assertArrayEquals(floatArrayOf(3f, 6f, 2f, 5f, 1f, 4f), red(counterclockwise))
        assertArrayEquals(rgb, ExperimentalMaterialShot.rotateClockwise(clockwise.width, clockwise.height, clockwise.values, -90).values)
    }

    @Test fun everyBasicChoiceMakesOneFinitePicture() {
        val image = StillPixels(8, 6, FloatArray(8 * 6 * 3) { .02f + (it % 17) / 40f })
        for (choice in ExperimentalMaterialShot.choices) {
            val result = ExperimentalMaterialShot.develop(image, choice.id, seed = 4)
            assertEquals(image.width, result.image.width)
            assertEquals(choice.id == "peel", result.peelSheet != null)
            assertTrue(result.image.values.all { it.isFinite() && it >= 0f }, choice.id)
        }
    }
}
