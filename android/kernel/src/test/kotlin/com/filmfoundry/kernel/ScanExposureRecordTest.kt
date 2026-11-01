package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.interchange.PhoneScanModels
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanExposureRecordTest {
    @Test fun recordsDistinguishCaptureAndCalibratedExposureWithoutChangingViewerOrMedium() {
        val kernel=PhoneReducedKernel()
        val master=OpticalMaster(FloatNd(intArrayOf(1,3,3),FloatArray(9){.2f+it*.05f}),mapOf("image_polarity" to "negative"),floatArrayOf(.2f,.2f,.2f))
        val original=master.opticalDensityRgb.values.copyOf()
        val base=mapOf("scanner" to mapOf("transmission_light_ev" to -1.0))
        for(model in listOf(PhoneScanModels.CURRENT,PhoneScanModels.RESULT_BASE,PhoneScanModels.LEGACY)) {
            val recipe=base+(PhoneScanModels.OPTION to model)
            val record=kernel.observe(master,recipe).metadata["scanner_light_exposure"] as Map<*,*>
            val calibrated=model==PhoneScanModels.CURRENT
            assertEquals(if(calibrated) "post_normalization" else "transmission_capture",record["stage"])
            assertEquals("scanner.transmission_light_ev",record["parameter"])
            assertEquals(-1f,record["lamp_ev"])
            assertEquals(if(calibrated) 1f else -1f,record["effective_ev"])
            assertEquals(if(calibrated) 0f else -1f,record["calibration_ev"])
            for(mode in listOf("viewer_light_table","viewer_projection")) {
                assertFalse(kernel.observe(master,recipe+("observe_mode" to mode)).metadata.containsKey("scanner_light_exposure"))
            }
        }
        assertArrayEquals(original,master.opticalDensityRgb.values,0f)
    }
}
