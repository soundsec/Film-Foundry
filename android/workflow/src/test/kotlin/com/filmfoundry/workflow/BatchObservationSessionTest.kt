package com.filmfoundry.workflow

import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class BatchObservationSessionTest {
    private val frames=(1..3).map { DevelopedResult("batch-$it","roll",it,"$it.darkroom_negative.npz",emptyMap()) }
    @Test fun partialCancellationKeepsCompletedScansAndFreezesOneRecipeForTheBatch() = runBlocking {
        var cancel=false;val recipe=mutableMapOf<String,Any?>("scanner" to mutableMapOf("scan_saturation" to .8));val saved=mutableListOf<String>()
        val result=BatchObservationSession.run(frames,recipe,WorkflowControl(cancelRequested={cancel}),{ frame,frozen,_->
            assertEquals(.8,(frozen["scanner"] as Map<*,*>)["scan_saturation"])
            saved.add(frame.resultId);recipe["scanner"]=mapOf("scan_saturation" to 2.0)
            if(saved.size == 2) cancel=true
        })
        assertEquals(2,result.completed);assertTrue(result.cancelled);assertEquals(listOf("batch-1","batch-2"),saved)
        assertTrue(frames.all { it.sourceRollPresent })
        Unit
    }
    @Test fun aFailedFrameReportsEarlierCommittedScansWithoutStartingLaterFrames() {
        val saved=mutableListOf<String>()
        val error=assertThrows(BatchObservationFailure::class.java) { runBlocking {
            BatchObservationSession.run(frames,emptyMap(),WorkflowControl(),{ frame,_,_->
                if(frame.sourceFrameIndex == 2) throw java.io.IOException("disk full")
                saved.add(frame.resultId)
            })
        } }
        assertEquals(1,error.completed);assertEquals(listOf("batch-1"),saved)
    }
}
