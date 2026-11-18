package com.filmfoundry.interchange

/** Formation cost is frozen at capture. Missing values retain the archived implementation. */
object PhoneFormationProfile {
    const val OPTION = "phone_formation_profile"
    const val ROLL_SETTING = "formation_profile"
    const val PREVIOUS_LIGHTWEIGHT = "phone_formation_fast_v1"
    const val TIME_LIGHTWEIGHT = "phone_formation_fast_v2"
    const val LIGHTWEIGHT = "phone_formation_fast_v3"
    const val DETAILED = "phone_formation_detailed_v1"

    fun validate(value: Any?): String? {
        require(value == null || isLightweight(value) || value == DETAILED) { "Unsupported phone formation profile: $value" }
        return value as String?
    }

    fun isLightweight(value: Any?) = value == LIGHTWEIGHT || value == TIME_LIGHTWEIGHT || value == PREVIOUS_LIGHTWEIGHT
    fun hasTimeResponse(value: Any?) = value == LIGHTWEIGHT || value == TIME_LIGHTWEIGHT

    fun resolve(recipe: Map<String, Any?>, metadata: Map<String, Any?>): String? =
        validate(if (metadata.containsKey(OPTION)) metadata[OPTION] else recipe[OPTION])
}
