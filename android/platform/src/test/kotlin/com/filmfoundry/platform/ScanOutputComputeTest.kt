package com.filmfoundry.platform

import com.filmfoundry.kernel.*
import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanOutputComputeTest {
    private fun plan(): ScanOutputPlan {
        val row=PhoneRowObservation(mapOf("scanner" to mapOf("scan_normalize" to false)))
        row.linear(OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap()))
        return row.outputPlan(1) {error("Normalization is disabled")}
    }
    private open class Backend: SrgbRowCompute {
        var closed=false;var outputCalls=0
        override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
        override fun encodeOutput(linear: FloatArray,plan: ScanOutputPlan): FloatArray? {
            outputCalls++;return plan.reference(linear)
        }
        override fun close() { closed=true }
    }
    @Test fun combinedDispatchChunksAndProtectsSourceOwnership() {
        val backend=object: Backend() {
            override fun encodeOutput(linear: FloatArray,plan: ScanOutputPlan)=super.encodeOutput(linear,plan).also { linear.fill(0f) }
        }
        val plan=plan();val input=FloatArray(DeferredPixelCompute.MAX_GPU_COMPONENTS+3){(it%31)*.04f};val before=input.copyOf()
        DeferredPixelCompute(true,{backend},selectByTiming=false).use { compute ->
            assertArrayEquals(plan.reference(input),compute.encodeOutput(input,plan),0f)
            assertArrayEquals(before,input,0f);assertEquals(3,backend.outputCalls)
        }
        assertTrue(backend.closed)
    }
    @Test fun wrongValuesAndPartialFailureDiscardOutput() {
        val plan=plan();val input=FloatArray(DeferredPixelCompute.MAX_GPU_COMPONENTS+3){.2f}
        for(failAfter in listOf(1,3)) {
            val backend=object: Backend() {
                override fun encodeOutput(linear: FloatArray,plan: ScanOutputPlan): FloatArray {
                    val result=super.encodeOutput(linear,plan)!!
                    if(outputCalls==failAfter) result.fill(Float.NaN)
                    return result
                }
            }
            DeferredPixelCompute(true,{backend},selectByTiming=false).use { compute ->
                assertNull(compute.encodeOutput(input,plan));assertTrue(backend.closed)
                assertTrue(input.all {it==.2f})
            }
        }
    }
    @Test fun slowerOrUnsupportedOutputDoesNotDisableOtherOperations() {
        val plan=plan();val input=FloatArray(9){.2f};val backend=Backend();var tick=0
        val times=longArrayOf(0,100,100,101,101,102)
        DeferredPixelCompute(true,{backend},clockNs={times[tick++]}).use { compute ->
            assertArrayEquals(plan.reference(input),compute.encodeOutput(input,plan),0f)
            assertNull(compute.encodeOutput(input,plan));assertFalse(backend.closed)
        }
        var outputCalls=0;var report=""
        DeferredPixelCompute(true,{object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=TransferFunctions.srgbToLinear(encoded)
            override fun encodeOutput(linear: FloatArray,plan: ScanOutputPlan): FloatArray? {outputCalls++;return null}
        }},{report=it},selectByTiming=false).use { compute ->
            assertNull(compute.encodeOutput(input,plan));assertNull(compute.encodeOutput(input,plan));assertEquals(1,outputCalls)
            compute.linearize(input);assertTrue(report.startsWith("gles31_srgb"))
        }
    }
    @Test fun nonfiniteInputIsRejectedBeforeInitializingHardware() {
        val plan=plan()
        DeferredPixelCompute(true,{error("Invalid rows must not initialize hardware")}).use { compute ->
            assertThrows(IllegalArgumentException::class.java) { compute.encodeOutput(floatArrayOf(0f,Float.NaN,0f),plan) }
        }
    }
    @Test fun laterImageRowsCanReconsiderAnUnrepresentativeFirstBlock() {
        val backend=Backend();val plan=plan();val input=FloatArray(9){.2f};var tick=0;var report=""
        val times=longArrayOf(0,100,100,101,101,102,200,201,201,301,301,401)
        DeferredPixelCompute(true,{backend},{report=it},clockNs={times[tick++]}).use { compute ->
            compute.encodeOutput(input,plan)
            repeat(14) { assertNull(compute.encodeOutput(input,plan)) }
            assertArrayEquals(plan.reference(input),compute.encodeOutput(input,plan),0f)
            assertTrue(report.startsWith("gles31_scan_output"));assertEquals(5,backend.outputCalls)
        }
    }
}
