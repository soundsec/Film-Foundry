package com.filmfoundry.workflow

import com.filmfoundry.interchange.RecipeJson
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessInspectionJourneyTest {
    @Test fun displayedSequenceUsesActualAutomaticCrossResolutionAndIncludesAdditionalBleaching() {
        val film=mapOf("color_process" to "bw","image_polarity" to "positive")
        val develop=mapOf("program_key" to "auto","chemistry" to "cross","mask_bleach_completion" to .6)
        val result=RecipeDrafts.inspectProcess(film,develop)
        assertEquals("bw_negative",result.program)
        assertEquals(listOf("develop_silver","fix_halide","remove_auxiliary","bleach_mask_dye"),result.steps.map { it["action"] })
        assertEquals(.6,result.steps.last()["strength"])
    }
    @Test fun inspectionKeepsCustomOrderAndGeneratedEffectsOutOfTheSourceRecipe() {
        val source=mapOf("program_key" to "custom","process_steps" to listOf(mapOf("action" to "develop_color"),mapOf("action" to "fix_halide")),
            "mask_bleach_completion" to .5,"dye_damage_strength" to .3)
        val before=frozenRecipe(source);val film=mapOf("color_process" to "color","image_polarity" to "negative")
        val result=RecipeDrafts.inspectProcess(film,source)
        assertEquals(listOf("develop_color","destroy_dye","fix_halide","bleach_mask_dye"),result.steps.map { it["action"] })
        assertTrue(RecipeJson.equivalent(before,source));assertEquals(2,(source["process_steps"] as List<*>).size)
        assertEquals(result.steps,RecipeDrafts.inspectProcess(film,source).steps)
    }
}
