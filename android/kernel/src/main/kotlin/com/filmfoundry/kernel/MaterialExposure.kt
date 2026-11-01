package com.filmfoundry.kernel

import com.filmfoundry.interchange.DataKernel

/** Small setup values only. No per-pixel spectral buffers or desktop runtime. */
internal class PhoneSpectralExposure private constructor(
    val metadata: Map<String, Any?>,
    val matrix: Array<FloatArray>,
) {
    val smits: Boolean = metadata["model"] == DataKernel.SMITS_EXPOSURE_MODEL_ID

    fun exposure(r: Float, g: Float, b: Float, layer: Int): Float {
        val row = matrix[layer]
        if (!smits) return r * row[0] + g * row[1] + b * row[2]
        val lowIndex = if (r <= g && r <= b) 0 else if (g <= b) 1 else 2
        val highIndex = if (r >= g && r >= b) 0 else if (g >= b) 1 else 2
        val low = minOf(r, g, b)
        val high = maxOf(r, g, b)
        val middle = if (r <= g) { if (g <= b) g else maxOf(r, b) }
            else { if (r <= b) r else maxOf(g, b) }
        return low * row[0] + (middle-low) * row[1+lowIndex] + (high-middle) * row[4+highIndex]
    }

    companion object {
        fun from(value: Any?, layers: Int): PhoneSpectralExposure {
            val profile = objectValue(value, "spectral_exposure_profile")
            if (profile.keys != DataKernel.SPECTRAL_EXPOSURE_PROFILE_FIELDS.toSet() ||
                profile["model"] !in listOf(DataKernel.SPECTRAL_EXPOSURE_MODEL_ID, DataKernel.SMITS_EXPOSURE_MODEL_ID)
            ) throw KernelException("unsupported spectral_exposure_profile fields or model.")
            val id = profile["profile_id"] as? String
            if (id.isNullOrBlank()) throw KernelException("spectral profile_id must be nonempty.")
            if (profile["working_space"] != DataKernel.CAPTURE_WORKING_SPACE) {
                throw KernelException("spectral profile requires the declared linear sRGB working space.")
            }
            val rawWave = profile["wavelengths_nm"] as? List<*>
                ?: throw KernelException("wavelengths_nm must be an array.")
            if (rawWave.size !in 2..256) throw KernelException("wavelengths_nm needs 2 to 256 samples.")
            val wave = vector(rawWave, rawWave.size, "wavelengths_nm", positive = true)
            if (wave.zipWithNext().any { (a, b) -> b <= a }) {
                throw KernelException("wavelengths_nm must be strictly increasing.")
            }
            val model = profile["model"] as String
            val basisCount = if (model == DataKernel.SMITS_EXPOSURE_MODEL_ID) 7 else 3
            val basis = spectra(profile["rgb_basis"], basisCount, wave.size, "rgb_basis")
            val sensitivity = spectra(profile["layer_sensitivity"], layers, wave.size, "layer_sensitivity")
            val gain = vector(profile["layer_gain"], layers, "layer_gain", positive = true)
            val weights = DoubleArray(wave.size) { n ->
                when (n) {
                    0 -> (wave[1] - wave[0]) * 0.5
                    wave.lastIndex -> (wave[n] - wave[n - 1]) * 0.5
                    else -> ((wave[n] - wave[n - 1]) + (wave[n + 1] - wave[n])) * 0.5
                }
            }
            val matrix = Array(layers) { layer ->
                FloatArray(basisCount) { channel ->
                    var sum = 0.0
                    for (n in wave.indices) {
                        sum += sensitivity[layer][n] * basis[channel][n] * weights[n] * gain[layer]
                    }
                    if (!sum.isFinite() || sum > Float.MAX_VALUE.toDouble()) {
                        throw KernelException("spectral exposure matrix exceeds the finite float32 range.")
                    }
                    sum.toFloat()
                }
            }
            if (matrix.any { row -> row.none { it > 0f } }) {
                throw KernelException("spectral sensitivity has no representable RGB-basis overlap.")
            }
            return PhoneSpectralExposure(mapOf(
                "model" to model,
                "profile_id" to id,
                "working_space" to DataKernel.CAPTURE_WORKING_SPACE,
                "wavelengths_nm" to wave,
                "rgb_basis" to basis,
                "layer_sensitivity" to sensitivity,
                "layer_gain" to gain,
            ), matrix)
        }

        private fun spectra(value: Any?, rows: Int, size: Int, name: String): List<List<Double>> {
            val raw = value as? List<*> ?: throw KernelException("$name must be an array.")
            if (raw.size != rows) throw KernelException("$name must contain $rows spectra matching the material.")
            return raw.map { row ->
                vector(row, size, name).also {
                    if (it.none { sample -> sample > 0.0 }) throw KernelException("$name requires nonzero support.")
                }
            }
        }

        private fun vector(value: Any?, size: Int, name: String, positive: Boolean = false): List<Double> {
            val raw = value as? List<*> ?: throw KernelException("$name must be an array.")
            if (raw.size != size) throw KernelException("$name must contain $size values.")
            return raw.map { number(it, name, positive) }
        }
    }
}

