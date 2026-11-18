package com.filmfoundry.app.ui

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class FinderCoachStateTest {
    @Test fun dismissingTouchCannotFallThroughToShutterOnRelease() {
        val state=FinderCoachState();state.show()
        assertTrue(state.touch(down=true,end=false));assertTrue(state.visible)
        state.dismiss() // A timeout or back action must not relinquish the active touch stream.
        assertTrue(state.touch(down=false,end=false))
        assertTrue(state.touch(down=false,end=true))
        assertFalse(state.touch(down=true,end=false))
        assertFalse(state.touch(down=false,end=true))
    }
    @Test fun expiryAndLeavingReturnControlWithoutReopening() {
        val state=FinderCoachState();state.show();state.dismiss()
        assertFalse(state.touch(down=true,end=false));assertFalse(state.touch(down=false,end=true))
        state.show();state.touch(down=true,end=false);state.reset()
        assertFalse(state.visible);assertFalse(state.touch(down=false,end=true))
        state.show();assertTrue(state.visible)
    }
    @Test fun scrollingKeepsGuideOpenAndTapAfterwardsClosesWithoutPassingThrough() {
        val state=FinderCoachState();state.show()
        assertTrue(state.touch(down=true,end=false))
        assertTrue(state.touch(down=false,end=false,moved=true))
        assertTrue(state.touch(down=false,end=true));assertTrue(state.visible)
        assertTrue(state.touch(down=true,end=false))
        assertTrue(state.touch(down=false,end=true));assertFalse(state.visible)
        assertFalse(state.touch(down=true,end=false))
        state.show();state.touch(down=true,end=false)
        assertTrue(state.touch(down=false,end=true,cancelled=true));assertTrue(state.visible)
    }
}
