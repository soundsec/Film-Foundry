package com.filmfoundry.app.session

/** Editing overrides only. Absent fields keep the recipe and bath-derived effects authoritative. */
class ProcessingIncidentDraft(initial: Map<String,Any?> = emptyMap()) {
    companion object {
        val keys=listOf("uneven_development","chemical_stain","reticulation_strength")
        fun legacy(style: Int,strength: Int): ProcessingIncidentDraft {
            val values=if(style in 1..4) keys.associateWith { 0.0 }.toMutableMap() else mutableMapOf()
            if(style in 2..4) values[keys[style-2]]=strength.coerceIn(0,100)/100.0
            return ProcessingIncidentDraft(values)
        }
    }
    private val values=linkedMapOf<String,Double>()
    private val past=ArrayDeque<Map<String,Double>>()
    private val future=ArrayDeque<Map<String,Double>>()
    private var gesture: Map<String,Double>?=null
    init { initial.forEach { (key,value) ->
        require(key in keys && value is Number && value.toDouble().isFinite() && value.toDouble() in 0.0..1.0)
        values[key]=(value as Number).toDouble()
    } }
    fun override(key: String): Double? { require(key in keys);return values[key] }
    val canUndo get()=past.isNotEmpty()
    val canRedo get()=future.isNotEmpty()
    private fun remember(before: Map<String,Double>) {
        past.addLast(before);if(past.size>24) past.removeFirst();future.clear()
    }
    private fun change(next: Map<String,Double>) {
        if(next==values) return
        if(gesture==null) remember(values.toMap())
        values.clear();values.putAll(next)
    }
    fun beginGesture() { if(gesture==null) gesture=values.toMap() }
    fun endGesture() { gesture?.let { if(it!=values) remember(it) };gesture=null }
    fun undo() { endGesture();if(past.isNotEmpty()) { future.addLast(values.toMap());values.clear();values.putAll(past.removeLast()) } }
    fun redo() { endGesture();if(future.isNotEmpty()) { past.addLast(values.toMap());values.clear();values.putAll(future.removeLast()) } }
    fun set(key: String,amount: Double?) {
        require(key in keys)
        if(amount!=null) require(amount.isFinite() && amount in 0.0..1.0)
        change(if(amount==null) values-key else values+(key to amount))
    }
    fun inheritAll()=change(emptyMap())
    /** A new imported recipe starts a new editing session, not an undo path to the old recipe. */
    fun reset() { values.clear();past.clear();future.clear();gesture=null }
    fun clean()=change(keys.associateWith { 0.0 })
    fun snapshot(): Map<String,Any?> = values.toMap()
    fun applyTo(recipe: Map<String,Any?>): Map<String,Any?> = recipe+values
}
