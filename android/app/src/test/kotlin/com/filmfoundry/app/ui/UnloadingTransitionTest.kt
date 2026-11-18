package com.filmfoundry.app.ui

import com.filmfoundry.app.ui.UnloadingTransition.Action.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class UnloadingTransitionTest {
    @Test fun fullExitOpensTheBackThenRemovesTheRollOnce() {
        val flow = UnloadingTransition()
        assertEquals(REVEAL, flow.reveal())
        assertEquals(OPEN, flow.revealed())
        assertEquals(REMOVE, flow.opened())
        assertEquals(LEAVE, flow.removed())
        assertEquals(NONE, flow.removed())
        assertEquals(NONE, flow.skip())
        assertEquals(UnloadingTransition.Stage.FINISHED, flow.stage)
    }

    @Test fun skippingMidwayLeavesOnceAndDropsTheRest() {
        val flow = UnloadingTransition()
        flow.reveal()
        assertEquals(LEAVE, flow.skip())
        assertEquals(NONE, flow.revealed())
        assertEquals(NONE, flow.opened())
        assertEquals(NONE, flow.skip())
        flow.close()
        assertEquals(NONE, flow.reveal())
    }

    @Test fun coveredSceneCannotSkipBeforeTheExitStarts() {
        val flow = UnloadingTransition()
        assertEquals(NONE, flow.skip())
        assertEquals(UnloadingTransition.Stage.COVERED, flow.stage)
    }
}
