package com.filmfoundry.platform

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class DeferredPixelComputeTest {
    @Test fun densityCanReconsiderFirstBlockTimingWithoutChangingItsFormula() {
        var tick=0;var calls=0;var report=""
        val times=longArrayOf(0,100,100,101,101,102,200,201,201,301,301,401)
        val input=FloatArray(12){it*.17f};val original=input.copyOf()
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=error("Density must not qualify sRGB")
            override fun transmit(density: FloatArray): FloatArray { calls++;return com.filmfoundry.kernel.DensityTransmissionCompute.reference(density) }
        }},{report=it},clockNs={times[tick++]}).use { compute ->
            repeat(16) { assertArrayEquals(com.filmfoundry.kernel.DensityTransmissionCompute.reference(input),compute.transmit(input),0f) }
            assertEquals(3,calls);assertTrue(report.startsWith("gles31_density_transmission"));assertArrayEquals(original,input,0f)
        }
    }
    @Test fun nonfiniteInputsAreRejectedBeforeHardwareOrCpuExecution() {
        val plan=com.filmfoundry.kernel.SpectralTransmissionPlan(2,DoubleArray(18){.1},DoubleArray(6){.5},DoubleArray(6){1.0/3})
        DeferredPixelCompute(true,{error("Invalid input must not initialize hardware")}).use { compute ->
            for(value in listOf(Float.NaN,Float.POSITIVE_INFINITY,Float.NEGATIVE_INFINITY)) {
                assertThrows(IllegalArgumentException::class.java) { compute.linearize(floatArrayOf(value)) }
                assertThrows(IllegalArgumentException::class.java) { compute.transmit(floatArrayOf(value)) }
                assertThrows(IllegalArgumentException::class.java) { compute.transmitSpectral(FloatArray(9){value},null,plan) }
                assertThrows(IllegalArgumentException::class.java) { compute.transmitSpectral(FloatArray(9),FloatArray(3){value},plan) }
            }
        }
    }

    @Test fun scanQualifiesItsOwnFormulaWithoutExecutingUnrelatedSrgbConversion() {
        var srgbCalls=0;var densityCalls=0
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray): FloatArray { srgbCalls++;return FloatArray(encoded.size) }
            override fun transmit(density: FloatArray): FloatArray { densityCalls++;return com.filmfoundry.kernel.DensityTransmissionCompute.reference(density) }
        }},selectByTiming=false).use { compute ->
            val density=floatArrayOf(.1f,.5f,1f)
            assertArrayEquals(com.filmfoundry.kernel.DensityTransmissionCompute.reference(density),compute.transmit(density),0f)
            assertEquals(0,srgbCalls);assertEquals(2,densityCalls)
            assertArrayEquals(TransferFunctions.srgbToLinear(floatArrayOf(.5f)),compute.linearize(floatArrayOf(.5f)),0f)
            assertEquals(1,srgbCalls) // Failed sRGB qualification still falls back; tolerance is unchanged.
        }
    }
    @Test fun transferTimingCanKeepSmallOperationsOnCpuWithoutDisablingSpectralHardware() {
        var calls=0;var report="";var tick=0
        val times=longArrayOf(0,100,100,101,101,102,200,201,201,301,301,401)
        val plan=com.filmfoundry.kernel.SpectralTransmissionPlan(2,DoubleArray(18){.1},DoubleArray(6){.5},DoubleArray(6){1.0/3})
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray): FloatArray { calls++;return TransferFunctions.srgbToLinear(encoded) }
            override fun transmitSpectral(amounts: FloatArray,post: FloatArray?,plan: com.filmfoundry.kernel.SpectralTransmissionPlan)=plan.reference(amounts,post)
        }},{report=it;error("diagnostics unavailable")},clockNs={times[tick++]}).use { compute ->
            compute.linearize(floatArrayOf(.5f));compute.linearize(floatArrayOf(.7f))
            assertEquals(2,calls);assertTrue(report.startsWith("cpu_srgb_rows_v1"))
            assertNotNull(compute.transmitSpectral(FloatArray(9){.2f},null,plan))
            assertEquals("gles31_spectral_transmission_rows_v1",report)
        }
    }
    @Test fun spectralChunksQualifyEachProfileAndAbandonPartialOutputWithoutChangingTheSource() {
        val waves=4
        val plan=com.filmfoundry.kernel.SpectralTransmissionPlan(waves,DoubleArray(waves*9) { .03+(it%7)*.1 },
            DoubleArray(waves*3) { .25 },DoubleArray(waves*3) { 1.0/3 })
        val input=FloatArray((DeferredPixelCompute.MAX_SPECTRAL_PIXELS+1)*9) { (it%13)*.1f };val before=input.copyOf()
        val post=FloatArray(input.size/3) { if(it%3==0) -.1f else .2f };val originalPost=post.copyOf()
        var calls=0;var closed=0;var fail=false;var last=""
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
            override fun transmitSpectral(amounts: FloatArray,overlay: FloatArray?,config: com.filmfoundry.kernel.SpectralTransmissionPlan): FloatArray {
                calls++;assertTrue(amounts.size<=DeferredPixelCompute.MAX_SPECTRAL_PIXELS*9)
                if(fail && amounts.size==9) { amounts.fill(99f);overlay?.fill(99f);error("lost spectral device") }
                return config.reference(amounts,overlay)
            }
            override fun close() { closed++ }
        }},{last=it},selectByTiming=false).use { compute ->
            assertArrayEquals(plan.reference(input,post),compute.transmitSpectral(input,post,plan),0f)
            assertEquals(4,calls) // two qualifications and two bounded chunks
            fail=true;assertNull(compute.transmitSpectral(input,post,plan))
            assertNull(compute.transmitSpectral(input,post,plan))
        }
        assertEquals(1,closed);assertArrayEquals(before,input,0f);assertArrayEquals(originalPost,post,0f)
        assertTrue(last.startsWith("cpu_spectral_transmission_rows_v1"))
    }
    @Test fun densityComputeQualifiesSeparatelyAndPreservesTheMasterOnPartialFailure() {
        val n=DeferredPixelCompute.MAX_GPU_COMPONENTS
        val input=FloatArray(n+13) { (it%511)/80f };val before=input.copyOf()
        val expected=com.filmfoundry.kernel.DensityTransmissionCompute.reference(input)
        var calls=0;var closed=0;var report=""
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
            override fun transmit(density: FloatArray): FloatArray {
                calls++
                if(calls==3) { density.fill(5f);return FloatArray(density.size) }
                return com.filmfoundry.kernel.DensityTransmissionCompute.reference(density)
            }
            override fun close() { closed++ }
        }},{report=it},selectByTiming=false).use { compute ->
            assertArrayEquals(expected,compute.transmit(input),0f)
            assertArrayEquals(expected,compute.transmit(input),0f)
        }
        assertArrayEquals(before,input,0f);assertEquals(3,calls);assertEquals(1,closed)
        assertTrue(report.startsWith("cpu_density_transmission_rows_v1"))
        var invalidCalls=0
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
            override fun transmit(density: FloatArray): FloatArray { invalidCalls++;return FloatArray(density.size) }
        }},selectByTiming=false).use { assertArrayEquals(expected,it.transmit(input),0f) }
        assertEquals(1,invalidCalls)
    }
    @Test fun acceptedDensityComputeReceivesBoundedCopiesAndReportsItsActualOperation() {
        val input=FloatArray(DeferredPixelCompute.MAX_GPU_COMPONENTS+17) { (it%101)/50f };var reports=0;var last=""
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
            override fun transmit(density: FloatArray): FloatArray {
                assertTrue(density.size<=DeferredPixelCompute.MAX_GPU_COMPONENTS)
                return com.filmfoundry.kernel.DensityTransmissionCompute.reference(density)
            }
        }},{reports++;last=it},selectByTiming=false).use { compute ->
            assertArrayEquals(com.filmfoundry.kernel.DensityTransmissionCompute.reference(input),compute.transmit(input),0f)
            repeat(3) { compute.transmit(input) }
            assertEquals(1,reports,"Do not write preferences for every scan stripe")
        }
        assertEquals("gles31_density_transmission_rows_v1",last)
    }
    @Test fun byteLookupKeepsTheReferenceBitsAndArbitraryFloatInputKeepsItsFormula() {
        val values=FloatArray(256+512) { if(it<256) it/255f else (it-256)/511f*1.2f-.1f }+floatArrayOf(-0f,Float.MIN_VALUE)
        val expected=TransferFunctions.srgbToLinear(values)
        val actual=TransferFunctions.srgbToLinearInPlace(values.copyOf())
        for(i in values.indices) assertEquals(expected[i].toRawBits(),actual[i].toRawBits())
    }
    @Test fun largeGpuBlocksAreSplitAndPartialFailureRetriesTheEntireOriginalOnCpu() {
        val n=DeferredPixelCompute.MAX_GPU_COMPONENTS
        val values=FloatArray(n*2+17) { (it%256)/255f };val expected=TransferFunctions.srgbToLinear(values)
        var calls=0
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray): FloatArray {
                calls++;assertTrue(encoded.size<=n)
                return TransferFunctions.srgbToLinear(encoded)
            }
        }},selectByTiming=false).use { assertArrayEquals(expected,it.linearize(values.copyOf()),0f) }
        assertEquals(4,calls) // qualification + three real chunks
        calls=0;var closed=0
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray): FloatArray {
                calls++;if(calls==3) { encoded.fill(1f);error("lost after first chunk") }
                return TransferFunctions.srgbToLinear(encoded)
            }
            override fun close() { closed++ }
        }},selectByTiming=false).use { assertArrayEquals(expected,it.linearize(values.copyOf()),0f) }
        assertEquals(1,closed)
    }
    @Test fun cpuDefaultNeverCreatesHardwareAndGpuFailureKeepsTheOriginalWorkingRows() {
        val encoded=floatArrayOf(0f,.02f,.04045f,.18f,.5f,1f);val expected=TransferFunctions.srgbToLinear(encoded)
        DeferredPixelCompute(false,{error("CPU mode must not initialize hardware")}).use { assertArrayEquals(expected,it.linearize(encoded.copyOf()),0f) }
        var closed=0;var calls=0;var reported=""
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(values: FloatArray): FloatArray {
                calls++;if(calls==1) return TransferFunctions.srgbToLinear(values)
                values.fill(1f);throw IllegalStateException("lost device")
            }
            override fun close() { closed++ }
        }},{ reported=it },selectByTiming=false).use { compute ->
            assertArrayEquals(expected,compute.linearize(encoded.copyOf()),0f)
            assertArrayEquals(expected,compute.linearize(encoded.copyOf()),0f)
        }
        assertEquals(2,calls);assertEquals(1,closed);assertTrue(reported.startsWith("cpu_srgb_rows_v1"));assertTrue(reported.contains("lost device"))
    }
    @Test fun unqualifiedHardwareNeverReachesRealImageRowsAndAcceptedBackendIsClosedOnce() {
        var badCalls=0
        DeferredPixelCompute(true,{object: SrgbRowCompute { override fun linearize(values: FloatArray): FloatArray { badCalls++;return FloatArray(values.size) } }},selectByTiming=false).use {
            assertEquals(TransferFunctions.srgbToLinear(.5f),it.linearize(floatArrayOf(.5f)).single())
        }
        assertEquals(1,badCalls)
        var closed=0;var report=""
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(values: FloatArray)=TransferFunctions.srgbToLinear(values)
            override fun close() { closed++ }
        }},{report=it},selectByTiming=false).use { assertEquals(1f,it.linearize(floatArrayOf(1f)).single()) }
        assertEquals(1,closed);assertEquals("gles31_srgb_rows_v1",report)
    }
}
