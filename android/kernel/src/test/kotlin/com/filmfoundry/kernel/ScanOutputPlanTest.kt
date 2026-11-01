package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanOutputPlanTest {
    @Test fun combinedCpuPlanPreservesSeparatePassesAndWholeImageStatistics() {
        val values=FloatArray(37*3) { when(it%11) { 0->-.3f;1->0f;2->.0031308f;else->(it%19)*.13f } }
        val curve=listOf(listOf(0.0,.01),listOf(.3,.2),listOf(.7,.82),listOf(1.0,.99))
        for(mode in listOf("off","luma","rgb","flat")) for(invert in listOf(false,true)) for(curved in listOf(false,true)) {
            val scanner=mapOf("scan_normalize" to (mode!="off"),"scan_normalize_mode" to mode,
                "invert_transmission" to invert,"print_mapping_mode" to "sigmoid","transmission_light_temperature_k" to 3200)+
                if(curved) mapOf("color_curve_rgb" to List(3){curve},"tone_curve" to curve) else emptyMap()
            val recipe=mapOf("scanner" to scanner,"look" to mapOf("print_exposure_ev" to -.4,"print_contrast" to 1.3))
            val row=PhoneRowObservation(recipe)
            row.linear(OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap()))
            val statistics=if(mode=="flat") FloatArray(values.size){.2f} else values
            val blocks: ((FloatArray)->Unit)->Unit={ consume -> consume(statistics.copyOfRange(0,33));consume(statistics.copyOfRange(33,statistics.size)) }
            val normalizer=row.normalizer(values.size/3,blocks)
            val separate=values.copyOf();normalizer(separate);row.encode(separate)
            val plan=row.outputPlan(values.size/3,blocks)
            assertArrayEquals(separate,plan.reference(values),0f,"$mode/$invert/$curved")
            val combined=values.copyOf();row.output(combined,plan)
            assertArrayEquals(separate,combined,0f)
            assertEquals(-.3f,values[0])
        }
    }
    @Test fun oldBackendWithNoCombinedOperationKeepsCpuFallbackAndViewerMath() {
        val recipe=mapOf("observe_mode" to "viewer_light_table")
        val row=PhoneRowObservation(recipe,object: DensityTransmissionCompute {
            override fun transmit(density: FloatArray)=DensityTransmissionCompute.reference(density)
        })
        row.linear(OpticalMaster(FloatNd(intArrayOf(1,1,3),floatArrayOf(.1f,.2f,.3f)),emptyMap()))
        val values=floatArrayOf(.1f,.4f,1.1f)
        val plan=row.outputPlan(1) { error("Viewer must not measure scanner statistics") }
        val expected=values.copyOf();row.encode(expected);row.output(values,plan)
        assertArrayEquals(expected,values,0f)
    }
}
