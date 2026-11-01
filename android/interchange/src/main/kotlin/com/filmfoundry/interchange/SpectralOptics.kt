package com.filmfoundry.interchange

/** Portable definitions. Pool units are independent of the legacy RGB master. */
object SpectralOptics {
    const val MATERIAL_KEY = "spectral_material_profile"
    const val MEDIUM_KEY = "spectral_medium"
    const val AMOUNTS_KEY = "spectral_component_amounts"
    const val OBSERVER_KEY = "spectral_observation_profile"
    const val POST_PROCESS_KEY = "spectral_post_process_density_rgb"
    const val POST_PROCESS_MODEL = "piecewise_rgb_density_overlay_v1"

    fun postProcessWeights(wave: Double): DoubleArray = when {
        wave <= 450.0 -> doubleArrayOf(0.0, 0.0, 1.0)
        wave < 550.0 -> ((wave - 450.0) / 100.0).let { doubleArrayOf(0.0, it, 1.0-it) }
        wave < 650.0 -> ((wave - 550.0) / 100.0).let { doubleArrayOf(it, 1.0-it, 0.0) }
        else -> doubleArrayOf(1.0, 0.0, 0.0)
    }
    val components = listOf("dye_c", "dye_m", "dye_y", "metallic_silver", "residual_halide",
        "bleached_halide", "clear_support", "masking_coupler", "auxiliary_layer")

    data class Material(val metadata: Map<String, Any?>, val wave: DoubleArray, val absorbance: Array<DoubleArray>)
    data class Observer(val metadata: Map<String, Any?>, val wave: DoubleArray, val light: DoubleArray, val response: Array<DoubleArray>)

    fun material(value: Any?): Material {
        val map = objectValue(value)
        val keys = setOf("model", "profile_id", "wavelengths_nm", "component_absorbance", "component_names", "coefficient_unit")
        if (map.keys != keys || map["model"] != "normalized_pool_absorbance_v1" ||
            map["coefficient_unit"] != "normalized_final_pool_v1" || map["component_names"] != components)
            throw InterchangeException("unsupported spectral material fields, units or model.")
        id(map)
        val wave = wave(map["wavelengths_nm"])
        val absorbance = rows(map["component_absorbance"], 9, wave.size)
        return Material(map, wave, absorbance)
    }

    fun observer(value: Any?): Observer {
        val map = objectValue(value)
        val keys = setOf("model", "profile_id", "wavelengths_nm", "illuminant", "response_rgb", "working_space", "normalization")
        if (map.keys != keys || map["model"] != "spectral_transmission_observer_v1" ||
            map["normalization"] != "open_gate_per_channel_v1" || map["working_space"] != DataKernel.CAPTURE_WORKING_SPACE)
            throw InterchangeException("unsupported spectral observer fields or model.")
        id(map)
        val wave = wave(map["wavelengths_nm"])
        val light = vector(map["illuminant"], wave.size)
        val response = rows(map["response_rgb"], 3, wave.size)
        if (response.any { row -> row.indices.none { light[it] * row[it] > 0.0 } })
            throw InterchangeException("spectral observer needs illuminant overlap in each channel.")
        return Observer(map, wave, light, response)
    }

    fun validateMedium(metadata: Map<String, Any?>, amounts: FloatNd?, shape: IntArray,
                       postProcess: FloatNd? = null) {
        val definition = metadata[MEDIUM_KEY]
        if (definition == null && amounts == null && postProcess == null) return
        val map = objectValue(definition)
        val required = setOf("profile", "base_component_amounts")
        if (map.keys !in listOf(required, required + "post_process_model") || amounts == null)
            throw InterchangeException("spectral medium requires paired definition and component array.")
        val declared = "post_process_model" in map
        if (declared != (postProcess != null) || (declared && map["post_process_model"] != POST_PROCESS_MODEL))
            throw InterchangeException("spectral post-process model and density array must be paired and supported.")
        if (postProcess != null && (!postProcess.shape.contentEquals(intArrayOf(shape[0],shape[1],3)) ||
            postProcess.values.any { !(it>=-Float.MAX_VALUE && it<=Float.MAX_VALUE) }))
            throw InterchangeException("spectral post-process density must be finite signed HxWx3.")
        material(map["profile"])
        vector(map["base_component_amounts"], 9)
        if (!amounts.shape.contentEquals(intArrayOf(shape[0], shape[1], 9)) ||
            amounts.values.any { !(it>=0f && it<=Float.MAX_VALUE) })
            throw InterchangeException("spectral amounts must be finite nonnegative HxWx9 matching the optical master.")
    }

    fun objectValue(value: Any?): Map<String, Any?> {
        if (value !is Map<*, *> || value.keys.any { it !is String })
            throw InterchangeException("spectral definition must be an object.")
        @Suppress("UNCHECKED_CAST")
        return value as Map<String, Any?>
    }
    fun vector(value: Any?, size: Int): DoubleArray {
        val list = value as? List<*> ?: throw InterchangeException("spectral values must be lists.")
        if (list.size != size) throw InterchangeException("spectral vector length mismatch.")
        return DoubleArray(size) { i ->
            val v = (list[i] as? Number)?.toDouble() ?: throw InterchangeException("spectral values must be numbers.")
            if (!v.isFinite() || v < 0.0) throw InterchangeException("spectral values must be finite nonnegative.")
            v
        }
    }
    private fun rows(value: Any?, count: Int, size: Int): Array<DoubleArray> {
        val list = value as? List<*> ?: throw InterchangeException("spectra must be lists.")
        if (list.size != count) throw InterchangeException("spectral row count mismatch.")
        return Array(count) { vector(list[it], size) }
    }
    private fun wave(value: Any?): DoubleArray {
        val list = value as? List<*> ?: throw InterchangeException("wavelengths must be a list.")
        if (list.size !in 2..256) throw InterchangeException("wavelengths require 2 to 256 samples.")
        val result = vector(value, list.size)
        if (result.any { it <= 0.0 } || result.indices.drop(1).any { result[it] <= result[it-1] })
            throw InterchangeException("wavelengths must be positive and strictly increasing.")
        return result
    }
    private fun id(map: Map<String, Any?>) {
        if ((map["profile_id"] as? String).isNullOrBlank()) throw InterchangeException("spectral profile_id must be nonempty.")
    }
}
