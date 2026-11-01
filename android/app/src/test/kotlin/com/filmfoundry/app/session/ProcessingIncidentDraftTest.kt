package com.filmfoundry.app.session

import com.filmfoundry.interchange.RecipeShareCodec
import com.filmfoundry.workflow.RecipeDrafts
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessingIncidentDraftTest {
    @Test fun effectsComposeIndependentlyAndAbsentKeysKeepBathDrivenEffects() {
        val original=mapOf<String,Any?>("time_min" to 8.0,"chemical_stain" to .12,"incident_identity_seed" to 123L)
        val draft=ProcessingIncidentDraft()
        assertEquals(original,draft.applyTo(original));assertFalse(draft.applyTo(original).containsKey("reticulation_strength"))
        draft.set("uneven_development",.25);draft.set("chemical_stain",.5);draft.set("reticulation_strength",.2)
        val all=RecipeDrafts.process(draft.applyTo(original))
        assertEquals(.25,all["uneven_development"]);assertEquals(.5,all["chemical_stain"]);assertEquals(.2,all["reticulation_strength"])
        draft.set("chemical_stain",0.0);assertEquals(.25,draft.applyTo(original)["uneven_development"])
        draft.set("chemical_stain",null);assertEquals(.12,draft.applyTo(original)["chemical_stain"])
        draft.clean();assertTrue(ProcessingIncidentDraft.keys.all { draft.applyTo(original)[it]==0.0 })
        draft.inheritAll();assertEquals(original,draft.applyTo(original))
        assertThrows(IllegalArgumentException::class.java) { draft.set("chemical_stain",Double.NaN) }
        assertThrows(IllegalArgumentException::class.java) { draft.set("other",.5) }
    }
    @Test fun legacySingleChoicePreservesItsExactOldMeaningAndNewCombinationRoundTrips() {
        assertTrue(ProcessingIncidentDraft.legacy(0,35).snapshot().isEmpty())
        for(style in 1..4) {
            val old=ProcessingIncidentDraft.legacy(style,37).snapshot()
            for((i,key) in ProcessingIncidentDraft.keys.withIndex()) assertEquals(if(style==i+2) .37 else 0.0,old[key])
        }
        val draft=ProcessingIncidentDraft.legacy(2,37);draft.set("chemical_stain",.23)
        val encoded=RecipeShareCodec.encode(mapOf("develop" to draft.snapshot()))
        val read=RecipeShareCodec.decode(encoded)["develop"] as Map<*,*>
        val restored=ProcessingIncidentDraft(read.entries.associate { it.key.toString() to it.value })
        assertEquals(draft.snapshot(),restored.snapshot())
        assertEquals(.37,restored.override("uneven_development"));assertEquals(.23,restored.override("chemical_stain"));assertEquals(0.0,restored.override("reticulation_strength"))
    }
    @Test fun continuousDragIsOneUndoStepAndCleanCanBeReversedWithoutLosingOtherEffects() {
        val draft=ProcessingIncidentDraft(mapOf("chemical_stain" to .23))
        draft.beginGesture();for(n in 1..60) draft.set("uneven_development",n/100.0);draft.endGesture()
        assertEquals(.6,draft.override("uneven_development"));assertTrue(draft.canUndo)
        draft.undo();assertFalse(draft.canUndo);assertNull(draft.override("uneven_development"));assertEquals(.23,draft.override("chemical_stain"))
        draft.redo();assertEquals(.6,draft.override("uneven_development"))
        val before=draft.snapshot();draft.clean();draft.undo();assertEquals(before,draft.snapshot())
        draft.set("reticulation_strength",.2);assertFalse(draft.canRedo)
        repeat(100) { draft.set("chemical_stain",it/100.0) }
        var count=0;while(draft.canUndo) { draft.undo();count++ };assertEquals(24,count)
        draft.reset();assertTrue(draft.snapshot().isEmpty());assertFalse(draft.canUndo);assertFalse(draft.canRedo)
    }
}
