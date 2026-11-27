package com.filmfoundry.interchange

/** Formation cost is frozen at capture. Missing values retain the archived implementation. */
object PhoneFormationProfile {
    const val OPTION = "phone_formation_profile"
    const val ROLL_SETTING = "formation_profile"
    const val PREVIOUS_LIGHTWEIGHT = "phone_formation_fast_v1"
    const val TIME_LIGHTWEIGHT = "phone_formation_fast_v2"
    const val MONO_LIGHTWEIGHT = "phone_formation_fast_v3"
    const val GRAIN_LIGHTWEIGHT = "phone_formation_fast_v4"
    const val STOCK_LIGHTWEIGHT = "phone_formation_fast_v5"
    const val LIGHTWEIGHT = "phone_formation_fast_v6"
    const val DETAILED = "phone_formation_detailed_v1"
    const val PREVIOUS_STOCK_DETAILED = "phone_formation_detailed_v2"
    const val STOCK_DETAILED = "phone_formation_detailed_v3"

    fun validate(value: Any?): String? {
        require(value == null || isLightweight(value) || value == DETAILED || isStockDetailed(value)) { "Unsupported phone formation profile: $value" }
        return value as String?
    }

    private fun isStockDetailed(value: Any?) = value == PREVIOUS_STOCK_DETAILED || value == STOCK_DETAILED
    fun hasBathDose(value: Any?) = value == LIGHTWEIGHT || value == STOCK_DETAILED
    fun hasStockCurve(value: Any?) = value == LIGHTWEIGHT || value == STOCK_LIGHTWEIGHT || isStockDetailed(value)
    fun hasCalibratedGrain(value: Any?) = value == LIGHTWEIGHT || value == STOCK_LIGHTWEIGHT || value == GRAIN_LIGHTWEIGHT
    fun hasMonoResponse(value: Any?) = hasCalibratedGrain(value) || value == MONO_LIGHTWEIGHT || isStockDetailed(value)
    fun isLightweight(value: Any?) = hasCalibratedGrain(value) || value == MONO_LIGHTWEIGHT || value == TIME_LIGHTWEIGHT || value == PREVIOUS_LIGHTWEIGHT
    fun hasTimeResponse(value: Any?) = (isLightweight(value) && value != PREVIOUS_LIGHTWEIGHT) || value == STOCK_DETAILED

    fun omitsSpatial(value: Any?) = isLightweight(value) && value != LIGHTWEIGHT

    fun resolve(recipe: Map<String, Any?>, metadata: Map<String, Any?>): String? =
        validate(if (metadata.containsKey(OPTION)) metadata[OPTION] else recipe[OPTION])
}
