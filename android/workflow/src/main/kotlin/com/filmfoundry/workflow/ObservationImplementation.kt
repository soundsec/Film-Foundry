package com.filmfoundry.workflow

import com.filmfoundry.kernel.PhoneReducedKernel
import com.filmfoundry.interchange.PhoneScanModels

/** Private output provenance; portable recipe fields remain unchanged. */
object ObservationImplementation {
    const val LEGACY="phone_scan_observation_v0"
    const val DEVICE_RESPONSE="phone_scan_device_response_v1"
    const val RESULT_DEFAULTS="phone_scan_result_defaults_v2"
    const val RESULT_DEVICE="phone_scan_result_device_v3"
    const val CALIBRATED="phone_scan_calibrated_light_v4"
    const val CALIBRATED_DEVICE="phone_scan_calibrated_device_v5"
    const val RANGE_FIRST="phone_scan_range_first_v6"
    const val RANGE_FIRST_DEVICE="phone_scan_range_first_device_v7"
    fun forRecipe(recipe: Map<String,Any?>): String {
        require(recipe[PhoneScanModels.OPTION]==null || recipe[PhoneScanModels.OPTION] in listOf(PhoneScanModels.LEGACY,PhoneScanModels.RESULT_BASE,PhoneScanModels.CALIBRATED,PhoneScanModels.CURRENT)) { "Unknown scan interpretation implementation" }
        val device=PhoneReducedKernel().usesScannerDeviceResponse(recipe)
        return when(recipe[PhoneScanModels.OPTION]) {
            PhoneScanModels.LEGACY -> if(device) DEVICE_RESPONSE else LEGACY
            PhoneScanModels.RESULT_BASE -> if(device) RESULT_DEVICE else RESULT_DEFAULTS
            PhoneScanModels.CALIBRATED -> if(device) CALIBRATED_DEVICE else CALIBRATED
            else -> if(device) RANGE_FIRST_DEVICE else RANGE_FIRST
        }
    }
    fun frozenRuntime(recipe: Map<String,Any?>,model: String): Map<String,Any?> {
        val implementation=when(model) {
            LEGACY,DEVICE_RESPONSE -> PhoneScanModels.LEGACY
            RESULT_DEFAULTS,RESULT_DEVICE -> PhoneScanModels.RESULT_BASE
            CALIBRATED,CALIBRATED_DEVICE -> PhoneScanModels.CALIBRATED
            RANGE_FIRST,RANGE_FIRST_DEVICE -> PhoneScanModels.CURRENT
            else -> throw IllegalArgumentException("Frozen scanner implementation unavailable")
        }
        return (recipe+(PhoneScanModels.OPTION to implementation)).also {
            require(forRecipe(it)==model) { "Frozen scanner response differs" }
        }
    }
    fun matches(recorded: String?,expected: String)=recorded==expected || recorded==null && expected==LEGACY
}
