package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*

/** Validate independent configuration with the actual phone backend, never a loaded roll or medium. */
object RecipeDrafts {
    data class ProcessInspection(val program: String,val colorMaterial: Boolean,val positive: Boolean,val components: List<String>,val variableImage: Boolean,
        val steps: List<Map<String,Any?>> = emptyList())
    fun inspectProcess(film: Map<String,Any?>,develop: Map<String,Any?>)=com.filmfoundry.kernel.inspectPhoneProcess(film,process(develop)).let {
        ProcessInspection(it.program,it.colorMaterial,it.positive,it.components,it.variableImage,it.steps.map(::frozenRecipe))
    }
    /** Parameter preparation only; no decoding or image-sized allocations. */
    fun validateCaptureSize(film: Map<String,Any?>,settings: Map<String,Any?>,width: Int,height: Int) {
        val aperture=StillFrameFormat.create(settings["format"]?.toString() ?: "legacy",false,width,height,
            settings["gate_revision"]?.toString() ?: StillFrameFormat.LEGACY_MODEL)
        PhoneReducedKernel().prepareExposure(PhoneMaterialDefaults.exposureRecipe(film,settings),aperture.width,aperture.height)
    }
    fun filmFromRecipe(recipe: Map<String,Any?>): Map<String,Any?> {
        val film=(recipe["film"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
        val process=film["color_process"]?.toString()?.trim()?.lowercase()
        return if(recipe["enable_subtractive"] == false && process !in listOf("bw","black_white","monochrome")) film+("color_process" to "bw") else film
    }
    fun material(film: Map<String,Any?>): Map<String,Any?> {
        ProcessSteps.validateMaterial(film)
        validatePhoneDevelopmentSpatial(film=film)
        fun vector(raw: Any?,count: Int) {
            val values=raw as? List<*> ?: error("Expected numeric array");require(values.size == count)
            require(values.all { it is Number && it.toDouble().isFinite() && it.toFloat().isFinite() })
        }
        for(key in listOf("film_base_density_rgb","retained_halide_density_rgb","clear_support_density_rgb","auxiliary_layer_density_rgb")) film[key]?.let { vector(it,3) }
        for(key in listOf("layer_sensitivity_matrix","dye_absorption_matrix")) film[key]?.let {
            val rows=it as? List<*> ?: error("Expected 3×3 matrix");require(rows.size == 3);rows.forEach { row->vector(row,3) }
        }
        val result=frozenRecipe(RecipeShareCodec.decode(RecipeShareCodec.encode(mapOf("film" to film))))["film"] as Map<*,*>
        val payload=mapOf("film" to result,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,"develop" to mapOf("program_key" to "auto"),
            PhoneMaterialPhenomena.EXPOSURE_OPTION to PhoneMaterialPhenomena.CURRENT,PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val kernel=PhoneReducedKernel()
        val latent=kernel.expose(RgbVolume(1,1,linearRgb=floatArrayOf(.18f,.18f,.18f)),payload)
        val optical=kernel.develop(latent,payload)
        require(optical.opticalDensityRgb.values.all(Float::isFinite)) { "Material produces invalid density" }
        return result.entries.associate { it.key.toString() to it.value }
    }
    fun scanner(scanner: Map<String,Any?>): Map<String,Any?> {
        val result=frozenRecipe(RecipeShareCodec.decode(RecipeShareCodec.encode(mapOf("scanner" to scanner))))["scanner"] as Map<*,*>
        val value=result.entries.associate { it.key.toString() to it.value }
        value["scan_grid_long_edge"]?.let {
            val n=it as? Number ?: error("Output grid must be an integer")
            require(n.toDouble() == n.toInt().toDouble() && n.toInt() in 2..8192)
        }
        value[SpectralOptics.OBSERVER_KEY]?.let { SpectralOptics.observer(it) }
        val optical=OpticalMaster(FloatNd(intArrayOf(1,2,3),floatArrayOf(.2f,.5f,.7f,.6f,.4f,.3f)),emptyMap())
        // Validation is independent of the chosen output size or an actual spectral material.
        val observed=PhoneReducedKernel().observe(optical,mapOf("scanner" to (value-"scan_grid_long_edge"-SpectralOptics.OBSERVER_KEY)))
        require(observed.outputRgb.values.all(Float::isFinite)) { "Scanner produces invalid output" }
        return frozenRecipe(value)
    }
    fun process(recipe: Map<String,Any?>): Map<String,Any?> {
        val value=frozenRecipe(recipe)
        ProcessSteps.validate(value)
        validatePhoneDevelopmentSpatial(develop=value)
        value["time_min"]?.let { require(it is Number && it.toDouble().isFinite() && it.toDouble() in 0.1..120.0) }
        require(!value.containsKey("chemistry") || value["chemistry"] in listOf("native","cross"))
        return value
    }
}
