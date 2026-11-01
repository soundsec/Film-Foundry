package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneScannerCaptureTest {
    private val kernel=PhoneReducedKernel()
    private val base=mapOf<String,Any?>("scan_normalize" to false,"remove_base_mask" to false,"invert_transmission" to false)
    private fun matrix(gain: Double)=List(3) { y->List(3) { x->if(x==y) gain else 0.0 } }
    // Device clipping tests deliberately target the original illuminated raw-capture contract.
    private fun linear(master: OpticalMaster,scanner: Map<String,Any?>)=PhoneObserve.run(master,mapOf("scanner" to scanner,
        com.filmfoundry.interchange.PhoneScanModels.OPTION to com.filmfoundry.interchange.PhoneScanModels.RESULT_BASE),FloatArray(3),true)
    @Test fun responseOperatesBeforeSensorClippingAndDeviceCurveBeforeInterpretation() {
        val clear=OpticalMaster(FloatNd(intArrayOf(1,1,3),FloatArray(3)),emptyMap())
        val half=List(3) { listOf(listOf(0.0,0.0),listOf(1.0,.5)) }
        val signal=FloatArray(3){4f}
        PhoneScannerCapture(mapOf("scanner_response_matrix" to matrix(.1),"device_response_rgb" to half),emptyMap()).apply(signal)
        assertArrayEquals(floatArrayOf(.2f,.2f,.2f),signal,1e-6f)
        val result=linear(clear,base+mapOf("transmission_light_ev" to 2.0,"scanner_response_matrix" to matrix(.1),"device_response_rgb" to half))
        // The default 5500 K source has unity red; green/blue retain its chromaticity.
        assertEquals(.2f,result.outputRgb.values[0],1e-6f)
        assertTrue(result.metadata.containsKey("scanner_capture_response"))
        val density=OpticalMaster(FloatNd(intArrayOf(1,1,3),FloatArray(3){.30103f}),emptyMap(),FloatArray(3))
        val square=List(3) { listOf(listOf(0.0,0.0),listOf(.5,.25),listOf(1.0,1.0)) }
        val interpreted=linear(density,base+mapOf("remove_base_mask" to true,"invert_transmission" to true,"device_response_rgb" to square))
        assertEquals(.72f,interpreted.outputRgb.values[0],2e-6f)
    }
    @Test fun crossChannelResponseAlsoProcessesTheBaseAnchorAndLeavesMediumUntouched() {
        val values=floatArrayOf(.1f,.3f,.5f);val before=values.copyOf()
        val master=OpticalMaster(FloatNd(intArrayOf(1,1,3),values),emptyMap(),before.copyOf())
        val response=listOf(listOf(.5,.4,.1),listOf(.1,.8,.1),listOf(.2,.1,.7))
        val curves=List(3) { listOf(listOf(0.0,.03),listOf(.4,.2),listOf(1.0,.9)) }
        val result=linear(master,base+mapOf("remove_base_mask" to true,"scanner_response_matrix" to response,"device_response_rgb" to curves))
        assertArrayEquals(floatArrayOf(1f,1f,1f),result.outputRgb.values,0f)
        assertArrayEquals(before,values,0f);assertArrayEquals(before,master.clearBaseOpticalDensityRgb,0f)
    }
    @Test fun identityIsExactAndViewersDoNotUseTheScannerDevice() {
        val master=OpticalMaster(FloatNd(intArrayOf(1,2,3),floatArrayOf(.2f,.4f,.7f,.8f,.3f,.1f)),emptyMap())
        val plain=kernel.observe(master,mapOf("scanner" to base))
        val identity=base+mapOf("scanner_response_matrix" to matrix(1.0),"device_response_rgb" to List(3) { listOf(listOf(0.0,0.0),listOf(1.0,1.0)) })
        val same=kernel.observe(master,mapOf("scanner" to identity))
        assertArrayEquals(plain.outputRgb.values,same.outputRgb.values,0f);assertEquals(plain.metadata,same.metadata)
        for(mode in listOf("viewer_light_table","viewer_projection")) {
            val a=kernel.observe(master,mapOf("observe_mode" to mode,"scanner" to base))
            val b=kernel.observe(master,mapOf("observe_mode" to mode,"scanner" to (base+("scanner_response_matrix" to matrix(.1)))))
            assertArrayEquals(a.outputRgb.values,b.outputRgb.values,0f);assertEquals(a.metadata,b.metadata)
        }
    }
    @Test fun malformedDeviceSettingsFailInsteadOfBeingSilentlyIgnored() {
        assertThrows(IllegalArgumentException::class.java) { PhoneScannerCapture(mapOf("scanner_response_matrix" to listOf(listOf(1))),emptyMap()) }
        assertThrows(IllegalArgumentException::class.java) { PhoneScannerCapture(mapOf("scanner_response_matrix" to matrix(Double.NaN)),emptyMap()) }
        assertThrows(IllegalArgumentException::class.java) { PhoneScannerCapture(mapOf("device_response_rgb" to List(3) { listOf(listOf(.5,0),listOf(.5,1)) }),emptyMap()) }
    }
}
