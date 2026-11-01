package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class BoundedPrintMappingTest {
    private val kernel=PhoneReducedKernel()
    private fun recipe(strength: Double,mapping: String="sigmoid")=mapOf("scanner" to mapOf(
        "print_mapping_mode" to mapping,"remove_base_mask" to false,"invert_transmission" to true,
        "scan_normalize" to true,"scan_normalize_strength" to strength))
    private fun master()=OpticalMaster(FloatNd(intArrayOf(16,16,3),FloatArray(256*3){(it/3)*3f/255}),emptyMap())
    @Test fun turningDownNormalizationKeepsAnOrderedUsableNegativePrintWithoutBlownWhites() {
        val source=master();val original=source.opticalDensityRgb.values.copyOf()
        for(strength in listOf(0.0,.1,.45,.8,1.0)) {
            val result=kernel.observe(source,recipe(strength)).outputRgb.values
            assertTrue(result.all { it.isFinite() && it in 0f..1f })
            assertTrue(result[0]<.03f);assertTrue(result.last()>.9f)
            assertTrue((1 until 256).all { result[it*3]>=result[(it-1)*3] })
            assertTrue(result.count { it==1f }<result.size*.03,"strength=$strength clips broad highlights")
        }
        val old=kernel.observe(source,recipe(.45,"printlike")).outputRgb.values
        assertTrue(old.count { it>=.999f }>old.size*.35,"fixture must reproduce the old reciprocal clipping")
        assertArrayEquals(original,source.opticalDensityRgb.values,0f)
    }
    @Test fun preparedAndRowScansUseTheSameBoundedMappingAndPositiveTransmissionIsUntouched() {
        val source=master();val config=recipe(.45)
        val expected=kernel.observe(source,config).outputRgb.values
        val prepared=kernel.prepareObservation(source,config).observe(config).outputRgb.values
        assertArrayEquals(expected,prepared,0f)
        val rows=PhoneRowObservation(config);val linear=rows.linear(source).outputRgb.values
        val normalize=rows.normalizer(256) { it(linear) };val actual=linear.copyOf();normalize(actual);rows.encode(actual)
        assertArrayEquals(expected,actual,0f)
        val settings=mapOf<String,Any?>("scan_normalize" to false,"invert_transmission" to false,"remove_base_mask" to false)
        val direct=kernel.observe(source,mapOf("scanner" to settings)).outputRgb.values
        val bounded=kernel.observe(source,mapOf("scanner" to (settings+("print_mapping_mode" to "sigmoid")))).outputRgb.values
        assertArrayEquals(direct,bounded,0f)
    }
}
