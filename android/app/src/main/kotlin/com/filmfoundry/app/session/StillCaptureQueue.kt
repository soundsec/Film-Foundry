package com.filmfoundry.app.session

/** Small shutter-request queue. The active exposure counts against both memory and roll reservations. */
class StillCaptureQueue<T>(private val capacity: Int=3) {
    private val pending=ArrayDeque<T>()
    private var active=false
    init { require(capacity > 0) }
    @Synchronized fun offer(value: T,remainingFrames: Int,mayQueue: Boolean): Boolean {
        val outstanding=pending.size+if(active) 1 else 0
        if(outstanding >= capacity || outstanding >= remainingFrames || (!mayQueue && outstanding > 0)) return false
        pending.addLast(value);return true
    }
    @Synchronized fun take(): T? {
        check(!active)
        return if(pending.isEmpty()) null else pending.removeFirst().also { active=true }
    }
    @Synchronized fun complete() { check(active);active=false }
    @Synchronized fun outstanding(): Int=pending.size+if(active) 1 else 0
    @Synchronized fun waiting(): Int=pending.size
    @Synchronized fun discardWaiting(): Int=pending.size.also { pending.clear() }
}
