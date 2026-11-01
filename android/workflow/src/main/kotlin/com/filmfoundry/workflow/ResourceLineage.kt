package com.filmfoundry.workflow

enum class ResourceAvailability { AVAILABLE, REMOVED, MISSING_FILE }

/** Exactly three product resources. Capture bundles and latent files are the exposure roll's contents. */
data class ResourceLineage(val rollId: String,val frameIndex: Int,val resultId: String?,
    val exposure: ResourceAvailability,val developed: ResourceAvailability,val result: DevelopedResult?)

object ResourceOrigins {
    fun find(library: FilmLibrary,rollId: String,frameIndex: Int,resultId: String?,exists: (String)->Boolean): ResourceLineage {
        val roll=library.allRolls().firstOrNull { it.rollId==rollId }
        val frame=roll?.frames?.getOrNull(frameIndex-1)
        val exposure=when {
            roll==null || roll.lifecycle==RollLifecycle.DISCARDED || frame==null || frame.state==FrameState.EMPTY -> ResourceAvailability.REMOVED
            listOfNotNull(frame.latentPath,frame.capturePath).any(exists) -> ResourceAvailability.AVAILABLE
            else -> ResourceAvailability.MISSING_FILE
        }
        val result=library.developedResults().firstOrNull { it.resultId==resultId }
        require(result==null || (result.sourceRollId==rollId && result.sourceFrameIndex==frameIndex)) { "resource origin identity mismatch" }
        val developed=when { result==null->ResourceAvailability.REMOVED;exists(result.opticalPath)->ResourceAvailability.AVAILABLE;else->ResourceAvailability.MISSING_FILE }
        return ResourceLineage(rollId,frameIndex,resultId,exposure,developed,result)
    }
}
