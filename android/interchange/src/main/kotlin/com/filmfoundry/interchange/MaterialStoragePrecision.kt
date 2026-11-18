package com.filmfoundry.interchange

/** Conservative subset of desktop appearance_float16_v1. This is storage, not FP16 chemistry.
 * Optical density, spectral amounts, chemical pools, coverage and latent files remain FP32. */
object MaterialStoragePrecision {
    enum class Policy { AUTO, FULL }
    private val appearance=mapOf(
        "peel_apart_transfer_v1" to setOf("paper_texture","backing_texture","paper"),
        "integral_instant_v1" to setOf("paper_texture","backing_texture","paper"),
        "daguerreotype_surface_v1" to setOf("polish_x","polish_y","microtexture"),
        // Basic phone paper/cyanotype reflectance exceeds our stricter 2e-4 linear
        // observation bound when stored in half; keep these at 32 until separately qualified.
        "photographic_paper_v1" to emptySet(),"wet_collodion_v1" to setOf("surface"),
        "cyanotype_v1" to emptySet(),"platinum_palladium_v1" to setOf("paper"))
    fun encode(family: String,name: String,field: FloatNd,policy: Policy=Policy.AUTO): NpyValue.Float32 {
        val half=policy==Policy.AUTO && name in appearance[family].orEmpty() &&
            field.values.all { it.isFinite() && it in -65504f..65504f }
        return NpyValue.Float32(field,if(half) 16 else 32)
    }
}
