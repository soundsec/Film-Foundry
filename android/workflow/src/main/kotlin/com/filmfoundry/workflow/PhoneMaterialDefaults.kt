package com.filmfoundry.workflow

/** New-roll defaults only; never applied to an existing roll or latent. */
object PhoneMaterialDefaults {
    const val REVISION="phone_exposure_optics_v1"
    /** Loading controls modify a stock's character; Natural retains its own strength. */
    fun withLevels(film: Map<String,Any?>,grain: Int,mtf: Int,halo: Int,size: Double): Map<String,Any?> {
        require(grain in 0..3 && mtf in 0..2 && halo in 0..2)
        fun strength(key: String,standard: Double,multiplier: Double,limit: Double): Double {
            val base=(film[key] as? Number)?.toDouble() ?: standard
            require(base.isFinite() && base in 0.0..limit) { "Invalid $key" }
            if(multiplier==0.0) return 0.0
            if(multiplier==1.0) return base
            return ((if(base==0.0) standard else base)*multiplier).coerceAtMost(limit)
        }
        val grainMultiplier=listOf(0.0,.48,1.0,2.0)[grain]
        val values=mutableMapOf<String,Any?>(
            "grain_strength" to strength("grain_strength",.025,grainMultiplier,.25),
            "emulsion_mtf_strength" to strength("emulsion_mtf_strength",.22,listOf(0.0,1.0,.5/.22)[mtf],1.0),
            "halation_strength" to strength("halation_strength",.12,listOf(0.0,1.0,2.5)[halo],2.0))
        // Grain Off must also disable a stock's independent retained-silver grain.
        if(film.containsKey("silver_grain_strength")) {
            val silver=(film["silver_grain_strength"] as Number).toDouble()
            require(silver.isFinite() && silver in 0.0..0.5)
            values["silver_grain_strength"]=(silver*grainMultiplier).coerceAtMost(.5)
        }
        return withEffects(film,.22,.12,size)+values
    }
    fun withEffects(film: Map<String,Any?>,mtf: Double,halo: Double,size: Double): Map<String,Any?> {
        require(mtf in 0.0..1.0 && halo in 0.0..2.0 && size in .1..8.0)
        val defaults=mapOf("grain_scales" to listOf(.0012*size,.0035*size,.010*size),"grain_scale_weights" to listOf(.70,.22,.08),
            "emulsion_mtf_strength" to mtf,"emulsion_blur_radius" to .0012,
            "halation_strength" to halo,"halation_threshold" to .72,"halation_softness" to .18,
            "halation_core_radius" to .004,"halation_outer_radius" to .018,"halation_core_mix" to .62)
        var result=defaults+film
        if(mtf == 0.0) result=result+("emulsion_mtf_strength" to 0.0)
        if(halo == 0.0) result=result+("halation_strength" to 0.0)
        if(size != 1.0 && film["grain_scales"] is List<*>) result=result+("grain_scales" to (film["grain_scales"] as List<*>).map { (it as Number).toDouble()*size })
        return result
    }
    fun exposureRecipe(film: Map<String,Any?>,captureSettings: Map<String,Any?>): Map<String,Any?> {
        var base: Map<String,Any?> =if(captureSettings["spatial_effects_revision"] == REVISION) mapOf("film" to film)
            else mapOf("film" to (film-"grain_scales"-"grain_scale_weights"),"enable_mtf" to false,"enable_halation" to false)
        if(captureSettings[com.filmfoundry.interchange.PhoneMaterialPhenomena.ROLL_SETTING]==com.filmfoundry.interchange.PhoneMaterialPhenomena.CURRENT)
            base=base+(com.filmfoundry.interchange.PhoneMaterialPhenomena.EXPOSURE_OPTION to com.filmfoundry.interchange.PhoneMaterialPhenomena.CURRENT)
        base=base+captureSettings.filterKeys { it=="film_border" }
        captureSettings[com.filmfoundry.interchange.PhoneGrainModels.ROLL_SETTING]?.let {
            require(it==com.filmfoundry.interchange.PhoneGrainModels.LAYERED)
            base=base+(com.filmfoundry.interchange.PhoneGrainModels.EXPOSURE_OPTION to it)
        }
        val model=captureSettings["material_model"]
        if(!com.filmfoundry.interchange.PhoneProcessModels.interactive(model)) return base
        val format=captureSettings["format"]?.toString() ?: "135"
        return base+mapOf(com.filmfoundry.interchange.PhoneProcessModels.OPTION to model,
            "develop" to mapOf("frame_long_edge_mm" to com.filmfoundry.interchange.StillFrameFormat.longEdgeMm.getValue(format)))
    }
}
