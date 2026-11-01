package com.filmfoundry.interchange

import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class StillFrameFormatTest {
    @Test fun roll120FormatsFreezeTheirApertureAndCapacityAndKeepV2ArchivesReadable() {
        val ratios=mapOf("6x4.5" to 56.0/41.5,"6x6" to 1.0,"6x7" to 68.5/56.0,"6x9" to 82.6/56.0)
        for((format,ratio) in ratios) {
            val cap=StillFrameFormat.capacity(format,120)
            StillFrameFormat.validateRoll(cap,mapOf("format" to format,"stock_length" to 120))
            assertThrows(IllegalArgumentException::class.java) { StillFrameFormat.validateRoll(cap+1,mapOf("format" to format,"stock_length" to 120)) }
            for((w,h) in listOf(4000 to 3000,3000 to 4000)) {
                val aperture=StillFrameFormat.create(format,false,w,h)
                assertEquals(if(w>=h) ratio else 1/ratio,aperture.width.toDouble()/aperture.height,.001)
                assertEquals(aperture,StillFrameFormat.read(aperture.metadata(),w,h))
            }
        }
        val old=StillFrameFormat.create("half",false,4000,3000,StillFrameFormat.PREVIOUS_MODEL)
        assertEquals(old,StillFrameFormat.read(old.metadata(),4000,3000))
        assertEquals(120,StillFrameFormat.create("6x6",false,160,120,StillFrameFormat.PREVIOUS_MODEL).width)
        assertThrows(IllegalArgumentException::class.java) { StillFrameFormat.create("6x7",false,4000,3000,StillFrameFormat.PREVIOUS_MODEL) }
    }
    @Test fun halfMaximizesSensorUseAndLegacyAperturesRemainFrozen() {
        for((w,h) in listOf(4000 to 2250,4000 to 3000,2250 to 4000)) {
            val full=StillFrameFormat.create("135",false,w,h);val half=StillFrameFormat.create("half",false,w,h)
            assertEquals(if(w>=h) .75 else 1/.75,half.width.toDouble()/half.height,.001)
            assertTrue(half.width==w || half.height==h)
            for(model in listOf(StillFrameFormat.PREVIOUS_MODEL,StillFrameFormat.V3_MODEL)) {
                val old=StillFrameFormat.create("half",false,w,h,model)
                if(w>=h) { assertEquals(full.width/2,old.width);assertEquals(full.height,old.height) }
                else { assertEquals(full.width,old.width);assertEquals(full.height/2,old.height) }
                assertEquals(old,StillFrameFormat.read(old.metadata(),w,h))
                assertTrue(half.width.toLong()*half.height>=old.width.toLong()*old.height)
            }
            assertEquals(half,StillFrameFormat.read(half.metadata(),w,h))
            val legacy=StillFrameFormat.create("half",false,w,h,StillFrameFormat.LEGACY_MODEL)
            assertEquals(legacy,StillFrameFormat.read(legacy.metadata(),w,h));assertNotEquals(legacy.width to legacy.height,half.width to half.height)
        }
        val gate=StillFrameFormat.create("half",false,3600,2400)
        assertEquals(1800,gate.width);assertEquals(2400,gate.height)
        val optimized=StillFrameFormat.create("half",false,4000,3000)
        assertEquals(2250,optimized.width);assertEquals(3000,optimized.height)
        val old120=StillFrameFormat.create("6x7",false,4000,3000,StillFrameFormat.V3_MODEL)
        assertEquals(old120,StillFrameFormat.read(old120.metadata(),4000,3000))
    }
    @Test fun stockLengthDeterminesCapacityAndPanoramaKeepsFull135Negative() {
        assertEquals(48,StillFrameFormat.capacity("half",24));assertEquals(72,StillFrameFormat.capacity("half",36))
        assertEquals(12,StillFrameFormat.capacity("6x6",120))
        StillFrameFormat.validateRoll(48,mapOf("format" to "half","stock_length" to 24))
        assertThrows(IllegalArgumentException::class.java) { StillFrameFormat.validateRoll(36,mapOf("format" to "half","stock_length" to 24)) }
        assertThrows(IllegalArgumentException::class.java) { StillFrameFormat.capacity("6x6",36) }
        val normal=StillFrameFormat.create("135",false,1920,1440)
        val wide=StillFrameFormat.create("135",true,1920,1440)
        assertEquals(normal.copy(panorama=true),wide);assertEquals(1920,wide.width);assertEquals(1280,wide.height)
        assertArrayEquals(intArrayOf(0,293,1920,693),wide.openArea())
        val old=StillFrameFormat.create("135",true,1920,1440,StillFrameFormat.V4_MODEL)
        assertArrayEquals(intArrayOf(0,240,1920,800),old.openArea());assertEquals(old,StillFrameFormat.read(old.metadata(),1920,1440))
        assertEquals(wide,StillFrameFormat.read(wide.metadata(),1920,1440))
        assertThrows(IllegalArgumentException::class.java) { StillFrameFormat.create("6x6",true,1920,1440) }
        val square=StillFrameFormat.create("6x6",false,8,6);assertEquals(6,square.width);assertEquals(6,square.height)
    }
    @Test fun halfFrame72SurvivesIndexAndTaskJournals() {
        val frames=(1..72).map { DevelopmentTaskFrame(it,"half:$it","half/frame_${it.toString().padStart(2,'0')}.darkroom_latent.npz",null) }
        val task=DevelopmentTask("long-half","half",emptyMap(),frames)
        assertEquals(72,DevelopmentTaskCodec.decode(DevelopmentTaskCodec.encode(task)).frames.size)
        val catalog=LibraryIndexCodec.decode("""{"kind":"phone_library_index_v0","version":2,"rolls":[],"results":[]}""".toByteArray())
        assertTrue(catalog.rolls.isEmpty())
    }
}
