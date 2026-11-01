package com.filmfoundry.app.ui

import com.filmfoundry.workflow.frozenRecipe

/** A small UI draft. It owns parameters only, never pixels, media or processing jobs. */
data class ScanEditorDraft(
    val base: Map<String,Any?>?=null,
    val label: String?=null,
    val style: Int=0,
    val scannerKey: String="current",
    val overlay: Map<String,Any?> = emptyMap(),
    val gain: Int?=null,
    val manual: Boolean=false,
    val numbers: Map<String,String> = emptyMap(),
    val normalize: Boolean=true,
    val baseMode: Int=0,
    val inversionMode: Int=0,
    val grid: String="",
    val autoPreview: Boolean=true,
    val curveDraft: ScannerCurveState.Draft?=null,
    val rotationDegrees: Int=0,
    val outputFormat: String="PNG",val outputBits: Int=16,val jpegQuality: Int=95,
) {
    /** Saving a scanner definition changes its name/selection, not the current edit. */
    fun withSavedScanner(id: String)=copy(style=3,scannerKey="saved:$id").detached()
    fun detached()=copy(base=base?.let(::frozenRecipe),overlay=frozenRecipe(overlay),numbers=numbers.toMap(),
        curveDraft=curveDraft?.copy(curves=curveDraft.curves.map { it.toList() }))
    companion object {
        /** Continuing a photo is explicit; interpretation flags never come from selecting a style. */
        fun saved(recipe: Map<String,Any?>,label: String,autoPreview: Boolean): ScanEditorDraft {
            val scanner=recipe["scanner"] as? Map<*,*> ?: emptyMap<String,Any?>()
            fun value(key: String)=if(scanner.containsKey(key)) scanner[key] else recipe[key]
            fun mode(key: String)=when(value(key)) { true->1;false->2;else->0 }
            val edge=value("scan_grid_long_edge") as? Number
            return ScanEditorDraft(base=frozenRecipe(recipe),label=label,style=3,
                baseMode=mode("remove_base_mask"),inversionMode=mode("invert_transmission"),
                grid=edge?.toInt()?.toString().orEmpty(),autoPreview=autoPreview,rotationDegrees=com.filmfoundry.platform.OutputOrientation.read(recipe),
                outputFormat=com.filmfoundry.platform.ScanDelivery.format(recipe).name,outputBits=com.filmfoundry.platform.ScanDelivery.bits(recipe),jpegQuality=com.filmfoundry.platform.ScanDelivery.jpegQuality(recipe))
        }
    }
}

/** One gesture or picker transaction is one undo step; cancelling also preserves redo. */
class ScanEditHistory(initial: ScanEditorDraft,private val limit: Int=24) {
    var current=initial.detached();private set
    private val past=ArrayDeque<ScanEditorDraft>()
    private val future=ArrayDeque<ScanEditorDraft>()
    private var start: ScanEditorDraft?=null
    private var depth=0
    val canUndo get()=past.isNotEmpty()
    val canRedo get()=future.isNotEmpty()
    val editing get()=depth>0
    fun synchronize(next: ScanEditorDraft) { current=next.detached() }
    fun begin() { if(depth++==0) start=current }
    fun change(next: ScanEditorDraft) {
        val value=next.detached()
        if(current==value) return
        if(depth==0) remember(current)
        current=value
    }
    fun commit() {
        if(depth==0 || --depth>0) return
        start?.takeIf { it!=current }?.let(::remember);start=null
    }
    fun cancel(): ScanEditorDraft { start?.let { current=it };start=null;depth=0;return current }
    fun undo(): ScanEditorDraft {
        while(depth>0) commit()
        if(past.isNotEmpty()) { future.addLast(current);current=past.removeLast() };return current
    }
    fun redo(): ScanEditorDraft {
        while(depth>0) commit()
        if(future.isNotEmpty()) { past.addLast(current);current=future.removeLast() };return current
    }
    private fun remember(value: ScanEditorDraft) {
        past.addLast(value);while(past.size>limit) past.removeFirst();future.clear()
    }
}

/** Bounded per-image sessions; the reusable workspace defaults are kept by the caller. */
class ScanEditorSessions(private val capacity: Int=8) {
    init { require(capacity>0) }
    companion object {
        fun key(resultId: String,path: String,photoId: String?=null,resume: String?=null)=
            photoId?.let { "photo:$it:$resultId:$path" } ?: resume ?: "film:$resultId:$path"
    }
    private val sessions=linkedMapOf<String,ScanEditHistory>()
    fun open(key: String,initial: ()->ScanEditorDraft): ScanEditHistory {
        val state=sessions.remove(key) ?: ScanEditHistory(initial())
        sessions[key]=state
        while(sessions.size>capacity) sessions.remove(sessions.keys.first())
        return state
    }
    fun clear()=sessions.clear()
}