internal data class PhoneMaterialGeometry(
    val longEdgeMm: Double,
    val referenceShortEdgeMm: Double,
    val height: Int,
    val width: Int,
) {
    fun metadata(): Map<String, Any?> = mapOf(
        "model" to DataKernel.MATERIAL_GEOMETRY_MODEL_ID,
        "frame_long_edge_mm" to longEdgeMm,
        "reference_short_edge_mm" to referenceShortEdgeMm,
        "full_shape" to listOf(height, width),
    )

    companion object {
        fun resolve(recipe: Map<String, Any?>, height: Int, width: Int): PhoneMaterialGeometry? {
            val film = recipe["film"] as? Map<*, *>
            val develop = ((recipe["chemistry"] as? Map<*, *>)?.entries.orEmpty() +
                (recipe["develop"] as? Map<*, *>)?.entries.orEmpty()).associate { it.key.toString() to it.value }
            val reference = film?.get("spatial_reference_short_edge_mm")
            val rawLong = develop["frame_long_edge_mm"]
            if (reference == null) {
                if (rawLong != null) throw KernelException("frame_long_edge_mm requires spatial_reference_short_edge_mm.")
                return null
            }
            val ref = number(reference, "spatial_reference_short_edge_mm", positive = true)
            val long = if (rawLong != null) number(rawLong, "frame_long_edge_mm", positive = true) else {
                val key = (develop["frame_size"] ?: "35mm").toString().trim().lowercase().replace('-', '_').replace(' ', '_')
                mapOf("half_frame" to 24.0, "35mm" to 36.0, "6x6" to 56.0, "6x7" to 69.0, "4x5" to 120.0)[key]
                    ?: throw KernelException("unknown physical frame_size; supply frame_long_edge_mm.")
            }
            return PhoneMaterialGeometry(long, ref, height, width)
        }

        fun from(value: Any?, height: Int, width: Int): PhoneMaterialGeometry {
            val raw = objectValue(value, "material_geometry")
            if (raw.keys != setOf("model", "frame_long_edge_mm", "reference_short_edge_mm", "full_shape") ||
                raw["model"] != DataKernel.MATERIAL_GEOMETRY_MODEL_ID
            ) throw KernelException("unsupported material_geometry fields or model.")
            val shape = raw["full_shape"] as? List<*>
                ?: throw KernelException("material_geometry full_shape must be an array.")
            if (shape.size != 2 || shape.any { it !is Number || !it.toDouble().isFinite() || it.toDouble() % 1.0 != 0.0 } ||
                (shape[0] as Number).toDouble() != height.toDouble() ||
                (shape[1] as Number).toDouble() != width.toDouble()
            ) throw KernelException("latent dimensions do not match material_geometry.")
            return PhoneMaterialGeometry(
                number(raw["frame_long_edge_mm"], "frame_long_edge_mm", positive = true),
                number(raw["reference_short_edge_mm"], "reference_short_edge_mm", positive = true), height, width,
            )
        }
    }
}

private fun objectValue(value: Any?, name: String): Map<String, Any?> {
    val raw = value as? Map<*, *> ?: throw KernelException("$name must be an object.")
    if (raw.keys.any { it !is String }) throw KernelException("$name fields must be strings.")
    return raw.entries.associate { it.key as String to it.value }
}

private fun number(value: Any?, name: String, positive: Boolean = false): Double {
    val raw = value as? Number ?: throw KernelException("$name must contain numeric values.")
    val result = raw.toDouble()
    if (!result.isFinite() || result < 0.0 || positive && result == 0.0) {
        throw KernelException("$name must be finite and ${if (positive) "positive" else "non-negative"}.")
    }
    return result
}
