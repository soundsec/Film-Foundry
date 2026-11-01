package com.filmfoundry.interchange

import org.json.*

/** Bounded configuration JSON only, never image or medium data. */
object RecipeJson {
    /** JSON numbers have value semantics across encoders that write 1.0 as 1. */
    fun equivalent(a: Any?,b: Any?): Boolean = when {
        a is Number && b is Number -> runCatching { java.math.BigDecimal(a.toString()).compareTo(java.math.BigDecimal(b.toString()))==0 }.getOrDefault(false)
        a is Map<*,*> && b is Map<*,*> -> a.keys==b.keys && a.all { (key,value)->equivalent(value,b[key]) }
        a is List<*> && b is List<*> -> a.size==b.size && a.indices.all { equivalent(a[it],b[it]) }
        else -> a==b
    }
    fun parse(text: String): Any? {
        require(text.toByteArray(Charsets.UTF_8).size <= 262144) { "Configuration is too large" }
        val tokener=JSONTokener(text);val value=tokener.nextValue()
        require(tokener.nextClean() == '\u0000') { "Unexpected trailing JSON" }
        fun convert(item: Any?): Any?=when(item) {
            JSONObject.NULL -> null
            is JSONObject -> item.keys().asSequence().associateWith { convert(item.get(it)) }
            is JSONArray -> (0 until item.length()).map { convert(item.get(it)) }
            is Number -> item.also { require(it.toDouble().isFinite()) }
            null,is String,is Boolean -> item
            else -> error("Unsupported JSON value")
        }
        return convert(value)
    }
    fun objectMap(text: String): Map<String,Any?> {
        val value=parse(text) as? Map<*,*> ?: error("Expected a JSON object")
        return value.entries.associate { it.key.toString() to it.value }
    }
    fun text(value: Any?): String=when(value) {
        null -> "null"
        is Map<*,*> -> JSONObject(value).toString(2)
        is List<*> -> JSONArray(value).toString(2)
        else -> value.toString()
    }
}
