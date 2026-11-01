package com.filmfoundry.interchange

/** Private execution selector for frozen scan jobs; not a portable recipe field. */
object PhoneScanModels {
    const val OPTION="phone_scan_interpretation_model"
    const val LEGACY="phone_coupled_base_inversion_v1"
    const val RESULT_BASE="phone_result_base_inversion_v2"
    const val CALIBRATED="phone_calibrated_light_exposure_v3"
    const val CURRENT="phone_range_first_lamp_color_v4"
}
