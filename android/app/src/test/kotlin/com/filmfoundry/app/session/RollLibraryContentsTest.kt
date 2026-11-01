package com.filmfoundry.app.session

import com.filmfoundry.workflow.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Assertions.*

class RollLibraryContentsTest {
    @Test fun everyDevelopmentStaysUnderItsOwnExposureRollAndFrameOrderSurvivesPartialRemoval() {
        fun result(id: String,roll: String,frame: Int)=DevelopedResult(id,roll,frame,"$id.npz",emptyMap(),true)
        val a2=result("batch-a-02","roll-a",2);val a1=result("batch-a-01","roll-a",1)
        val b1=result("batch-b-01","roll-a",1);val foreign=result("batch-a-01","roll-b",1)
        val before=listOf(a2,foreign,b1,a1)
        val groups=RollLibraryContents.batches("roll-a",before)
        assertEquals(listOf("batch-a","batch-b"),groups.map { it.id })
        assertEquals(listOf(a1,a2),groups[0].frames);assertEquals(listOf(b1),groups[1].frames)
        val remaining=RollLibraryContents.batches("roll-a",before-a1)
        assertEquals(listOf(a2),remaining[0].frames);assertEquals(listOf(b1),remaining[1].frames)
        assertEquals(4,before.size);assertTrue(RollLibraryContents.batches("missing",before).isEmpty())
    }
    @Test fun removingExposureDoesNotRemoveIndependentDevelopmentEntries() {
        val library=FilmLibrary();library.loadRoll("roll",12,emptyMap())
        library.commitCameraShot("roll",1,"source.darkroom_latent.npz");library.seal("roll")
        library.recordDevelopedResult("batch-one-01","roll",1,"one.darkroom_negative.npz",emptyMap())
        library.recordDevelopedResult("batch-two-01","roll",1,"two.darkroom_negative.npz",emptyMap())
        library.discardRoll("roll")
        assertEquals(RollLifecycle.DISCARDED,library.allRolls().single().lifecycle)
        val batches=RollLibraryContents.batches("roll",library.developedResults())
        assertEquals(2,batches.size);assertTrue(batches.flatMap { it.frames }.none { it.sourceRollPresent })
    }
}
