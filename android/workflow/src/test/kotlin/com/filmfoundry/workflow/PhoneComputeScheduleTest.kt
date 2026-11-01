package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class PhoneComputeScheduleTest {
    @Test fun budgetGrowsThePassWithoutPassingTheCapOrTheFrame() {
        val wide=PhoneComputeSchedule.blockPixels(4000,3000,256L*1048576)
        assertTrue(wide>16384)
        assertTrue(wide<=PhoneComputeSchedule.MAX_PIXELS)
        assertEquals(0,wide%4000)
        assertEquals(640*480,PhoneComputeSchedule.blockPixels(640,480,256L*1048576))
        assertEquals(4000,PhoneComputeSchedule.blockPixels(4000,3000,0))
        assertEquals(300000,PhoneComputeSchedule.blockPixels(300000,2,256L*1048576))
        assertEquals(wide.toLong()*PhoneComputeSchedule.COLOR_BYTES_PER_PIXEL,PhoneComputeSchedule.workingBytes(wide))
    }
}
