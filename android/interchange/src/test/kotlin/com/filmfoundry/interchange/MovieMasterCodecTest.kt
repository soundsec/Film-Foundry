package com.filmfoundry.interchange

import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class MovieMasterCodecTest {
    @Test fun sharedTimelineUsesRationalPresentationTimesAndExistingNpzPaths() {
        val bytes=javaClass.getResourceAsStream("/movie_master_v1_sample.json")!!.use { it.readBytes() }
        val master=MovieMasterCodec.decode(bytes)
        assertEquals(listOf(80L,120L,200L),master.frames.map { it.pts })
        assertNull(master.frames.last().duration)
        assertEquals(master,MovieMasterCodec.decode(MovieMasterCodec.encode(master)))
        for(bad in listOf(master.copy(stage="captured_source"),
            master.copy(frames=listOf(master.frames.first().copy(path="../frame.darkroom_latent.npz"))),
            master.copy(frames=master.frames.map { it.copy(pts=0) }),
            master.copy(frames=listOf(master.frames.first().copy(timeBaseDen=0)))))
            assertThrows(IllegalArgumentException::class.java) { MovieMasterCodec.encode(bad) }
    }
}
