package com.filmfoundry.kernel

import com.filmfoundry.interchange.FloatNd
import kotlin.math.pow
import kotlin.math.max
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

/** Independent numerical expectations, not a second call to the production renderer. */
class ScanLampFormulaTest {
    private val kernel=PhoneReducedKernel()
    private fun encode(x: Double): Double = (if(x<=.0031308) 12.92*x else 1.055*x.pow(1.0/2.4)-.055).coerceIn(0.0,1.0)
    private fun decode(x: Double): Double = if(x<=.04045) x/12.92 else ((x+.055)/1.055).pow(2.4)

    @Test fun absoluteDensityWedgeMatchesBeerLambertInversionAndOneSignedExposure() {
        val densities=listOf(.30,.45,.60,.85)
        val base=.12
        val film=OpticalMaster(FloatNd(intArrayOf(1,4,3),FloatArray(12){densities[it/3].toFloat()}),
            mapOf("image_polarity" to "negative"),FloatArray(3){base.toFloat()})
        var worst=0.0;var checks=0
        for(invert in listOf(false,true)) for(remove in listOf(false,true)) for(ev in listOf(-2.0,0.0,2.0)) for(gain in listOf(-.5,.5)) {
            val recipe=mapOf("scanner" to mapOf("print_mapping_mode" to "sigmoid","remove_base_mask" to remove,
                "invert_transmission" to invert,"scan_normalize" to false,"scan_saturation" to 1.0,
                "transmission_light_temperature_k" to 5500.0,"transmission_light_ev" to ev),"look" to mapOf("print_exposure_ev" to gain))
            val actual=kernel.observe(film,recipe).outputRgb.values
            densities.forEachIndexed { i,density ->
                // At 5500 K the red source response is exactly one. No production
                // capture/encoding/normalization helpers are used for this expectation.
                val transmitted=10.0.pow(-density)/(if(remove) 10.0.pow(-base) else 1.0)
                val signal=if(invert) max(.18/transmitted-.18,0.0) else transmitted
                val print=if(invert) signal/(1.0+signal) else signal
                val expected=encode(print*2.0.pow(gain+if(invert) -ev else ev))
                val error=kotlin.math.abs(expected-actual[i*3]);worst=max(worst,error);checks++
                assertEquals(expected,actual[i*3].toDouble(),3e-6,"density=$density invert=$invert base=$remove EV=$ev gain=$gain")
            }
        }
        println("Independent absolute wedge: $checks samples; max error=$worst")
    }

    @Test fun independentExposureRatioSurvivesBothNormalizationModesAndStrengths() {
        var worst=0.0;var checks=0
        for(positive in listOf(false,true)) for(invert in listOf(false,true)) for(remove in listOf(false,true))
            for(mode in listOf("luma","rgb")) for(strength in listOf(0.0,.45,1.0)) {
            val film=OpticalMaster(FloatNd(intArrayOf(3,11,3),FloatArray(99){i->.28f+(i/3%11)*.047f+(i%3)*.025f}),
                mapOf("image_polarity" to if(positive) "positive" else "negative"),floatArrayOf(.1f,.14f,.17f))
            val scanner=mapOf("print_mapping_mode" to "sigmoid","remove_base_mask" to remove,"invert_transmission" to invert,
                "scan_normalize" to true,"scan_normalize_strength" to strength,"scan_normalize_mode" to mode)
            val reference=kernel.observe(film,mapOf("scanner" to scanner)).outputRgb.values
            for(ev in listOf(-1.0,1.0)) {
                val actual=kernel.observe(film,mapOf("scanner" to (scanner+("transmission_light_ev" to ev)))).outputRgb.values
                for(i in reference.indices) {
                    // Ignore already clipped reference channels: their pre-clipped
                    // magnitude cannot be reconstructed from a display value.
                    if(reference[i]<=.001f || reference[i]>=.999f) continue
                    val expected=encode(decode(reference[i].toDouble())*2.0.pow(if(invert) -ev else ev))
                    val error=kotlin.math.abs(expected-actual[i]);worst=max(worst,error);checks++
                    assertEquals(expected,actual[i].toDouble(),3e-6,"$positive/$invert/$remove/$mode/$strength/$ev channel=$i")
                }
            }
        }
        assertTrue(checks>1000)
        println("Independent exposure-ratio oracle: $checks samples; max error=$worst")
    }
}
