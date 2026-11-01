package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.LibraryIndexCodec
import com.filmfoundry.interchange.LibraryIndexDocument
import com.filmfoundry.interchange.LibraryIndexException

interface LibraryIndexStore {
    /** Atomically publish the complete document; on failure retain the previous complete index. */
    fun writeIndex(bytes: ByteArray)
    fun readIndex(): ByteArray?
}

class MemoryLibraryIndexStore : LibraryIndexStore {
    private var payload: ByteArray? = null

    override fun writeIndex(bytes: ByteArray) {
        payload = bytes.copyOf()
    }

    override fun readIndex(): ByteArray? {
        return payload?.copyOf()
    }
}

/**
 * Phone film library: two independent collections.
 *
 * An undeveloped roll is a generator. Each sealed latent can yield many
 * transmissive developed results. A developed result cannot recreate the roll.
 * Developing does not consume or delete source latents. Discarding the roll
 * is an explicit space trade.
 *
 * Rolls are filled by camera stills only. Gallery pictures are not frames.
 * Optional [indexStore] writes an application JSON catalog; medium files stay NPZ.
 */
class FilmLibrary(
    private val indexStore: LibraryIndexStore? = null,
) {
    private val rolls = linkedMapOf<String, Roll>()
    private val results = linkedMapOf<String, DevelopedResult>()
    // One in-flight exposure per roll; never hold camera/pixel data or a monitor across suspension.
    private val pendingCameraShots = mutableSetOf<String>()
    private val pendingRollJobs = mutableSetOf<String>()

    @Synchronized fun hasActiveRollJob(rollId: String)=rollId in pendingRollJobs
    /** Negatives remain observable after the exposure roll was discarded. */
    suspend fun <T> withObservationJob(rollId: String,work: suspend ()->T): T {
        synchronized(this) {
            requireNoPendingShot(rollId);roll(rollId)
            require(pendingRollJobs.add(rollId)) { "roll job already active" }
        }
        try { return work() } finally { synchronized(this) { pendingRollJobs.remove(rollId) } }
    }

    /** Serialise product roll jobs and prevent source discard while their coroutines are active. */
    suspend fun <T> withSealedRollJob(rollId: String,work: suspend ()->T): T {
        synchronized(this) {
            requireNoPendingShot(rollId)
            require(roll(rollId).lifecycle == RollLifecycle.SEALED && rollId !in pendingRollJobs) { "roll job already active or source is not sealed" }
            pendingRollJobs.add(rollId)
        }
        try { return work() } finally { synchronized(this) { pendingRollJobs.remove(rollId) } }
    }

    init {
        restore()
    }

    @Synchronized
    fun loadRoll(
        rollId: String,
        capacity: Int,
        filmRecipe: Map<String, Any?>,
        label: String = "",
        developRecipe: Map<String, Any?> = emptyMap(),
        scannerRecipe: Map<String, Any?> = emptyMap(),
        captureSettings: Map<String, Any?> = emptyMap(),
    ): Roll {
        val id = rollId.trim()
        if (id.isEmpty()) {
            throw WorkflowException("invalid_request", "roll_id must be non-empty")
        }
        if (id in rolls) {
            throw WorkflowException("invalid_request", "roll already exists: $id")
        }
        val roll = Roll(
            rollId = id,
            capacity = capacity,
            filmRecipe = frozenRecipe(filmRecipe),
            developRecipe = frozenRecipe(developRecipe),
            scannerRecipe = frozenRecipe(scannerRecipe),
            label = label.trim(),
            captureSettings = frozenRecipe(captureSettings),
        )
        rolls[id] = roll
        try { persist() } catch(error: Exception) { rolls.remove(id); throw error }
        return roll
    }

    @Synchronized
    fun roll(rollId: String): Roll {
        return rolls[rollId] ?: throw WorkflowException("invalid_request", "unknown roll: $rollId")
    }

    @Synchronized
    fun sealedRolls(): List<Roll> {
        return rolls.values.filter { it.lifecycle == RollLifecycle.SEALED }
    }

    @Synchronized
    fun shootingRolls(): List<Roll> = rolls.values.filter {
        it.lifecycle == RollLifecycle.LOADING || it.lifecycle == RollLifecycle.SHOOTING
    }

    @Synchronized
    fun developedResults(): List<DevelopedResult> {
        return results.values.toList()
    }

    @Synchronized fun allRolls(): List<Roll> = rolls.values.toList()
    @Synchronized fun visibleRolls(): List<Roll> = rolls.values.filterNot { it.catalogRemoved }

    /** Detach only the saved camera bundles. A complete latent remains the roll's source. */
    @Synchronized fun releaseCaptureSources(rollId: String): List<String> {
        requireNoPendingShot(rollId)
        require(rollId !in pendingRollJobs) { "roll job is active" }
        val roll=roll(rollId)
        require(roll.lifecycle == RollLifecycle.SEALED && roll.exposedCount()>0) { "seal the roll first" }
        require(roll.frames.filter { it.state != FrameState.EMPTY }.all { it.state == FrameState.LATENT && it.latentPath != null }) { "form all latents before releasing camera sources" }
        val before=roll.frames.map { it.capturePath to it.captureExposures }
        val paths=before.mapNotNull { it.first }
        roll.frames.forEach { it.capturePath=null;it.captureExposures=0 }
        try { persist() } catch(error: Exception) {
            roll.frames.zip(before).forEach { (frame,old)->frame.capturePath=old.first;frame.captureExposures=old.second };throw error
        }
        return paths
    }

    /** Saved photos are independent outputs and may outlive this optical master. */
    @Synchronized fun removeDevelopedResult(resultId: String): DevelopedResult {
        val result=results[resultId] ?: throw WorkflowException("invalid_request","unknown result")
        requireNoPendingShot(result.sourceRollId)
        require(result.sourceRollId !in pendingRollJobs) { "roll job is active" }
        val before=results.toMap();results.remove(resultId)
        try { persist() } catch(error: Exception) { results.clear();results.putAll(before);throw error }
        return result
    }

    @Synchronized
    fun resultsForRoll(rollId: String): List<DevelopedResult> {
        return results.values.filter { it.sourceRollId == rollId }
    }
    @Synchronized fun renameRoll(rollId: String,label: String) {
        requireNoPendingShot(rollId)
        val roll=roll(rollId);val previous=roll.label;roll.label=RollNaming.validate(label)
        try { persist() } catch(error: Exception) { roll.label=previous;throw error }
    }

    data class CapturedUpdate(val path: String, val open: Boolean, val exposures: Int,val shutterArmed: Boolean=true,val gatePanorama: Boolean=false)

    /** Cock the shutter without advancing film or recording another exposure. */
    @Synchronized fun rearmCurrentFrame(rollId: String): Frame {
        requireNoPendingShot(rollId)
        val roll=roll(rollId)
        require(roll.lifecycle in listOf(RollLifecycle.LOADING,RollLifecycle.SHOOTING))
        val frame=roll.frames.firstOrNull { it.state == FrameState.CAPTURING } ?: error("no current exposed frame")
        require(!frame.shutterArmed) { "shutter is already armed" }
        frame.shutterArmed=true
        try { persist() } catch(error: Exception) { frame.shutterArmed=false;throw error }
        return frame
    }

    /** Durable source capture is an application state, never a pretend latent NPZ. */
    suspend fun recordCameraExposure(rollId: String, choose: (Roll) -> Int,
        write: suspend (Roll,Frame) -> CapturedUpdate): Frame {
        val (roll,frame)=synchronized(this) {
            requireNoPendingShot(rollId)
            val roll=roll(rollId)
            require(roll.lifecycle in listOf(RollLifecycle.LOADING,RollLifecycle.SHOOTING)) { "roll is sealed" }
            val frame=frameAt(roll,choose(roll))
            require(frame.state in listOf(FrameState.EMPTY,FrameState.CAPTURING,FrameState.CAPTURED)) { "frame already formed" }
            require(roll.frames.none { it.state == FrameState.CAPTURING && it.frameIndex != frame.frameIndex }) { "finish the open multiple exposure first" }
            pendingCameraShots.add(rollId); roll to frame
        }
        try {
            val update=write(roll,frame)
            require(update.path.endsWith(".capture/receipt.json"))
            require(update.exposures in 1..com.filmfoundry.interchange.CaptureReceiptCodec.MAX_EXPOSURES && update.exposures >= frame.captureExposures)
            require(frame.state != FrameState.CAPTURED || (!update.open && update.exposures == frame.captureExposures)) { "advanced frame cannot be exposed again" }
            return synchronized(this) {
                val old=frame.copy(); val lifecycle=roll.lifecycle
                frame.capturePath=update.path; frame.state=if(update.open) FrameState.CAPTURING else FrameState.CAPTURED
                frame.captureExposures=update.exposures
                frame.shutterArmed=update.shutterArmed
                frame.gatePanorama=update.gatePanorama
                roll.lifecycle=RollLifecycle.SHOOTING
                try { persist();frame } catch(error: Exception) {
                    frame.capturePath=old.capturePath; frame.captureExposures=old.captureExposures
                    frame.state=old.state;frame.shutterArmed=old.shutterArmed;frame.gatePanorama=old.gatePanorama; roll.lifecycle=lifecycle; throw error
                }
            }
        } finally { synchronized(this) { pendingCameraShots.remove(rollId) } }
    }

    suspend fun materializeCapturedFrame(rollId: String, index: Int,
        write: suspend (Roll,Frame) -> String): Frame {
        val (roll,frame)=synchronized(this) {
            requireNoPendingShot(rollId)
            val roll=roll(rollId); val frame=frameAt(roll,index)
            require(roll.lifecycle == RollLifecycle.SEALED && frame.state == FrameState.CAPTURED)
            pendingCameraShots.add(rollId); roll to frame
        }
        try {
            val path=write(roll,frame)
            require(path.endsWith(DataKernel.LATENT_SUFFIX))
            return synchronized(this) {
                val old=frame.copy()
                frame.state=FrameState.LATENT;frame.latentPath=path
                try { persist();frame } catch(error: Exception) {
                    frame.state=old.state;frame.latentPath=old.latentPath;throw error
                }
            }
        } finally { synchronized(this) { pendingCameraShots.remove(rollId) } }
    }

    /** Validate/reserve before the camera or writer runs, including across separate shoot sessions. */
    suspend fun captureCameraShot(
        rollId: String,
        frameIndex: Int,
        writeLatent: suspend (Roll) -> String,
    ): Frame = captureReserved(rollId, frameIndex) { roll, _ -> writeLatent(roll) }

    /** Selection and reservation share a lock; callers never predict or queue the next slot. */
    suspend fun captureNextCameraShot(
        rollId: String,
        writeLatent: suspend (Roll, Frame) -> String,
    ): Frame = captureReserved(rollId, null, writeLatent)

    private suspend fun captureReserved(
        rollId: String, frameIndex: Int?, writeLatent: suspend (Roll, Frame) -> String,
    ): Frame {
        val (roll, frame) = synchronized(this) {
            requireNoPendingShot(rollId)
            val roll = roll(rollId)
            val index = frameIndex ?: roll.frames.firstOrNull { it.state == FrameState.EMPTY }?.frameIndex
                ?: throw WorkflowException("invalid_request", "roll is full; load another roll")
            val frame = writableFrame(roll, index)
            pendingCameraShots.add(rollId)
            roll to frame
        }
        try {
            val path = writeLatent(roll, frame)
            return synchronized(this) { commitShot(rollId, frame.frameIndex, path, emptyList()) }
        } finally {
            synchronized(this) { pendingCameraShots.remove(rollId) }
        }
    }

    @Synchronized
    fun commitCameraShot(
        rollId: String,
        frameIndex: Int,
        latentPath: String,
        labels: List<String> = emptyList(),
    ): Frame {
        requireNoPendingShot(rollId)
        return commitShot(rollId, frameIndex, latentPath, labels)
    }

    private fun commitShot(rollId: String, frameIndex: Int, latentPath: String, labels: List<String>): Frame {
        if (!latentPath.endsWith(DataKernel.LATENT_SUFFIX)) {
            throw WorkflowException(
                "invalid_request",
                "camera still must freeze as ${DataKernel.LATENT_SUFFIX}",
            )
        }
        val roll = roll(rollId)
        val frame = writableFrame(roll, frameIndex)
        val oldFrame = frame.copy()
        val oldLifecycle = roll.lifecycle
        frame.state = FrameState.LATENT
        frame.latentPath = latentPath
        frame.labels = java.util.Collections.unmodifiableList(labels.toList())
        roll.lifecycle = RollLifecycle.SHOOTING
        try { persist() } catch(error: Exception) {
            frame.state = oldFrame.state
            frame.latentPath = oldFrame.latentPath
            frame.labels = oldFrame.labels
            roll.lifecycle = oldLifecycle
            throw error
        }
        return frame
    }

    private fun writableFrame(roll: Roll, frameIndex: Int): Frame {
        if (roll.lifecycle != RollLifecycle.LOADING && roll.lifecycle != RollLifecycle.SHOOTING) {
            throw WorkflowException("invalid_request", "sealed or discarded rolls cannot accept shots")
        }
        if(roll.frames.any { it.state == FrameState.CAPTURING })
            throw WorkflowException("invalid_request","finish the open multiple exposure first")
        val frame = frameAt(roll, frameIndex)
        if (frame.state != FrameState.EMPTY) {
            throw WorkflowException("invalid_request", "frame $frameIndex is already exposed")
        }
        return frame
    }

    private fun requireNoPendingShot(rollId: String) {
        if (rollId in pendingCameraShots) {
            throw WorkflowException("invalid_request", "roll has a camera exposure in progress")
        }
    }

    fun commitGalleryImport(rollId: String, frameIndex: Int, path: String): Nothing {
        throw WorkflowException(
            "invalid_request",
            "phone rolls accept camera stills only; imported pictures are not frames",
        )
    }

    fun commitPreviewAsShot(rollId: String, frameIndex: Int): Nothing {
        throw WorkflowException(
            "invalid_request",
            "preview may illuminate the viewer but is not a camera still",
        )
    }

    @Synchronized
    fun seal(rollId: String): Roll {
        requireNoPendingShot(rollId)
        val roll = roll(rollId)
        if (roll.lifecycle != RollLifecycle.LOADING && roll.lifecycle != RollLifecycle.SHOOTING) {
            throw WorkflowException("invalid_request", "only a shooting roll can enter the library")
        }
        if (roll.exposedCount() <= 0 || roll.frames.any { it.state == FrameState.CAPTURING }) {
            throw WorkflowException("invalid_request", "seal requires at least one exposed frame")
        }
        val previous = roll.lifecycle
        roll.lifecycle = RollLifecycle.SEALED
        try { persist() } catch(error: Exception) { roll.lifecycle = previous; throw error }
        return roll
    }

    /** Product sealing reconciles completed, unindexed camera files while the roll is reserved. */
    suspend fun sealWithSavedShots(rollId: String, findSaved: suspend (Roll) -> Map<Int,String>): Roll {
        val roll = synchronized(this) {
            requireNoPendingShot(rollId)
            val roll = roll(rollId)
            if(roll.lifecycle != RollLifecycle.LOADING && roll.lifecycle != RollLifecycle.SHOOTING)
                throw WorkflowException("invalid_request","only a shooting roll can enter the library")
            pendingCameraShots.add(rollId)
            roll
        }
        try {
            val saved = findSaved(roll)
            return synchronized(this) {
                // Validate the entire small plan before changing any frame or lifecycle.
                saved.forEach { (index,path) ->
                    writableFrame(roll,index)
                    require(path.endsWith(DataKernel.LATENT_SUFFIX)) { "saved shot must be a latent archive" }
                }
                val previous = roll.lifecycle
                val frames = roll.frames.map { it.copy() }
                try {
                    saved.forEach { (index,path) ->
                        val frame = frameAt(roll,index)
                        frame.state = FrameState.LATENT; frame.latentPath = path
                    }
                    if(roll.exposedCount() == 0 || roll.frames.any { it.state == FrameState.CAPTURING })
                        throw WorkflowException("invalid_request","finish the open exposure before sealing")
                    roll.lifecycle = RollLifecycle.SEALED
                    persist()
                    roll
                } catch(error: Exception) {
                    roll.lifecycle = previous
                    roll.frames.zip(frames).forEach { (frame,old) ->
                        frame.state=old.state; frame.latentPath=old.latentPath; frame.labels=old.labels
                    }
                    throw error
                }
            }
        } finally { synchronized(this) { pendingCameraShots.remove(rollId) } }
    }

    @Synchronized
    fun recordDevelopedResult(
        resultId: String,
        sourceRollId: String,
        sourceFrameIndex: Int,
        opticalPath: String,
        developRecipe: Map<String, Any?>,
    ): DevelopedResult {
        val id = resultId.trim()
        if (id.isEmpty()) {
            throw WorkflowException("invalid_request", "result_id must be non-empty")
        }
        if (id in results) {
            throw WorkflowException("invalid_request", "developed result already exists: $id")
        }
        if (
            !opticalPath.endsWith(DataKernel.NEGATIVE_SUFFIX) &&
            !opticalPath.endsWith(DataKernel.POSITIVE_SUFFIX)
        ) {
            throw WorkflowException(
                "invalid_request",
                "developed result must be ${DataKernel.NEGATIVE_SUFFIX} or ${DataKernel.POSITIVE_SUFFIX}",
            )
        }
        val roll = roll(sourceRollId)
        if (roll.lifecycle != RollLifecycle.SEALED) {
            throw WorkflowException("invalid_request", "develop from the sealed roll library only")
        }
        val frame = frameAt(roll, sourceFrameIndex)
        if (frame.state != FrameState.LATENT || frame.latentPath == null) {
            throw WorkflowException("invalid_request", "frame $sourceFrameIndex has no undeveloped latent")
        }
        val result = DevelopedResult(
            resultId = id,
            sourceRollId = sourceRollId,
            sourceFrameIndex = sourceFrameIndex,
            opticalPath = opticalPath,
            developRecipe = frozenRecipe(developRecipe),
            sourceRollPresent = true,
        )
        results[id] = result
        try { persist() } catch(error: Exception) { results.remove(id); throw error }
        return result
    }

    @Synchronized
    fun discardRoll(rollId: String, deleteLatent: (String) -> Unit = {}): Roll {
        return releaseRoll(rollId,false,deleteLatent)
    }

    /** Delete the catalog entry and all exposures; keep a source tombstone for derived resources. */
    @Synchronized fun removeRoll(rollId: String): Roll = releaseRoll(rollId,true,{})

    private fun releaseRoll(rollId: String, removeFromCatalog: Boolean, deleteLatent: (String)->Unit): Roll {
        requireNoPendingShot(rollId)
        require(rollId !in pendingRollJobs) { "roll job is active" }
        val roll = roll(rollId)
        if (roll.lifecycle == RollLifecycle.DISCARDED && (!removeFromCatalog || roll.catalogRemoved)) {
            return roll
        }
        if (!removeFromCatalog && roll.lifecycle != RollLifecycle.SEALED) {
            throw WorkflowException("invalid_request", "only a sealed library roll can be discarded")
        }
        val oldFrames=roll.frames.map { it.copy() }
        val oldLifecycle=roll.lifecycle;val oldRemoved=roll.catalogRemoved
        val oldPresent=results.values.filter { it.sourceRollId==rollId }.associate { it.resultId to it.sourceRollPresent }
        val paths=roll.frames.mapNotNull { it.latentPath }
        for (frame in roll.frames) {
            frame.latentPath = null
            frame.capturePath = null
            frame.captureExposures = 0
            if (frame.state in listOf(FrameState.LATENT,FrameState.CAPTURED,FrameState.CAPTURING)) {
                frame.state = FrameState.EMPTY
            }
        }
        roll.lifecycle = RollLifecycle.DISCARDED
        if(removeFromCatalog) roll.catalogRemoved=true
        for (result in results.values) {
            if (result.sourceRollId == rollId) {
                result.sourceRollPresent = false
            }
        }
        try { persist() } catch(error: Exception) {
            roll.lifecycle=oldLifecycle;roll.catalogRemoved=oldRemoved
            roll.frames.zip(oldFrames).forEach { (frame,old)->
                frame.latentPath=old.latentPath;frame.capturePath=old.capturePath
                frame.captureExposures=old.captureExposures;frame.state=old.state
            }
            oldPresent.forEach { (id,present)->results.getValue(id).sourceRollPresent=present }
            throw error
        }
        // File owners should stage a reversible removal around this index transaction.
        // In particular, an index failure must never delete a source first.
        paths.forEach(deleteLatent)
        return roll
    }

    fun undevelop(resultId: String): Nothing {
        throw WorkflowException(
            "invalid_request",
            "developed results cannot recreate an undeveloped roll",
        )
    }

    private fun frameAt(roll: Roll, frameIndex: Int): Frame {
        if (frameIndex < 1 || frameIndex > roll.capacity) {
            throw WorkflowException(
                "invalid_request",
                "frame_index must be between 1 and ${roll.capacity}",
            )
        }
        return roll.frames[frameIndex - 1]
    }

    private fun persist() {
        val store = indexStore ?: return
        try {
            store.writeIndex(
                LibraryIndexCodec.encode(
                    LibraryIndexDocument(
                        rolls = rolls.values.map { rollToMap(it) },
                        results = results.values.map { resultToMap(it) },
                    ),
                ),
            )
        } catch (exc: LibraryIndexException) {
            throw WorkflowException("invalid_request", exc.message ?: "library index encode failed")
        }
    }

    private fun restore() {
        val bytes = try {
            indexStore?.readIndex()
        } catch (exc: Exception) {
            throw WorkflowException("io_error", exc.message ?: "library index could not be read")
        } ?: return
        if (bytes.isEmpty()) {
            return
        }
        val document = try {
            LibraryIndexCodec.decode(bytes)
        } catch (exc: LibraryIndexException) {
            throw WorkflowException("invalid_request", exc.message ?: "library index is not readable")
        }
        rolls.clear()
        results.clear()
        for (item in document.rolls) {
            val roll = rollFromMap(item)
            rolls[roll.rollId] = roll
        }
        for (item in document.results) {
            val result = resultFromMap(item)
            results[result.resultId] = result
        }
    }

    private fun rollToMap(roll: Roll): Map<String, Any?> {
        return linkedMapOf(
            "roll_id" to roll.rollId,
            "capacity" to roll.capacity,
            "lifecycle" to roll.lifecycle.wireName,
            "label" to roll.label,
            "film" to roll.filmRecipe,
            "develop" to roll.developRecipe,
            "scanner" to roll.scannerRecipe,
            "capture" to roll.captureSettings,
            "frames" to roll.frames.map { frame ->
                linkedMapOf(
                    "frame_index" to frame.frameIndex,
                    "frame_id" to frame.frameId,
                    "state" to frame.state.wireName,
                    "latent_path" to frame.latentPath,
                    "capture_path" to frame.capturePath,
                    "capture_exposures" to frame.captureExposures,
                    "shutter_armed" to frame.shutterArmed,
                    "gate_panorama" to frame.gatePanorama,
                    "labels" to frame.labels,
                )
            },
        ).apply { if(roll.catalogRemoved) put("catalog_removed",true) }
    }

    private fun resultToMap(result: DevelopedResult): Map<String, Any?> {
        return linkedMapOf(
            "result_id" to result.resultId,
            "source_roll_id" to result.sourceRollId,
            "source_frame_index" to result.sourceFrameIndex,
            "optical_path" to result.opticalPath,
            "develop" to result.developRecipe,
            "source_roll_present" to result.sourceRollPresent,
        )
    }

    private fun rollFromMap(raw: Map<String, Any?>): Roll {
        val frames = (raw.getValue("frames") as List<*>).map { item ->
            val frame = item as Map<*, *>
            Frame(
                frameId = frame["frame_id"] as String,
                frameIndex = (frame["frame_index"] as Number).toInt(),
                state = FrameState.entries.first { it.wireName == frame["state"] },
                latentPath = frame["latent_path"] as String?,
                capturePath = frame["capture_path"] as String?,
                captureExposures = (frame["capture_exposures"] as Number).toInt(),
                shutterArmed = frame["shutter_armed"] as Boolean,
                gatePanorama = frame["gate_panorama"] as Boolean,
                labels = (frame["labels"] as List<*>).map { it.toString() },
            )
        }.sortedBy { it.frameIndex }.toMutableList()
        return Roll(
            rollId = raw.getValue("roll_id") as String,
            capacity = (raw.getValue("capacity") as Number).toInt(),
            filmRecipe = frozenRecipe(objectMap(raw["film"])),
            developRecipe = frozenRecipe(objectMap(raw["develop"])),
            scannerRecipe = frozenRecipe(objectMap(raw["scanner"])),
            frames = frames,
            lifecycle = RollLifecycle.entries.first { it.wireName == raw["lifecycle"] },
            label = raw["label"] as? String ?: "",
            captureSettings = frozenRecipe(objectMap(raw["capture"])),
            catalogRemoved = raw["catalog_removed"] == true,
        )
    }

    private fun resultFromMap(raw: Map<String, Any?>): DevelopedResult {
        return DevelopedResult(
            resultId = raw.getValue("result_id") as String,
            sourceRollId = raw.getValue("source_roll_id") as String,
            sourceFrameIndex = (raw.getValue("source_frame_index") as Number).toInt(),
            opticalPath = raw.getValue("optical_path") as String,
            developRecipe = frozenRecipe(objectMap(raw["develop"])),
            sourceRollPresent = raw.getValue("source_roll_present") as Boolean,
        )
    }

    private fun objectMap(value: Any?): Map<String, Any?> {
        val raw = value as? Map<*, *> ?: emptyMap<String, Any?>()
        return raw.entries.associate { it.key.toString() to it.value }
    }
}
