package com.filmfoundry.app.ui

/** Dismissing a guide owns the whole touch stream, including release after timeout. */
class FinderCoachState {
    var visible=false;private set
    private var swallowing=false
    private var dragging=false
    fun show() { visible=true }
    fun dismiss() { visible=false }
    fun reset() { visible=false;swallowing=false;dragging=false }
    fun touch(down: Boolean,end: Boolean,moved: Boolean=false,cancelled: Boolean=false): Boolean {
        if(down) { swallowing=visible;dragging=false }
        val consumed=swallowing
        if(consumed && moved) dragging=true
        if(consumed && end && !dragging && !cancelled) dismiss()
        if(end) swallowing=false
        return consumed
    }
}
