package com.filmfoundry.app.session

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class CameraSoundSynthTest {
    @Test fun eachCueHasAnAudibleBuiltInLength() {
        val shutter = CameraSoundSynth.samples(CameraSoundSynth.Cue.SHUTTER)
        val advance = CameraSoundSynth.samples(CameraSoundSynth.Cue.ADVANCE)
        val motor = CameraSoundSynth.samples(CameraSoundSynth.Cue.MOTOR)
        val rewind = CameraSoundSynth.samples(CameraSoundSynth.Cue.REWIND)
        assertTrue(shutter.size < advance.size)
        assertTrue(rewind.size > motor.size)
        assertEquals(0L, CameraSoundSynth.shutterGapMs(null))
        assertEquals(0L, CameraSoundSynth.shutterGapMs(8_000_000L))
        assertEquals(1_000L, CameraSoundSynth.shutterGapMs(1_000_000_000L))
        assertEquals(CameraSoundSynth.SHUTTER_GAP_CAP_MS, CameraSoundSynth.shutterGapMs(60_000_000_000L))
        val curtain = CameraSoundSynth.curtain()
        assertTrue(curtain.size < shutter.size)
        assertTrue(curtain.maxOf { kotlin.math.abs(it.toInt()) } > 1000)
        for (cue in CameraSoundSynth.Cue.entries) {
            val samples = CameraSoundSynth.samples(cue)
            assertTrue(samples.size > CameraSoundSynth.RATE / 20, cue.name)
            assertTrue(samples.maxOf { kotlin.math.abs(it.toInt()) } > 1000, cue.name)
        }
    }
}
