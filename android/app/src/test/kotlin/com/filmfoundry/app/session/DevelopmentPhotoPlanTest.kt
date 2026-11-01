package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeShareCodec
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.util.concurrent.CancellationException

class DevelopmentPhotoPlanTest {
    @Test fun disabledAutomaticScanningDoesNotReadInvalidScannerDrafts() {
        var reads=0
        val plan=DevelopmentPhotoPlan.prepare(false) { reads++;error("invalid scanner field") }
        assertEquals(0,reads);assertFalse(plan.enabled);assertNull(plan.error)
    }
    @Test fun invalidScannerSettingsAreReportedWithoutUsingADifferentRecipe() {
        val plan=DevelopmentPhotoPlan.prepare(true) { throw IllegalArgumentException("Output long edge must be 2–8192 px") }
        assertFalse(plan.enabled);assertNull(plan.recipe);assertNull(plan.share);assertNull(plan.model)
        assertTrue(plan.error!!.contains("2–8192"))
    }
    @Test fun enabledScanningFreezesAnIndependentRecipeAndItsShareCode() {
        val scanner=mutableMapOf<String,Any?>("scan_saturation" to .8)
        val plan=DevelopmentPhotoPlan.prepare(true) { mapOf("scanner" to scanner) }
        scanner["scan_saturation"]=.2
        assertTrue(plan.enabled);assertNull(plan.error)
        assertEquals(.8,(plan.recipe!!["scanner"] as Map<*,*>)["scan_saturation"])
        assertEquals(.8,((RecipeShareCodec.decode(plan.share)["scanner"] as Map<*,*>)["scan_saturation"] as Number).toDouble())
    }
    @Test fun cancellationIsNeverReportedAsAnOptionalScannerFailure() {
        assertThrows(CancellationException::class.java) { DevelopmentPhotoPlan.prepare(true) { throw CancellationException("stop") } }
    }
}
