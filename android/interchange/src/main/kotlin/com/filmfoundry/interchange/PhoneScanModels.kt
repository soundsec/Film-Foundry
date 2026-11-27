package com.filmfoundry.interchange

/** Private execution selector for frozen scan jobs; not a portable recipe field. */
object PhoneScanModels {
    const val OPTION="phone_scan_interpretation_model"
    const val LEGACY="phone_coupled_base_inversion_v1"
    const val RESULT_BASE="phone_result_base_inversion_v2"
    const val CALIBRATED="phone_calibrated_light_exposure_v3"
    const val CURRENT="phone_range_first_lamp_color_v4"
    const val NEUTRAL="phone_desktop_neutral_print_v5"

    fun desktopPrint(recipe: Map<String,Any?>): Boolean {
        val mapping=(recipe["scanner"] as? Map<*,*>)?.get("print_mapping_mode") ?: recipe["print_mapping_mode"]
        return when (recipe[OPTION]) {
            null -> mapping == "printlike"
            NEUTRAL -> true
            else -> false
        }
    }
}
