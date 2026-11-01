package com.filmfoundry.app.session

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class ProcessingStateTest {
    @Test fun recreatedScreenWaitsForOriginalWorkThenReloadsItsLibraryOnce() {
        val state=ProcessingState();val original=Any();val replacement=Any()
        val id=state.begin(original,"Developing")
        val openedDuringWork=state.snapshot.revision
        assertTrue(state.availableTo(original));assertFalse(state.availableTo(replacement))
        assertFalse(state.needsReload(replacement,openedDuringWork))
        assertThrows(IllegalStateException::class.java) { state.begin(replacement,"Duplicate") }
        assertTrue(state.finish(id,"Finished"))
        assertTrue(state.availableTo(replacement));assertTrue(state.needsReload(replacement,openedDuringWork))
        assertFalse(state.needsReload(original,0))
        assertFalse(state.needsReload(Any(),state.snapshot.revision))
    }
    @Test fun oldCompletionCannotReleaseANewerProcessingLease() {
        val state=ProcessingState();val owner=Any()
        val first=state.begin(owner,"One");assertTrue(state.finish(first,"Done"))
        val second=state.begin(owner,"Two")
        assertFalse(state.finish(first,"Late duplicate"));assertTrue(state.snapshot.active)
        assertEquals(second,state.snapshot.revision);assertEquals("Two",state.snapshot.title)
        assertTrue(state.finish(second,"Paused"));assertFalse(state.finish(second,"Late"))
        assertEquals("Paused",state.snapshot.message)
    }
}
