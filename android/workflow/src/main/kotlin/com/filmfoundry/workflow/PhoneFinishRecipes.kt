package com.filmfoundry.workflow

/** Phone product defaults using the existing shared scanner fields; no extra medium format. */
object PhoneFinishRecipes {
    const val AUTO="auto"
    // Append to preserve archived UI draft indices (custom remains 3).
    val styles=listOf("ready","soft","rich","custom",AUTO)
    val automaticKeys=setOf("print_mapping_mode","scan_normalize","scan_normalize_strength","scan_normalize_mode",
        "scan_black_percentile","scan_white_percentile","invert_transmission","remove_base_mask")
    fun scanner(style: String): Map<String,Any?> {
        require(style in styles && style != "custom")
        if(style==AUTO) return mapOf("print_mapping_mode" to "sigmoid","scan_normalize" to true,
            "scan_normalize_strength" to 1.0,"scan_normalize_mode" to "luma",
            "scan_black_percentile" to .25,"scan_white_percentile" to 99.7)
        return mapOf(
            "print_mapping_mode" to "sigmoid","scan_normalize" to true,
            "scan_normalize_strength" to 1.0,"scan_normalize_mode" to "luma",
            "scan_black_percentile" to .25,"scan_white_percentile" to 99.7,
            "scan_saturation" to when(style) { "rich"->1.28;"soft"->1.08;else->1.18 },
            "tone_curve" to when(style) {
                "soft"->listOf(listOf(0.0,0.0),listOf(.2,.23),listOf(.5,.52),listOf(.8,.79),listOf(1.0,1.0))
                "rich"->listOf(listOf(0.0,0.0),listOf(.2,.13),listOf(.5,.46),listOf(.8,.84),listOf(1.0,1.0))
                else->listOf(listOf(0.0,0.0),listOf(.2,.16),listOf(.5,.49),listOf(.8,.82),listOf(1.0,1.0))
            })
    }
    fun observation(base: Map<String,Any?>,fallback: Map<String,Any?>,style: String): Map<String,Any?> {
        val existing=(base["scanner"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value } ?: fallback
        if(style == "custom") return existing
        // Only neutral interpretation/range controls change. User colour and curves survive.
        if(style == AUTO) return (existing-automaticKeys)+scanner(AUTO)
        // Starting a named finish resets output curves/negative inversion; spectral readout remains explicit.
        return (existing-"tone_curve"-"color_curve_rgb"-"invert_transmission"-"remove_base_mask")+scanner(style)
    }
}
