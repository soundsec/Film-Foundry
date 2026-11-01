package com.filmfoundry.kernel

import java.util.Collections

/** Deeply detached JSON values for task setup. No mutable maps from the caller escape into a plan. */
internal fun preparedValues(values: Map<String, Any?>): Map<String, Any?> {
    fun freeze(value: Any?): Any? = when (value) {
        is Map<*, *> -> Collections.unmodifiableMap(value.entries.associate { it.key to freeze(it.value) })
        is List<*> -> Collections.unmodifiableList(value.map { freeze(it) })
        null, is String, is Number, is Boolean -> value
        else -> throw KernelException("prepared metadata must contain JSON values, not image or platform objects.")
    }
    @Suppress("UNCHECKED_CAST")
    return freeze(values) as Map<String, Any?>
}
