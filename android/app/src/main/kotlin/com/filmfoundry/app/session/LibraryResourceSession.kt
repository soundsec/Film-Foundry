package com.filmfoundry.app.session

import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.io.File

/** Resource ownership is an application concern, separate from formation and observation. */
class LibraryResourceSession(private val library: FilmLibrary,private val storage: StorageAdapter,
    private val scans: PrivateScanStore,private val medium: MediumStore,private val tasks: DevelopmentTaskStore) {
    private fun capturePaths(roll: Roll)=roll.frames.mapNotNull { it.capturePath?.substringBeforeLast('/') }.distinct()
    private val removal=PrivateResourceRemoval(File(storage.privateDir()),::referenced)
    val needsRecovery get()=removal.needsRecovery
    fun recover()=removal.recover()
    fun lineage(rollId: String,frameIndex: Int,resultId: String?)=ResourceOrigins.find(library,rollId,frameIndex,resultId,storage::existsPrivate)
    private fun referenced(path: String): Boolean = library.allRolls().any { roll ->
        path in capturePaths(roll) || roll.frames.any { it.latentPath==path } ||
            (roll.lifecycle!=RollLifecycle.DISCARDED && path in RollDirectReference.relativePaths(roll.rollId))
    } || library.developedResults().any { it.opticalPath==path } || scans.ownsPath(path)
    data class Inventory(val captures: Int,val captureBytes: Long,val latents: Int,val latentBytes: Long,
        val negatives: Int,val negativeBytes: Long,val photos: Int,val photoBytes: Long)
    fun inventory(rollId: String): Inventory {
        recover();val roll=library.roll(rollId)
        val camera=capturePaths(roll);val latent=roll.frames.mapNotNull { it.latentPath }
        val negatives=library.resultsForRoll(rollId).map { it.opticalPath }
        val photos=mutableListOf<SavedScan>();var offset=0
        do { val page=scans.pageNewest(rollId,offset);photos+=page.items;offset+=page.items.size } while(offset<page.total)
        return Inventory(camera.size,removal.bytes(camera),latent.size,removal.bytes(latent),negatives.size,removal.bytes(negatives),
            photos.size,removal.bytes(photos.map { "scans/${it.id}" }))
    }
    private fun idle(rollId: String) {
        require(!library.hasActiveRollJob(rollId)) { "Wait for the active roll job before removing resources" }
        val task=tasks.latest(rollId)
        require(task==null || task.status in listOf("completed","abandoned")) { "Finish or end the paused development task before removing resources" }
    }
    fun removePhoto(saved: SavedScan): Boolean {
        require(!library.hasActiveRollJob(saved.rollId)) { "Wait for the active roll job before removing photos" }
        return removal.remove(listOf("scans/${saved.id}")) { scans.remove(saved.id) }
    }
    fun removeNegative(result: DevelopedResult): Boolean {
        idle(result.sourceRollId)
        return removal.remove(listOf(result.opticalPath)) { library.removeDevelopedResult(result.resultId) }.also {
            NegativeDisplayStore.delete(storage,result.resultId)
        }
    }
    data class RemovalBatch(val completed: Int,val total: Int,val cleanupPending: Boolean,val error: Throwable?=null)
    /** Preflight the entire selection, then commit existing recoverable transactions one by one. */
    fun removeSelected(rolls: List<String> = emptyList(),negatives: List<DevelopedResult> = emptyList(),
        photos: List<SavedScan> = emptyList(),control: WorkflowControl=WorkflowControl(),removeRollCatalog: Boolean=false): RemovalBatch {
        val total=rolls.size+negatives.size+photos.size
        var done=0;var cleanup=false
        try {
            require(listOf(rolls,negatives,photos).count { it.isNotEmpty() }==1) { "Select one resource layer at a time" }
            require(rolls.distinct().size==rolls.size && negatives.map { it.resultId }.distinct().size==negatives.size && photos.map { it.id }.distinct().size==photos.size)
            control.checkCancel()
            for(id in rolls) { idle(id);require(!library.roll(id).catalogRemoved);if(!removeRollCatalog) require(library.roll(id).lifecycle==RollLifecycle.SEALED) { "Seal the roll before deleting exposures" } }
            val existing=library.developedResults().associateBy { it.resultId }
            for(item in negatives) { idle(item.sourceRollId);require(existing[item.resultId]==item) { "Selected negative is missing or changed" } }
            if(photos.isNotEmpty()) {
                val saved=scans.matching(check=control::checkCancel).associateBy { it.id }
                for(item in photos) { require(!library.hasActiveRollJob(item.rollId)) { "Wait for the active roll job before removing photos" };require(saved[item.id]==item) { "Selected photo is missing or changed" } }
            }
            fun commit(action: ()->Boolean) { control.checkCancel();cleanup=(!action()) || cleanup;done++ }
            for(id in rolls) commit { if(removeRollCatalog) removeRoll(id) else discardRoll(id) }
            for(item in negatives) commit { removeNegative(item) }
            for(item in photos) commit { removePhoto(item) }
            return RemovalBatch(done,total,cleanup)
        } catch(error: Exception) { return RemovalBatch(done,total,cleanup,error) }
    }
    fun releaseCameraSources(rollId: String,control: WorkflowControl): Boolean {
        idle(rollId);val roll=library.roll(rollId)
        require(roll.lifecycle==RollLifecycle.SEALED && roll.exposedCount()>0)
        // Validate the retained source before the reversible file move, not by image hashes.
        for(frame in roll.frames.filter { it.state!=FrameState.EMPTY }) {
            control.checkCancel()
            require(frame.state==FrameState.LATENT && frame.latentPath!=null && medium.hasRecoverableCameraLatent(
                frame.latentPath!!,FrameIdentity(rollId,frame.frameId,frame.frameIndex),control)) { "Complete and retain every latent before releasing original captures" }
        }
        control.checkCancel()
        return removal.remove(capturePaths(roll)) { library.releaseCaptureSources(rollId) }
    }
    fun discardRoll(rollId: String): Boolean {
        idle(rollId);val roll=library.roll(rollId)
        require(roll.lifecycle==RollLifecycle.SEALED)
        val paths=capturePaths(roll)+roll.frames.mapNotNull { it.latentPath }+com.filmfoundry.workflow.RollDirectReference.relativePaths(rollId)
        return removal.remove(paths) { library.discardRoll(rollId) }
    }
    fun removeRoll(rollId: String): Boolean {
        idle(rollId);val roll=library.roll(rollId)
        if(roll.lifecycle==RollLifecycle.DISCARDED) {
            removal.recover();library.removeRoll(rollId);return true
        }
        val paths=capturePaths(roll)+roll.frames.mapNotNull { it.latentPath }+RollDirectReference.relativePaths(rollId)
        return removal.remove(paths) { library.removeRoll(rollId) }
    }
}
