package com.filmfoundry.app.session

import com.filmfoundry.workflow.FilmLibrary
import com.filmfoundry.workflow.frozenRecipe
import java.util.concurrent.atomic.AtomicReference

/** A small loading draft, detached from configuration editors. No exposures are consumed here. */
class RollLoadingSession(
    private val library: FilmLibrary,
    private val id: String,
    private val capacity: Int,
    film: Map<String,Any?>,
    private val label: String,
    develop: Map<String,Any?>,
    scanner: Map<String,Any?>,
    settings: Map<String,Any?>,
) {
    enum class State { READY, COMMITTING, LOADED, CANCELLED }
    private val phase=AtomicReference(State.READY)
    private val film=frozenRecipe(film)
    private val develop=frozenRecipe(develop)
    private val scanner=frozenRecipe(scanner)
    private val settings=frozenRecipe(settings)
    val state: State get()=phase.get()

    fun commit(): String {
        if(phase.get() == State.LOADED) return id
        check(phase.compareAndSet(State.READY,State.COMMITTING)) { "Loading is already active or cancelled" }
        try {
            library.loadRoll(id,capacity,film,label,develop,scanner,settings+("created_at" to (settings["created_at"] ?: java.time.Instant.now().toString())))
            phase.set(State.LOADED)
            return id
        } catch(error: Exception) {
            phase.set(State.READY)
            throw error
        }
    }

    // Never block the UI on index I/O. Once publication starts, a completed roll is retained.
    fun cancel() { phase.compareAndSet(State.READY,State.CANCELLED) }
}
