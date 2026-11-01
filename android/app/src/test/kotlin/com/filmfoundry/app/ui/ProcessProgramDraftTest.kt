package com.filmfoundry.app.ui

import com.filmfoundry.interchange.ProcessSteps
import com.filmfoundry.interchange.RecipeJson
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessProgramDraftTest {
    @Test fun openingAndBrowsingANamedProgramDoesNotConvertItToCustom() {
        val source=mapOf("program_key" to "color_reversal","developer_type" to "lab_mix","future_option" to mapOf("amount" to .7))
        val draft=ProcessProgramDraft(source);draft.select(2)
        assertFalse(draft.free);assertTrue(RecipeJson.equivalent(source,draft.build(emptyMap())))
        draft.setFree(true,"bw_negative");assertEquals("custom",draft.build(emptyMap())["program_key"])
        draft.setFree(false,"bw_negative");assertTrue(RecipeJson.equivalent(source,draft.build(emptyMap())))
    }
    @Test fun editingAndDiscardingTheWorkbenchCannotMutateTheSourceRecipe() {
        val sourceSteps=ProcessSteps.template("color_negative").map { it.toMutableMap() }.toMutableList()
        val source=mapOf("program_key" to "custom","process_steps" to sourceSteps,"time_min" to 8.0)
        val before=com.filmfoundry.workflow.frozenRecipe(source);val draft=ProcessProgramDraft(source)
        draft.replace(0,draft.steps[0]+mapOf("temperature_c" to 34.0));draft.move(1);draft.remove()
        assertTrue(RecipeJson.equivalent(before,source))
        val reopened=ProcessProgramDraft(source)
        assertTrue(RecipeJson.equivalent(sourceSteps,reopened.steps))
    }
    @Test fun bathSelectionAndReorderingKeepEveryOtherConditionAndOperator() {
        val steps=listOf(mapOf("action" to "develop_color","time_min" to 4,"temperature_c" to 28,"developer_type" to "fine_grain"),mapOf("action" to "bleach_silver","strength" to .3),mapOf("action" to "fix_halide"))
        val draft=ProcessProgramDraft(mapOf("program_key" to "custom","process_steps" to steps))
        draft.select(1);draft.move(-1);draft.replace(0,draft.steps[0]+("label" to "light bleach"))
        val saved=draft.build(emptyMap())["process_steps"] as List<*>
        assertTrue(RecipeJson.equivalent(steps[1]+("label" to "light bleach"),saved[0]))
        assertTrue(RecipeJson.equivalent(steps[0],saved[1]));assertTrue(RecipeJson.equivalent(steps[2],saved[2]))
    }
    @Test fun inheritedConditionsAreValidatedAndClearedFieldsStayInherited() {
        val draft=ProcessProgramDraft(mapOf("program_key" to "custom","time_min" to 8,"agitation_time_s" to 60,
            "process_steps" to listOf(mapOf("action" to "develop_silver","agitation_time_s" to 45))))
        assertThrows(IllegalArgumentException::class.java) { draft.build(mapOf("time_min" to .5,"agitation_time_s" to null)) }
        val valid=draft.build(mapOf("time_min" to 1,"agitation_time_s" to null))
        assertFalse(valid.containsKey("agitation_time_s"));assertEquals(1,valid["time_min"])
        assertEquals(45,((valid["process_steps"] as List<*>).single() as Map<*,*>)["agitation_time_s"])
    }
    @Test fun autoProgramCanBeBrowsedAndOnlyAnActualBathEditCreatesACustomSequence() {
        val film=mapOf("color_process" to "bw","image_polarity" to "positive")
        val source=mapOf("program_key" to "auto","chemistry" to "cross","time_min" to 8.0,"mask_bleach_completion" to .6)
        val inspection=com.filmfoundry.workflow.RecipeDrafts.inspectProcess(film,source)
        assertEquals("bw_negative",inspection.program)
        val draft=ProcessProgramDraft(source,inspection.program);draft.select(1)
        assertFalse(draft.free);assertTrue(RecipeJson.equivalent(source,draft.build(emptyMap())))
        val native=com.filmfoundry.workflow.RecipeDrafts.inspectProcess(film,source+("chemistry" to "native"))
        draft.refreshResolved(native.program);assertEquals("bw_reversal",native.program);assertEquals(6,draft.steps.size)
        assertFalse(draft.free);assertEquals("auto",draft.build(mapOf("chemistry" to "native"))["program_key"])
        draft.refreshResolved(inspection.program);assertEquals(3,draft.steps.size)
        assertFalse(draft.edit(0,draft.steps[0]));assertFalse(draft.free)
        assertTrue(draft.edit(0,draft.steps[0]+("temperature_c" to 28.0)));assertTrue(draft.free)
        val saved=draft.build(emptyMap());assertEquals("custom",saved["program_key"])
        val actual=com.filmfoundry.workflow.RecipeDrafts.inspectProcess(film,saved)
        assertEquals(1,actual.steps.count { it["action"]=="bleach_mask_dye" })
        assertFalse(source.containsKey("process_steps"));assertEquals(.6,saved["mask_bleach_completion"])
    }
}
