package com.filmfoundry.app.ui

import com.filmfoundry.workflow.frozenRecipe

/** UI-thread receipt for the recipe actually shown; no images or material state are retained. */
class ScanPreviewState {
    class Request internal constructor(val revision: Long,val resultId: String,val opticalPath: String,val recipe: Map<String,Any?>)
    private var revision=0L
    private var displayed: Request?=null

    fun invalidate() { revision++;displayed=null }
    /** Stop in-flight requests without invalidating a successful, still-current display. */
    fun cancelPendingKeepingDisplayed() {
        val shown=displayed?.takeIf { current(it) }
        revision++
        displayed=shown?.let { Request(revision,it.resultId,it.opticalPath,it.recipe) }
    }
    fun begin(resultId: String,opticalPath: String,recipe: Map<String,Any?>): Request {
        invalidate()
        return Request(revision,resultId,opticalPath,frozenRecipe(recipe))
    }
    fun current(request: Request)=request.revision==revision
    fun complete(request: Request): Boolean {
        if(!current(request)) return false
        displayed=request;return true
    }
    fun recipeFor(resultId: String,opticalPath: String): Map<String,Any?>? = displayed?.takeIf {
        current(it) && it.resultId==resultId && it.opticalPath==opticalPath
    }?.recipe
}
