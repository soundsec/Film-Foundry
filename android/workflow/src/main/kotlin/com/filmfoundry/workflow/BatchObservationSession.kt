package com.filmfoundry.workflow

data class BatchObservationProgress(val completed: Int,val total: Int,val cancelled: Boolean=false)
class BatchObservationFailure(val completed: Int,val total: Int,cause: Exception): Exception("Saved $completed/$total frames: ${cause.message}",cause)

/** One frozen observation recipe, one frame at a time. Completed scans are never rolled back. */
object BatchObservationSession {
    suspend fun run(frames: List<DevelopedResult>,recipe: Map<String,Any?>,control: WorkflowControl,
        save: suspend (DevelopedResult,Map<String,Any?>,WorkflowControl)->Unit,
        progress: (BatchObservationProgress)->Unit={}): BatchObservationProgress {
        val selected=frames.toList();require(selected.isNotEmpty() && selected.map { it.resultId }.distinct().size == selected.size)
        val frozen=frozenRecipe(recipe);var completed=0
        try {
            for(frame in selected) { control.checkCancel();save(frame,frozen,control);completed++;progress(BatchObservationProgress(completed,selected.size)) }
            return BatchObservationProgress(completed,selected.size)
        } catch(error: Exception) {
            if(error is WorkflowException && error.code == "cancelled") return BatchObservationProgress(completed,selected.size,true)
            throw BatchObservationFailure(completed,selected.size,error)
        }
    }
}
