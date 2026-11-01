package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanPreviewStateTest {
    @Test fun backgroundKeepsTheCompletedReceiptButRejectsItsOldCompletionToken() {
        val state=ScanPreviewState();val shown=state.begin("a","a.npz",mapOf("scanner" to mapOf("scan_saturation" to 1.4)))
        assertTrue(state.complete(shown));state.cancelPendingKeepingDisplayed()
        assertFalse(state.complete(shown))
        assertEquals(1.4,(state.recipeFor("a","a.npz")!!["scanner"] as Map<*,*>)["scan_saturation"])
        state.cancelPendingKeepingDisplayed();assertNotNull(state.recipeFor("a","a.npz"))
        state.invalidate();state.cancelPendingKeepingDisplayed();assertNull(state.recipeFor("a","a.npz"))
    }
    @Test fun backgroundCannotResurrectAnOlderDisplayWhenAnUpdateWasPending() {
        val state=ScanPreviewState();val shown=state.begin("a","a.npz",emptyMap());state.complete(shown)
        val pending=state.begin("a","a.npz",mapOf("look" to mapOf("print_exposure_ev" to 1.0)))
        state.cancelPendingKeepingDisplayed()
        assertFalse(state.complete(pending));assertNull(state.recipeFor("a","a.npz"))
        val resumed=state.begin("b","b.npz",emptyMap());assertTrue(state.complete(resumed))
        assertNull(state.recipeFor("a","a.npz"));assertNotNull(state.recipeFor("b","b.npz"))
    }
    @Test fun refreshDisablesSavingUntilTheCurrentRequestActuallyCompletes() {
        val state=ScanPreviewState()
        val first=state.begin("frame","film.npz",mapOf("look" to mapOf("print_exposure_ev" to 0.0)))
        assertNull(state.recipeFor("frame","film.npz"));assertTrue(state.complete(first))
        val retry=state.begin("frame","film.npz",mapOf("look" to mapOf("print_exposure_ev" to 1.0)))
        assertNull(state.recipeFor("frame","film.npz")) // A failed retry must not unlock the old receipt.
        assertFalse(state.complete(first));assertTrue(state.complete(retry))
        assertEquals(1.0,(state.recipeFor("frame","film.npz")!!["look"] as Map<*,*>)["print_exposure_ev"])
    }
    @Test fun editsOrSourceChangesRejectLatePreviewCompletion() {
        val state=ScanPreviewState();val old=state.begin("a","a.npz",emptyMap())
        state.invalidate();assertFalse(state.complete(old))
        val next=state.begin("b","b.npz",emptyMap());assertTrue(state.complete(next))
        assertNull(state.recipeFor("a","a.npz"));assertNull(state.recipeFor("b","replaced.npz"))
        assertNotNull(state.recipeFor("b","b.npz"))
    }
    @Test fun savingKeepsTheDisplayedRecipeEvenIfDraftInputsLaterChange() {
        val scanner=mutableMapOf<String,Any?>("scan_saturation" to 1.25)
        val draft=mutableMapOf<String,Any?>("scanner" to scanner)
        val state=ScanPreviewState();val request=state.begin("frame","film.npz",draft)
        scanner["scan_saturation"]=0.2;draft["look"]=mapOf("print_exposure_ev" to -1.0)
        assertTrue(state.complete(request))
        val saved=state.recipeFor("frame","film.npz")!!
        assertEquals(1.25,(saved["scanner"] as Map<*,*>)["scan_saturation"]);assertFalse(saved.containsKey("look"))
    }
}
