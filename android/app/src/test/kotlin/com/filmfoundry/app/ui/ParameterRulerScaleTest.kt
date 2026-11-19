package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class ParameterRulerScaleTest {
    @Test fun wideRangesRetainFastTravelAndSingleStepFineTravel() {
        for ((maximum, coarse) in listOf(7200 to 144f, 1199 to 24f)) {
            assertEquals(coarse, rulerStepsPerTick(maximum, false))
            assertEquals(1f, rulerStepsPerTick(maximum, true))
        }
        assertEquals(.4f, rulerStepsPerTick(80, true))
    }
}
