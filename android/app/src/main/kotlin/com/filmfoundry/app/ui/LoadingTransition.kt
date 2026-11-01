package com.filmfoundry.app.ui

/** Presentation events only: skipping must never request another roll publication. */
internal class LoadingTransition {
    enum class Stage { THREADING, SAVING, ENTERING, FINISHED, CLOSED }
    enum class Action { NONE, COMMIT, ANIMATE_ENTRY, ENTER }
    var stage=Stage.THREADING
        private set
    private var skipRequested=false
    fun confirm(animate: Boolean): Action {
        if(stage != Stage.THREADING) return Action.NONE
        skipRequested=!animate;stage=Stage.SAVING;return Action.COMMIT
    }
    fun skip(): Action {
        skipRequested=true
        return when(stage) {
            Stage.THREADING -> confirm(false)
            Stage.ENTERING -> finish()
            else -> Action.NONE
        }
    }
    fun committed(animationsEnabled: Boolean): Action {
        if(stage != Stage.SAVING) return Action.NONE
        if(skipRequested || !animationsEnabled) return finish()
        stage=Stage.ENTERING;return Action.ANIMATE_ENTRY
    }
    fun failed(): Boolean {
        if(stage != Stage.SAVING) return false
        stage=Stage.THREADING;return true
    }
    fun entryFinished(): Action=if(stage == Stage.ENTERING) finish() else Action.NONE
    private fun finish(): Action { stage=Stage.FINISHED;return Action.ENTER }
    fun close() { stage=Stage.CLOSED }
}
