package com.filmfoundry.app.session

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.MovieMasterCodec
import com.filmfoundry.platform.MediumStore
import com.filmfoundry.workflow.*

/** Sealed source rolls remain reusable; only a completed optical archive enters the result library. */
class DevelopSession(private val library: FilmLibrary, private val store: MediumStore, private val facade: WorkflowFacade) {
    /** One frozen recipe and one frame's working arrays at a time. Completed items survive cancellation. */
    suspend fun developRoll(rollId: String, batchId: String, recipe: Map<String, Any?>,
        control: WorkflowControl = WorkflowControl(), onFrame: (DevelopedResult) -> Unit = {}): List<DevelopedResult> {
        require(batchId.matches(Regex("[A-Za-z0-9_-]{1,80}"))) { "batchId must be a simple identifier" }
        val roll = library.roll(rollId)
        require(roll.lifecycle == RollLifecycle.SEALED) { "develop from the sealed roll library only" }
        require(roll.frames.none { it.state in listOf(FrameState.CAPTURING,FrameState.CAPTURED) }) { "generate captured latents before developing" }
        val frozen = MovieMasterCodec.snapshot(recipe).toMutableMap()
        frozen["film"] = MovieMasterCodec.snapshot(roll.filmRecipe)
        if (!frozen.containsKey("develop")) frozen["develop"] = MovieMasterCodec.snapshot(roll.developRecipe)
        val frames = roll.frames.filter { it.state == FrameState.LATENT && it.latentPath != null }.map { it.frameIndex }
        require(frames.isNotEmpty()) { "roll has no latent frames" }
        val ids = frames.map { "$batchId-$it" }
        require(library.developedResults().none { it.resultId in ids }) { "development batch already exists" }
        val results = mutableListOf<DevelopedResult>()
        for (index in frames) {
            control.checkCancel()
            val result = developFrame(rollId, index, "$batchId-$index", frozen, control)
            results.add(result)
            onFrame(result)
        }
        return results
    }

    suspend fun developFrame(rollId: String, frameIndex: Int, resultId: String,
        recipe: Map<String, Any?>, control: WorkflowControl = WorkflowControl()): DevelopedResult {
        val roll = library.roll(rollId)
        require(roll.lifecycle == RollLifecycle.SEALED) { "develop from the sealed roll library only" }
        require(frameIndex in 1..roll.capacity)
        val frame = roll.frames[frameIndex - 1]
        val source = frame.latentPath ?: throw WorkflowException("invalid_request", "frame has no latent")
        require(frame.state == FrameState.LATENT)
        require(resultId.matches(Regex("[A-Za-z0-9_-]{1,96}"))) { "resultId must be a simple identifier" }
        require(library.developedResults().none { it.resultId == resultId }) { "result already exists" }
        val path = "results/$resultId${DataKernel.NEGATIVE_SUFFIX}"
        store.requireNewOpticalTarget(path)
        val result = facade.run(store.developRequest(source, path, recipe, control))
        if (!result.mediumCommitted) store.writeOptical(path, result.requireOpticalArchive())
        return library.recordDevelopedResult(resultId, rollId, frameIndex, path, recipe)
    }
}
