package com.filmfoundry.kernel

import com.filmfoundry.interchange.PhoneGrainModels

/** Freeze the new multiscale grain definition at exposure; old latents have no such profile. */
internal object PhoneGrainProfile {
    const val KEY="phone_grain_profile"
    const val MODEL=PhoneGrainModels.CURRENT
    const val PREVIOUS_MODEL=PhoneGrainModels.PREVIOUS
    fun fromRecipe(recipe: Map<String,Any?>): Map<String,Any?>? {
        val film=recipe["film"] as? Map<*,*> ?: return null
        if(film["grain_scales"] == null) return null
        val look=recipe["look"] as? Map<*,*> ?: emptyMap<Any,Any>()
        fun number(raw: Any?,default: Float,min: Float,max: Float): Float {
            if(raw == null) return default
            require(raw is Number && raw.toFloat().isFinite() && raw.toFloat() in min..max) { "Invalid grain profile number" }
            return raw.toFloat()
        }
        val scales=film["grain_scales"] as? List<*> ?: error("grain_scales must be an array")
        val weights=film["grain_scale_weights"] as? List<*> ?: listOf(.58,.30,.12)
        require(scales.size in 1..4 && weights.size == scales.size)
        val parsed=scales.map { number(it,0f,.00001f,.1f) }
        val energy=weights.map { number(it,0f,0f,10f) };require(energy.sum()>0)
        val strength=if(recipe["enable_grain"] == false) 0f else number(film["grain_strength"],.025f,0f,.25f)*number(look["grain_multiplier"],1f,0f,4f)
        val model=recipe[PhoneGrainModels.EXPOSURE_OPTION] ?: MODEL
        require(model in listOf(MODEL,PREVIOUS_MODEL,PhoneGrainModels.LAYERED)) { "Unknown phone grain implementation" }
        return mapOf("model" to model,"grain_strength" to strength,
            "scales" to parsed,"weights" to energy,"size_multiplier" to number(look["grain_size_multiplier"],1f,.1f,8f),
            "midtone_mu" to number(film["grain_midtone_mu"],.48f,0f,1f),"midtone_sigma" to number(film["grain_midtone_sigma"],.22f,.01f,1f))
    }
}
