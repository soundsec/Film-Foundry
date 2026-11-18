package com.filmfoundry.platform

import com.filmfoundry.kernel.FilmDevelopmentPlan
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FilmFormationComputeTest {
    private val plan=FilmDevelopmentPlan.prepare(mapOf("program_key" to "color_reversal"),emptyMap(),true)
    private fun input(n: Int)=FloatArray(n*8) { i -> when(i%8) { 0,3,6 ->1f;1 -> (i/8%13)/12f;7 -> (i/8%3).toFloat();else ->0f } }
    @Test fun boundedBackendAndPartialFailureRetainOriginalInput() {
        var fail=false;var calls=0;var report=""
        val data=input(QualifiedFilmDevelopmentCompute.MAX_SAMPLES+1);val before=data.copyOf()
        QualifiedFilmDevelopmentCompute(object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=error("Unrelated operation")
            override fun develop(input: FloatArray,plan: FilmDevelopmentPlan): FloatArray {
                calls++;assertTrue(input.size<=QualifiedFilmDevelopmentCompute.MAX_SAMPLES*8)
                if(fail && input.size==8) { input.fill(4f);error("lost device") }
                return plan.reference(input)
            }
        },{report=it},false).use { backend ->
            assertArrayEquals(plan.reference(data),backend.develop(data,plan),0f)
            assertTrue(report.startsWith("gles31_"));assertTrue(calls>=3)
            fail=true;assertNull(backend.develop(data,plan));assertFalse(backend.available(plan))
        }
        assertArrayEquals(before,data,0f);assertTrue(report.contains("fallback"))
    }
    @Test fun inaccurateOrSlowerDeviceIsDeclined() {
        QualifiedFilmDevelopmentCompute(object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=encoded
            override fun develop(input: FloatArray,plan: FilmDevelopmentPlan)=FloatArray(input.size/8*6)
        },selectByTiming=false).use { assertNull(it.develop(input(32),plan)) }
        val times=longArrayOf(0,100,100,101,101,102);var index=0
        QualifiedFilmDevelopmentCompute(object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=encoded
            override fun develop(input: FloatArray,plan: FilmDevelopmentPlan)=plan.reference(input)
        },clockNs={times[index++]}).use {
            assertNull(it.develop(input(32),plan));assertFalse(it.available(plan))
        }
        val fastTimes=longArrayOf(0,1,1,101,101,201);index=0
        QualifiedFilmDevelopmentCompute(object: SrgbRowCompute {
            override fun linearize(encoded: FloatArray)=encoded
            override fun develop(input: FloatArray,plan: FilmDevelopmentPlan)=plan.reference(input)
        },clockNs={fastTimes[index++]}).use {
            assertNull(it.developPrepared(input(32),plan,10_000L),"CPU packing cost must count against GPU")
        }
    }
}
