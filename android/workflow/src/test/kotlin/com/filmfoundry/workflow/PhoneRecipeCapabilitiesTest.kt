package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneRecipeCapabilitiesTest {
    @Test fun frozenLightweightContextReportsOmissionsWithoutRewritingParameters() {
        val r=mapOf("film" to mapOf("dir_material_response_matrix" to List(3) { List(3) { .15 } }),
            "develop" to mapOf("development_adjacency_strength" to .3))
        val before=RecipeShareCodec.encode(r)
        val fast=PhoneRecipeCapabilities.inspect(r,PhoneRecipeCapabilities.Stage.DEVELOP,PhoneFormationProfile.LIGHTWEIGHT)
        assertEquals(setOf("film.dir_material_response_matrix","develop.development_adjacency_strength"),
            fast.warnings.filter { it.status==PhoneRecipeCapabilities.Status.OMITTED }.map { it.path }.toSet())
        assertFalse(PhoneRecipeCapabilities.inspect(r,PhoneRecipeCapabilities.Stage.DEVELOP).entries.any { it.status==PhoneRecipeCapabilities.Status.OMITTED })
        assertTrue(PhoneRecipeCapabilities.inspect(r,PhoneRecipeCapabilities.Stage.SCAN,PhoneFormationProfile.LIGHTWEIGHT).warnings.isEmpty())
        assertEquals(before,RecipeShareCodec.encode(r))
    }
    @Test fun newDesktopStateModelsArePreservedAndReportedOnlyForRelevantStages() {
        val recipe=mapOf("exposure" to mapOf("duration_seconds" to 12.5),
            "film" to mapOf("time_response" to mapOf("model" to "film_time_response_v2"),"carbon_backing" to mapOf("model" to "carbon_backing_v2")),
            "develop" to mapOf("process_events" to mapOf("model" to "film_process_events_v15"),
                "formation_history_model" to "bath_products_local_v2","latent_wait" to mapOf("duration_seconds" to 900)))
        val before=RecipeShareCodec.encode(recipe)
        val capture=PhoneRecipeCapabilities.inspect(recipe,PhoneRecipeCapabilities.Stage.CAPTURE)
        assertTrue(capture.warnings.any { it.path=="exposure.duration_seconds" && it.status==PhoneRecipeCapabilities.Status.PRESERVED })
        val develop=PhoneRecipeCapabilities.inspect(recipe,PhoneRecipeCapabilities.Stage.DEVELOP)
        for(key in listOf("process_events","formation_history_model","latent_wait"))
            assertTrue(develop.warnings.any { it.path=="develop.$key" && it.status==PhoneRecipeCapabilities.Status.PRESERVED })
        assertTrue(PhoneRecipeCapabilities.inspect(recipe,PhoneRecipeCapabilities.Stage.SCAN).warnings.isEmpty())
        assertEquals(before,RecipeShareCodec.encode(recipe))
    }
    @Test fun desktopOnlyBathFieldsAreNotMisreportedAsAnExecutableReducedProcess() {
        val recipe=mapOf("develop" to mapOf("program_key" to "custom","process_steps" to listOf(
            mapOf("action" to "develop_silver","solution" to mapOf("model" to "bath_solution_v3")))))
        assertTrue(PhoneRecipeCapabilities.inspect(recipe,PhoneRecipeCapabilities.Stage.DEVELOP).warnings.any {
            it.path=="develop.process_steps" && it.status==PhoneRecipeCapabilities.Status.UNSUPPORTED
        })
    }
    @Test fun newRollExposureUsesAnIndependentFrozenPhenomenaSelectionAndEditorsValidateIt() {
        val film=mapOf("light_piping_strength" to .3,"light_piping_edge_mode" to "left","silver_grain_strength" to .1,"emulsion_highlight_spread_strength" to .5)
        val old=mapOf("spatial_effects_revision" to PhoneMaterialDefaults.REVISION,"material_model" to PhoneProcessModels.CURRENT)
        assertNull(PhoneMaterialDefaults.exposureRecipe(film,old)[PhoneMaterialPhenomena.EXPOSURE_OPTION])
        val modern=old+(PhoneMaterialPhenomena.ROLL_SETTING to PhoneMaterialPhenomena.CURRENT)
        assertEquals(PhoneMaterialPhenomena.CURRENT,PhoneMaterialDefaults.exposureRecipe(film,modern)[PhoneMaterialPhenomena.EXPOSURE_OPTION])
        RecipeDrafts.material(film)
        assertThrows(IllegalArgumentException::class.java) { RecipeDrafts.material(film+("silver_grain_clump_mix" to 2)) }
        val report=PhoneRecipeCapabilities.inspect(mapOf("film" to film),PhoneRecipeCapabilities.Stage.DEVELOP)
        for(key in listOf("light_piping_strength","silver_grain_strength","emulsion_highlight_spread_strength"))
            assertTrue(report.entries.any { it.path=="film.$key" && it.status==PhoneRecipeCapabilities.Status.REDUCED })
    }
    @Test fun activeUnsupportedFeaturesAreReportedWithoutChangingTheRecipe() {
        val recipe=mapOf("film" to mapOf("dir_material_response_matrix" to List(3) { List(3) { .1 } },"halation_strength" to .2,"halation_return_model" to "spectral_layers"),
            "develop" to mapOf("development_adjacency_strength" to .4),
            "scanner" to mapOf("device_response_rgb" to List(3) { listOf(listOf(0,0),listOf(1,.5)) },"negative_channel_gamma" to listOf(1.2,1,1)))
        val before=RecipeShareCodec.encode(recipe)
        val process=PhoneRecipeCapabilities.inspect(recipe,PhoneRecipeCapabilities.Stage.DEVELOP)
        assertTrue(process.entries.any { it.path=="film.dir_material_response_matrix" && it.status==PhoneRecipeCapabilities.Status.REDUCED })
        assertTrue(process.entries.any { it.path=="develop.development_adjacency_strength" && it.status==PhoneRecipeCapabilities.Status.REDUCED })
        assertTrue(process.warnings.any { it.status==PhoneRecipeCapabilities.Status.UNSUPPORTED })
        val scan=PhoneRecipeCapabilities.inspect(recipe,PhoneRecipeCapabilities.Stage.SCAN)
        assertEquals(listOf("scanner.negative_channel_gamma"),scan.warnings.map { it.path })
        assertTrue(scan.entries.any { it.path=="scanner.device_response_rgb" && it.status==PhoneRecipeCapabilities.Status.IMPLEMENTED })
        assertEquals(before,RecipeShareCodec.encode(recipe))
        val neutral=mapOf("scanner" to mapOf("negative_channel_gamma" to listOf(1,1,1),"negative_channel_matrix" to List(3) { y->List(3) { x->if(x==y) 1 else 0 } }))
        assertTrue(PhoneRecipeCapabilities.inspect(neutral,PhoneRecipeCapabilities.Stage.SCAN).warnings.isEmpty())
    }
    @Test fun captureValidationUsesTheRealApertureAndPreservesThePreviousExposureModel() {
        val film=mapOf<String,Any?>("spatial_reference_short_edge_mm" to 24.0,"hd_gamma" to listOf(.62,.62,.62),"emulsion_mtf_strength" to .5,"emulsion_blur_radius" to .05)
        val settings=mapOf("format" to "135","gate_revision" to StillFrameFormat.MODEL,"material_model" to PhoneProcessModels.PREVIOUS,"spatial_effects_revision" to PhoneMaterialDefaults.REVISION)
        assertEquals(PhoneProcessModels.PREVIOUS,PhoneMaterialDefaults.exposureRecipe(film,settings)[PhoneProcessModels.OPTION])
        RecipeDrafts.validateCaptureSize(film,settings,12,9)
        assertThrows(IllegalArgumentException::class.java) { RecipeDrafts.validateCaptureSize(film,settings,4000,3000) }
        RecipeDrafts.validateCaptureSize(film+("emulsion_blur_radius" to .0012),settings,4000,3000)
    }
}
