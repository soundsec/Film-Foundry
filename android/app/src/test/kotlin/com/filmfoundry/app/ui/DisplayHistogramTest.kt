package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class DisplayHistogramTest {
    @Test fun logScaleRevealsSmallBinsWithoutChangingCountsOrClippingEndpoints() {
        val builder=DisplayHistogram.Builder(10002,1)
        repeat(10000) { builder.addArgb(0xFF000000.toInt()) }
        builder.addArgb(0xFF808080.toInt());builder.addArgb(0xFFFFFFFF.toInt())
        val value=builder.finish();val linear=value.heights(0);val log=value.heights(0,true)
        assertEquals(1f,log[0]);assertEquals(0f,log[100]);assertEquals(1f,linear[0])
        assertTrue(log[128]>.07f);assertEquals(.0001f,linear[128],.000001f)
        assertEquals(log[128],log[255]);assertEquals(10000,value.count(0,0))
        assertEquals(10002,(0..255).sumOf { value.count(0,it) })
        assertTrue(log.all { it.isFinite() && it in 0f..1f })
    }
    @Test fun sharedLinearScaleKeepsChannelWeightsAndSeparatesEndpointSpikesFromTheBody() {
        val builder=DisplayHistogram.Builder(10010,1)
        repeat(10000) { builder.addArgb(0xFF000000.toInt()) }
        repeat(8) { builder.addArgb(0xFF404080.toInt()) }
        repeat(2) { builder.addArgb(0xFF802080.toInt()) }
        val value=builder.finish();val peak=value.plotPeak()
        assertEquals(10,peak)
        assertEquals(.8f,value.heights(1,false,peak)[64]);assertEquals(.2f,value.heights(1,false,peak)[128])
        assertEquals(1f,value.heights(3,false,peak)[128]);assertEquals(1f,value.heights(3,false,peak)[0])
        assertEquals(0xFFFFFF,value.clippedChannels(false));assertEquals(0,value.clippedChannels(true))
        assertEquals(10000,value.count(3,0));assertEquals(10010,(0..255).sumOf { value.count(3,it) })
    }
    @Test fun displayEndpointsAndChannelsRemainDistinct() {
        val builder=DisplayHistogram.Builder(3,2)
        listOf(0xFF000000,0xFFFFFFFF,0xFFFF0000,0xFF00FF00,0xFF0000FF,0xFF808080).forEach { builder.addArgb(it.toInt()) }
        val value=builder.finish()
        assertEquals(6,value.pixelCount)
        for(channel in 0..3) assertEquals(6,(0..255).sumOf { value.count(channel,it) })
        for(channel in 1..3) {
            assertEquals(3,value.count(channel,0));assertEquals(2,value.count(channel,255));assertEquals(1,value.count(channel,128))
        }
        for(bin in listOf(0,255,54,182,18,128)) assertEquals(1,value.count(0,bin))
        assertEquals(3,value.peak(1));assertEquals(1,value.peak(0))
    }
    @Test fun incompletePreviewCannotPublishAndPublishedBinsAreIndependent() {
        val builder=DisplayHistogram.Builder(1,1)
        assertThrows(IllegalStateException::class.java) { builder.finish() }
        builder.addArgb(0xFF000000.toInt());val accepted=builder.finish()
        builder.addArgb(0xFFFFFFFF.toInt())
        assertEquals(0,accepted.count(0,255));assertEquals(1,accepted.count(0,0))
        assertThrows(IllegalStateException::class.java) { builder.finish() }
        assertThrows(IllegalArgumentException::class.java) { DisplayHistogram.Builder(Int.MAX_VALUE,2) }
    }
}
