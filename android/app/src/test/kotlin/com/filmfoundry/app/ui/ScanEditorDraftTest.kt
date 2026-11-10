package com.filmfoundry.app.ui

import com.filmfoundry.interchange.CurvePoint
import com.filmfoundry.interchange.RecipeJson
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ScanEditorDraftTest {
    @Test fun automaticFinishKeepsVisibleColourAndDeliveryAndCanBeUndone() {
        val color=mapOf("scan_saturation" to 1.7,"tone_curve" to listOf(listOf(0.0,0.0),listOf(.5,.6),listOf(1.0,1.0)))
        val before=ScanEditorDraft(style=0,gain=47,manual=true,numbers=mapOf("scan_white_percentile" to "90","print_contrast" to "1.2"),
            baseMode=2,inversionMode=1,grid="1920",rotationDegrees=90,outputFormat="JPG",outputBits=8,jpegQuality=70)
        val after=before.withAutomaticFinish(color+mapOf("scan_normalize" to false,"scan_white_percentile" to 90,"invert_transmission" to true))
        assertEquals(4,after.style);assertEquals(color,after.overlay);assertEquals(0,after.inversionMode);assertEquals(0,after.baseMode)
        assertEquals(mapOf("print_contrast" to "1.2"),after.numbers);assertTrue(after.normalize)
        assertEquals(before.gain,after.gain);assertEquals(before.grid,after.grid);assertEquals(90,after.rotationDegrees);assertEquals(70,after.jpegQuality)
        val history=ScanEditHistory(before);history.change(after);assertEquals(before,history.undo());assertEquals(after,history.redo())
    }
    @Test fun mixerSurvivesPhotoRestoreUndoRedoAndCancel() {
        val rows=List(8) { if(it==0) listOf(15,20,-10) else listOf(0,0,0) }
        val initial=ScanEditorDraft.saved(mapOf("scanner" to mapOf("color_mixer" to rows)),"Photo",true)
        val history=ScanEditHistory(initial)
        val edited=initial.copy(overlay=initial.overlay+("color_mixer" to List(8){listOf(0,0,0)}))
        history.begin();history.change(edited);history.commit()
        assertEquals(initial,history.undo());assertEquals(edited.detached(),history.redo())
        history.begin();history.change(initial);history.cancel()
        assertEquals(edited.detached(),history.current)
        assertTrue((initial.base?.get("scanner") as Map<*,*>).containsKey("color_mixer"))
    }
    @Test fun outputDirectionSurvivesPhotoRestoreHistoryAndCancelledStyleAuditions() {
        val recipe=com.filmfoundry.platform.OutputOrientation.apply(mapOf("scanner" to mapOf("scan_saturation" to 1.4)),270)
        val initial=ScanEditorDraft.saved(recipe,"Photo",true)
        assertEquals(270,initial.rotationDegrees)
        val history=ScanEditHistory(initial);history.change(initial.copy(rotationDegrees=0))
        assertEquals(270,history.undo().rotationDegrees);assertEquals(0,history.redo().rotationDegrees)
        history.begin();history.change(history.current.copy(style=2));history.cancel()
        assertEquals(0,history.current.rotationDegrees)
        assertEquals(270,initial.withSavedScanner("custom").rotationDegrees)
    }
    @Test fun continuingAPhotoStartsWithItsOwnFrozenSettings() {
        val scanner=linkedMapOf<String,Any?>("scan_saturation" to 1.35,"scan_grid_long_edge" to 1280,
            "remove_base_mask" to false,"invert_transmission" to true,"tone_curve" to listOf(listOf(0,0),listOf(.5,.6),listOf(1,1)))
        val recipe=mapOf("scanner" to scanner,"look" to mapOf("print_exposure_ev" to .65))
        val draft=ScanEditorDraft.saved(recipe,"Saved",false)
        assertTrue(RecipeJson.equivalent(recipe,draft.base));assertEquals(3,draft.style)
        assertEquals(2,draft.baseMode);assertEquals(1,draft.inversionMode);assertEquals("1280",draft.grid)
        assertFalse(draft.autoPreview);assertNull(draft.gain)
        scanner["scan_saturation"]=.2
        assertEquals(1.35,(draft.base!!["scanner"] as Map<*,*>)["scan_saturation"])
    }
    @Test fun aContinuousGestureAndAGroupResetAreEachOneStep() {
        val initial=ScanEditorDraft(baseMode=1,inversionMode=2,grid="1920")
        val history=ScanEditHistory(initial)
        history.begin();for(value in 41..57) history.change(initial.copy(gain=value));history.commit()
        val edited=history.current
        history.begin();history.change(edited.copy(style=2,gain=null,overlay=mapOf("scan_saturation" to 1.7)));history.commit()
        assertEquals(edited,history.undo());assertEquals(initial,history.undo())
        assertEquals(edited,history.redo());assertEquals(2,history.redo().style)
        assertEquals(1,history.current.baseMode);assertEquals("1920",history.current.grid)
    }
    @Test fun cancelledStyleAuditionsRestoreTheDraftAndPreserveRedo() {
        val initial=ScanEditorDraft();val history=ScanEditHistory(initial,2)
        history.change(initial.copy(gain=45));history.change(initial.copy(gain=55));history.undo()
        val before=history.current
        history.begin();history.change(before.copy(style=1));history.change(before.copy(style=2))
        assertEquals(before,history.cancel());assertTrue(history.canRedo);assertEquals(55,history.redo().gain)
        history.change(initial.copy(gain=60));history.change(initial.copy(gain=65))
        assertEquals(60,history.undo().gain);assertEquals(55,history.undo().gain);assertFalse(history.canUndo)
    }
    @Test fun sourceFilmAndSavedVersionsHaveSeparateSessionsWhileRefreshKeepsTheCurrentOne() {
        val sessions=ScanEditorSessions(2)
        val film=ScanEditorSessions.key("result","medium")
        val photo=ScanEditorSessions.key("result","medium","photo")
        assertNotEquals(film,photo);assertEquals(photo,ScanEditorSessions.key("result","medium",resume=photo))
        sessions.open(film) { ScanEditorDraft() }.change(ScanEditorDraft(gain=45))
        sessions.open(photo) { ScanEditorDraft(gain=51) }.change(ScanEditorDraft(gain=59))
        assertEquals(45,sessions.open(film) { error("must keep film draft") }.current.gain)
        sessions.open("other") { ScanEditorDraft() }
        assertEquals(12,sessions.open(photo) { ScanEditorDraft(gain=12) }.current.gain)
    }
    @Test fun historyKeepsDisabledCurvesWithoutRetainingMutableInputLists() {
        val points=mutableListOf(CurvePoint(0.0,0.0),CurvePoint(.5,.7),CurvePoint(1.0,1.0))
        val draft=ScanEditorDraft(curveDraft=ScannerCurveState.Draft(List(4) { points },false,false))
        val history=ScanEditHistory(draft)
        points.clear()
        assertEquals(3,history.current.curveDraft!!.curves[0].size)
        history.change(history.current.copy(gain=42));history.undo()
        assertFalse(history.current.curveDraft!!.toneEnabled)
        assertEquals(.7,history.current.curveDraft!!.curves[0][1].y)
    }
    @Test fun savingAScannerKeepsAdjustmentsAndInterpretationInsteadOfReapplyingAFinish() {
        val points=listOf(CurvePoint(0.0,0.0),CurvePoint(.5,.7),CurvePoint(1.0,1.0))
        val source=ScanEditorDraft(style=0,gain=53,manual=true,normalize=false,
            overlay=mapOf("scan_saturation" to 1.73,"tone_curve" to null),
            numbers=mapOf("print_contrast" to "1.35","transmission_light_ev" to "-0.5"),
            baseMode=2,inversionMode=1,grid="3840",autoPreview=false,
            curveDraft=ScannerCurveState.Draft(List(4) { points },false,false))
        val selected=source.withSavedScanner("new-style")
        assertEquals(3,selected.style);assertEquals("saved:new-style",selected.scannerKey)
        assertEquals(source,selected.copy(style=source.style,scannerKey=source.scannerKey))
        assertFalse(selected.curveDraft!!.toneEnabled);assertEquals(.7,selected.curveDraft.curves[0][1].y)
        val history=ScanEditHistory(source)
        history.change(selected);assertEquals(source,history.undo());assertEquals(selected,history.redo())
    }
}
