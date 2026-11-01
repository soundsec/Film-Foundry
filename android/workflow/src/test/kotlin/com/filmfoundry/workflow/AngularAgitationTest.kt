package com.filmfoundry.workflow

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class AngularAgitationTest {
    @Test fun timestampedRotationProducesOnlyMeasuredActiveTimeAndIgnoresGapsOrInvalidSamples() {
        val motion=AngularAgitation()
        for(i in 0..500) motion.add(1_000_000_000L+i*20_000_000L,if(i in 100..300) 2.0 else 0.0,0.0,0.0)
        val result=motion.summary();assertEquals(10.0,result.elapsedSeconds,1e-8)
        assertEquals(4.0,result.activeSeconds,1e-8);assertEquals(1.0,result.strength,1e-8)
        motion.add(10L,2.0,0.0,0.0);motion.add(12_000_000_000L,Double.NaN,0.0,0.0)
        motion.add(20_000_000_000L,2.0,0.0,0.0)
        assertEquals(4.0,motion.summary().activeSeconds,1e-8)
        assertEquals(502,motion.summary().samples)
    }
}
