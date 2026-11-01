package com.filmfoundry.workflow

import java.util.Collections

/** Detach only small JSON recipe values; no pixel data or platform objects belong here. */
fun frozenRecipe(value: Map<String,Any?>): Map<String,Any?> {
    fun freeze(item: Any?): Any? = when(item) {
        is Map<*,*> -> {
            require(item.keys.all { it is String }) { "Recipe keys must be strings" }
            Collections.unmodifiableMap(item.entries.associate { it.key as String to freeze(it.value) })
        }
        is List<*> -> Collections.unmodifiableList(item.map { freeze(it) })
        is Number -> item.also { require(it.toDouble().isFinite()) { "Recipe numbers must be finite" } }
        null, is String, is Boolean -> item
        else -> throw WorkflowException("invalid_request","Recipe values must be JSON values")
    }
    @Suppress("UNCHECKED_CAST")
    return freeze(value) as Map<String,Any?>
}
