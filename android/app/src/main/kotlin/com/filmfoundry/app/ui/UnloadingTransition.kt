package com.filmfoundry.app.ui

/** Exit order after the roll is already sealed. Skipping never reopens the camera. */
internal class UnloadingTransition {
    enum class Stage { COVERED, REVEALING, OPENING, REMOVING, FINISHED, CLOSED }
    enum class Action { NONE, REVEAL, OPEN, REMOVE, LEAVE }
    var stage = Stage.COVERED
        private set

    fun reveal(): Action {
        if (stage != Stage.COVERED) return Action.NONE
        stage = Stage.REVEALING
        return Action.REVEAL
    }

    fun revealed(): Action {
        if (stage != Stage.REVEALING) return Action.NONE
        stage = Stage.OPENING
        return Action.OPEN
    }

    fun opened(): Action {
        if (stage != Stage.OPENING) return Action.NONE
        stage = Stage.REMOVING
        return Action.REMOVE
    }

    fun removed(): Action = if (stage == Stage.REMOVING) leave() else Action.NONE

    fun skip(): Action {
        if (stage == Stage.FINISHED || stage == Stage.CLOSED || stage == Stage.COVERED) return Action.NONE
        return leave()
    }

    fun close() { stage = Stage.CLOSED }

    private fun leave(): Action {
        stage = Stage.FINISHED
        return Action.LEAVE
    }
}
