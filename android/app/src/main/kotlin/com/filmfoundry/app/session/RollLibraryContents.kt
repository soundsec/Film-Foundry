package com.filmfoundry.app.session

import com.filmfoundry.workflow.DevelopedResult

/** Catalog projection only. Results are independently owned; missing frames are never reconstructed. */
object RollLibraryContents {
    data class Batch(val id: String,val frames: List<DevelopedResult>)
    fun batchId(resultId: String): String {
        val suffix=resultId.substringAfterLast('-')
        return if(suffix.toIntOrNull()!=null) resultId.substringBeforeLast('-') else resultId
    }
    fun batchId(result: DevelopedResult)=batchId(result.resultId)
    fun batches(rollId: String,results: List<DevelopedResult>): List<Batch> = results.filter { it.sourceRollId==rollId }
        .groupBy(::batchId).map { (id,frames)->Batch(id,frames.sortedBy { it.sourceFrameIndex }) }
}
