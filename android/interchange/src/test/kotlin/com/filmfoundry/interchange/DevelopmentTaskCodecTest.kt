package com.filmfoundry.interchange

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class DevelopmentTaskCodecTest {
    @Test fun taskAndIdsRoundTripAndNeverDeclareUnregisteredCompletion() {
        val frame=DevelopmentTaskFrame(1,"roll:1","roll/frame_01.darkroom_latent.npz","roll/frame_01.capture/receipt.json")
        val task=DevelopmentTask("job","roll",mapOf("develop" to mapOf("time_min" to 2)),listOf(frame))
        assertEquals(task,DevelopmentTaskCodec.decode(DevelopmentTaskCodec.encode(task)))
        assertEquals(listOf("job","next"),DevelopmentTaskCodec.decodeIds(DevelopmentTaskCodec.encodeIds(listOf("job","next"))))
        assertThrows(IllegalArgumentException::class.java) { DevelopmentTaskCodec.encode(task.copy(status="completed")) }
        assertThrows(IllegalArgumentException::class.java) { DevelopmentTaskCodec.encodeIds(listOf("../escape")) }
        assertThrows(IllegalArgumentException::class.java) { DevelopmentTaskCodec.encode(task.copy(frames=listOf(frame,frame))) }
        val done=task.copy(status="completed",frames=listOf(frame.copy(phase="registered",formationMs=3,developmentMs=5)))
        assertEquals(done,DevelopmentTaskCodec.decode(DevelopmentTaskCodec.encode(done)))
    }
}
