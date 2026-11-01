package com.filmfoundry.app.session

import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class CaptureIsoLockTest {
    @Test fun temporaryUnlockCannotCrossRollsOrReturnThroughALateCameraRollback() {
        val lock=CaptureIsoLock()
        lock.setUnlocked(true);assertFalse(lock.unlocked)
        lock.selectRoll("a");lock.setUnlocked(true)
        val sameSession=lock.snapshot()
        lock.setUnlocked(false);lock.restore(sameSession);assertTrue(lock.unlocked)
        lock.selectRoll("a");assertTrue(lock.unlocked)
        lock.selectRoll("b");lock.restore(sameSession);assertFalse(lock.unlocked)
        lock.setUnlocked(true);val old=lock.snapshot()
        lock.leave();lock.selectRoll("b");lock.restore(old);assertFalse(lock.unlocked)
        lock.setUnlocked(true);val beforeSettings=lock.snapshot()
        lock.reset();lock.restore(beforeSettings);assertFalse(lock.unlocked)
    }
}
