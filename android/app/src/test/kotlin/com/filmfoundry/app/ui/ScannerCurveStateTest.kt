package com.filmfoundry.app.ui

import com.filmfoundry.interchange.PiecewiseCurve
import com.filmfoundry.interchange.RecipeJson
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScannerCurveStateTest {
    @Test fun switchingDotsKeepsToneAndAllEditedRgbChannels() {
        val state=ScannerCurveState()
        val tone=PiecewiseCurve.identity().insert(.5,.65);state.edit(tone)
        state.select(1);val red=PiecewiseCurve.identity().insert(.5,.35);state.edit(red)
        state.select(2);val green=PiecewiseCurve.identity().insert(.4,.45);state.edit(green)
        state.select(0);assertTrue(RecipeJson.equivalent(tone.payload(),state.curve.payload()))
        val payload=state.payload();val rgb=payload["color_curve_rgb"] as List<*>
        assertTrue(RecipeJson.equivalent(red.payload(),rgb[0]));assertTrue(RecipeJson.equivalent(green.payload(),rgb[1]))
        assertTrue(RecipeJson.equivalent(PiecewiseCurve.identity().payload(),rgb[2]))
    }
    @Test fun liveEchoAndDisablingDoNotDiscardTheCurveBeingEdited() {
        val state=ScannerCurveState();state.select(3);val blue=PiecewiseCurve.identity().insert(.3,.2);state.edit(blue)
        assertFalse(state.bind(state.payload()));assertEquals(3,state.channel)
        state.enable(false);assertFalse(state.bind(state.payload()));state.enable(true)
        assertTrue(RecipeJson.equivalent(blue.payload(),state.curve.payload()))
        state.enable(false);state.bind(emptyMap(),force=true);state.enable(true)
        assertTrue(RecipeJson.equivalent(PiecewiseCurve.identity().payload(),state.curve.payload()))
    }
    @Test fun selectingADotIsReadOnlyAndInvalidExternalCurvesCannotPartlyReplaceTheDraft() {
        val source=mapOf("tone_curve" to listOf(listOf(0,0),listOf(.5,.6),listOf(1,1)))
        val state=ScannerCurveState();state.bind(source);state.select(1);assertFalse(state.enabled)
        assertNull(state.payload()["color_curve_rgb"])
        val before=state.payload()
        assertThrows(IllegalArgumentException::class.java) { state.bind(mapOf("color_curve_rgb" to listOf(emptyList<Any>()))) }
        assertTrue(RecipeJson.equivalent(before,state.payload()));assertEquals(3,(source["tone_curve"] as List<*>).size)
    }
    @Test fun aNewPhotoWithoutACurveDraftCannotInheritAnotherPhotosDisabledPoints() {
        val state=ScannerCurveState();state.edit(PiecewiseCurve.identity().insert(.5,.8));state.enable(false)
        val saved=ScanEditorDraft.saved(mapOf("scanner" to emptyMap<String,Any?>()),"B",true)
        state.restore(emptyMap(),saved.curveDraft)
        assertFalse(state.enabled);state.enable(true)
        assertTrue(RecipeJson.equivalent(PiecewiseCurve.identity().payload(),state.payload()["tone_curve"]))
    }
    @Test fun returningToASessionKeepsItsDisabledPointsWhileExplicitResetUsesThePreset() {
        val state=ScannerCurveState();val edited=PiecewiseCurve.identity().insert(.5,.8)
        state.edit(edited);state.enable(false);val session=state.snapshot()
        state.restore(emptyMap(),null);state.restore(emptyMap(),session)
        assertFalse(state.enabled);state.enable(true)
        assertTrue(RecipeJson.equivalent(edited.payload(),state.payload()["tone_curve"]))
        val preset=mapOf("tone_curve" to listOf(listOf(0,0),listOf(.4,.3),listOf(1,1)))
        state.restore(preset,null)
        assertTrue(state.enabled);assertEquals(.4,state.curve.points[1].x)
        assertEquals(.3,state.curve.points[1].y)
        state.enable(false);state.restore(emptyMap(),null);state.enable(true)
        assertTrue(RecipeJson.equivalent(PiecewiseCurve.identity().payload(),state.payload()["tone_curve"]))
    }
}
