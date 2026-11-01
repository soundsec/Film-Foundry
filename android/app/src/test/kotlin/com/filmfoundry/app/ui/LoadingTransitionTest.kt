package com.filmfoundry.app.ui

import com.filmfoundry.app.ui.LoadingTransition.Action.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class LoadingTransitionTest {
    @Test fun skippingBeforeThreadingCommitsOnceAndEntersOnlyAfterPublication() {
        val flow=LoadingTransition()
        assertEquals(COMMIT,flow.skip());assertEquals(NONE,flow.skip());assertEquals(NONE,flow.confirm(true))
        assertEquals(LoadingTransition.Stage.SAVING,flow.stage)
        assertEquals(ENTER,flow.committed(true));assertEquals(NONE,flow.committed(true));assertEquals(NONE,flow.entryFinished())
    }
    @Test fun skippingDuringSavingWaitsForTheOriginalCommitWithoutAnotherRequest() {
        val flow=LoadingTransition()
        assertEquals(COMMIT,flow.confirm(true));assertEquals(NONE,flow.skip());assertEquals(NONE,flow.skip())
        assertEquals(LoadingTransition.Stage.SAVING,flow.stage)
        assertEquals(ENTER,flow.committed(true));assertEquals(NONE,flow.skip())
    }
    @Test fun skippingTheViewfinderEntersOnceAndLateAnimationCallbacksAreIgnored() {
        val flow=LoadingTransition()
        flow.confirm(true);assertEquals(ANIMATE_ENTRY,flow.committed(true))
        assertEquals(ENTER,flow.skip());assertEquals(NONE,flow.entryFinished());assertEquals(NONE,flow.committed(true))
        flow.close();assertEquals(NONE,flow.skip());assertEquals(NONE,flow.confirm(false))
    }
    @Test fun failedPublicationCanRetryAndDisabledAnimationsOrLeavingNeverForceAnEntry() {
        val flow=LoadingTransition()
        flow.skip();assertTrue(flow.failed());assertEquals(COMMIT,flow.confirm(true))
        assertEquals(ENTER,flow.committed(false))
        val left=LoadingTransition();left.confirm(true);left.close()
        assertEquals(NONE,left.committed(true));assertEquals(NONE,left.entryFinished());assertFalse(left.failed())
    }
}
