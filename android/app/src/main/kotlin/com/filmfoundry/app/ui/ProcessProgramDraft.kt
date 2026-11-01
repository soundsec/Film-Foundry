package com.filmfoundry.app.ui

import com.filmfoundry.interchange.ProcessSteps
import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.workflow.RecipeDrafts
import com.filmfoundry.workflow.frozenRecipe

/** Detached processing draft. Navigating baths does not apply a recipe or touch a material. */
class ProcessProgramDraft(source: Map<String,Any?>,resolvedProgram: String?=null) {
    private val source=frozenRecipe(source)
    private val rows=(ProcessSteps.read(this.source["process_steps"]) ?: this.source["program_key"]?.toString()
        ?.takeIf { it !in listOf("auto","custom") }?.let(ProcessSteps::template)
        ?: resolvedProgram?.let(ProcessSteps::template)).orEmpty().map(::frozenRecipe).toMutableList()
    val steps get()=rows.toList()
    var free=this.source["process_steps"]!=null || this.source["program_key"]=="custom";private set
    var selected=if(rows.isEmpty()) -1 else 0;private set
    fun select(index: Int) { require(index in rows.indices);selected=index }
    /** Refresh a native program's presentation after a chemistry change; keep it native. */
    fun refreshResolved(program: String) {
        if(free) return
        val resolved=ProcessSteps.template(program).map(::frozenRecipe)
        rows.clear();rows.addAll(resolved);selected=selected.coerceIn(0,rows.lastIndex)
    }
    fun setFree(enabled: Boolean,template: String) {
        if(enabled && rows.isEmpty()) fill(template)
        free=enabled
    }
    fun fill(template: String) { rows.clear();rows.addAll(ProcessSteps.template(template).map(::frozenRecipe));selected=0 }
    fun add(action: String) {
        require(rows.size<32)
        rows+=ProcessSteps.read(listOf(mapOf("action" to action,"strength" to 1.0)))!!.single()
        selected=rows.lastIndex
    }
    fun replace(index: Int,step: Map<String,Any?>) {
        require(index in rows.indices);rows[index]=frozenRecipe(ProcessSteps.read(listOf(step))!!.single());selected=index
    }
    /** Opening/canceling or accepting an unchanged bath must keep the selected native program. */
    fun edit(index: Int,step: Map<String,Any?>): Boolean {
        require(index in rows.indices)
        val checked=frozenRecipe(ProcessSteps.read(listOf(step))!!.single())
        if(RecipeJson.equivalent(rows[index],checked)) return false
        rows[index]=checked;selected=index;free=true;return true
    }
    fun move(delta: Int) {
        val next=selected+delta;require(selected in rows.indices && next in rows.indices)
        val row=rows.removeAt(selected);rows.add(next,row);selected=next
    }
    fun remove() {
        require(selected in rows.indices);rows.removeAt(selected);selected=if(rows.isEmpty()) -1 else selected.coerceAtMost(rows.lastIndex)
    }
    fun build(edits: Map<String,Any?>): Map<String,Any?> {
        val values=source.toMutableMap()
        edits.forEach { (key,value)->if(value==null) values.remove(key) else values[key]=value }
        values["program_key"]=if(free) "custom" else source["program_key"]?.takeIf { it!="custom" } ?: "auto"
        if(free) values["process_steps"]=rows.map(::frozenRecipe) else values.remove("process_steps")
        return RecipeDrafts.process(values)
    }
}
