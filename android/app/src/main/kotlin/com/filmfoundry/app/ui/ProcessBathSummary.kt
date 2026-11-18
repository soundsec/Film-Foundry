package com.filmfoundry.app.ui

/** Presentation merge only. Raw shared input remains visible, including incomplete edits. */
object ProcessBathSummary {
    data class Value(val key: String,val value: Any?,val overridden: Boolean)
    fun resolve(step: Map<String,Any?>,shared: Map<String,Any?>): List<Value> =
        (shared.keys+step.keys.filter { it !in setOf("action","label") }).map { key ->
            Value(key,if(step.containsKey(key)) step[key] else shared[key],step.containsKey(key))
        }
}
