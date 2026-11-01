package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.LatentArchive
import com.filmfoundry.interchange.DevelopedOpticalArchive
import com.filmfoundry.kernel.LatentState
import com.filmfoundry.kernel.OpticalMaster
import com.filmfoundry.kernel.RgbVolume
import com.filmfoundry.kernel.ScanObservation

enum class Command(val wireName: String) {
    EXPOSE("expose"),
    DEVELOP_LATENT("develop_latent"),
    SCAN("scan"),
    ;

    companion object {
        fun fromWire(name: String): Command {
            return entries.firstOrNull { it.wireName == name }
                ?: throw WorkflowException("unsupported_command", "Unsupported workflow command: $name")
        }
    }
}

enum class FrameState(val wireName: String) {
    EMPTY("empty"),
    CAPTURING("capturing"),
    CAPTURED("captured"),
    LATENT("latent"),
    FAILED("failed"),
    CONSUMED("consumed"),
    DEVELOPED("developed"),
}

enum class RollLifecycle(val wireName: String) {
    LOADING("loading"),
    SHOOTING("shooting"),
    SEALED("sealed"),
    DISCARDED("discarded"),
}

data class FrameIdentity(
    val rollId: String,
    val frameId: String? = null,
    val frameIndex: Int? = null,
)

data class MediumRef(val path: String)

data class WorkflowControl(
    val cancelRequested: () -> Boolean = { false },
    val onProgress: (command: String, stage: String) -> Unit = { _, _ -> },
    /** Optional application diagnostic; nanoseconds, outside material metadata and stage formulas. */
    val onTiming: ((command: String, section: String, elapsedNanos: Long) -> Unit)? = null,
) {
    fun checkCancel() {
        if (cancelRequested()) {
            throw WorkflowException("cancelled", "cancelled")
        }
    }

    fun report(command: String, stage: String) {
        require(stage in DataKernel.WORKFLOW_PROGRESS_STAGES) { "unknown stage $stage" }
        onProgress(command, stage)
    }

    fun <T> measured(command: String, section: String, work: () -> T): T {
        val callback=onTiming ?: return work()
        val started=System.nanoTime()
        try { return work() } finally {
            // A diagnostic consumer cannot fail a material write or hide cancellation.
            try { callback(command,section,(System.nanoTime()-started).coerceAtLeast(0)) } catch(_: Exception) { }
        }
    }
}

data class WorkflowRequest(
    val command: Command,
    val input: MediumRef,
    val output: MediumRef,
    val recipe: Map<String, Any?>,
    val frameIdentity: FrameIdentity? = null,
    val control: WorkflowControl = WorkflowControl(),
    val linearRgb: RgbVolume? = null,
    val latent: LatentState? = null,
    val optical: OpticalMaster? = null,
    val captureRecord: Map<String, Any?>? = null,
    val rowIo: RowMediumIo? = null,
    val rgbRows: RgbRows? = null,
    val rowBlockPixels: Int = 16384,
    val developmentRecord: Map<String, Any?>? = null,
    /** When set, formation chooses its own pass length from this byte budget. Null keeps rowBlockPixels. */
    val rowBudgetBytes: Long? = null,
    val rowWorkers: Int = 1,
    /** Optional one-frame handoff after a durable latent commit; never part of a recipe or medium. */
    val rowHandoff: RowMediumHandoff? = null,
    /** Optional optical rows for the immediately following observation, after durable commit. */
    val opticalHandoff: RowMediumHandoff? = null,
)

data class WorkflowResult(
    val command: Command,
    val outputPath: String?,
    val mediumPath: String? = null,
    val latent: LatentState? = null,
    val optical: OpticalMaster? = null,
    val observation: ScanObservation? = null,
    val mediumCommitted: Boolean = false,
)

class WorkflowException(val code: String, message: String) : RuntimeException(message) {
    init {
        require(code in DataKernel.WORKFLOW_ERROR_CODES) { "unknown error code $code" }
    }
}

data class Frame(
    val frameId: String,
    val frameIndex: Int,
    var state: FrameState = FrameState.EMPTY,
    var latentPath: String? = null,
    var labels: List<String> = emptyList(),
    var capturePath: String? = null,
    var captureExposures: Int = 0,
    var shutterArmed: Boolean = true,
    var gatePanorama: Boolean = false,
)

data class DevelopedResult(
    val resultId: String,
    val sourceRollId: String,
    val sourceFrameIndex: Int,
    val opticalPath: String,
    val developRecipe: Map<String, Any?>,
    var sourceRollPresent: Boolean = true,
)

data class Roll(
    val rollId: String,
    val capacity: Int,
    val filmRecipe: Map<String, Any?>,
    var developRecipe: Map<String, Any?> = emptyMap(),
    var scannerRecipe: Map<String, Any?> = emptyMap(),
    val frames: MutableList<Frame> = mutableListOf(),
    var lifecycle: RollLifecycle = RollLifecycle.LOADING,
    var label: String = "",
    val captureSettings: Map<String, Any?> = emptyMap(),
    /** Provenance remains for independent negatives/photos after the roll leaves the catalog. */
    var catalogRemoved: Boolean = false,
) {
    init {
        if (capacity !in DataKernel.PHONE_ROLL_CAPACITY_VALUES) {
            throw WorkflowException("invalid_request", "unsupported roll capacity")
        }
        com.filmfoundry.interchange.StillFrameFormat.validateRoll(capacity,captureSettings)
        if (frames.isEmpty()) {
            repeat(capacity) { index ->
                val frameIndex = index + 1
                frames.add(Frame(frameId = "$rollId:$frameIndex", frameIndex = frameIndex))
            }
        } else if (frames.size != capacity) {
            throw WorkflowException("invalid_request", "roll frames must match capacity $capacity")
        }
    }

    fun latentCount(): Int = frames.count { it.state == FrameState.LATENT && it.latentPath != null }
    fun exposedCount(): Int = frames.count { it.state in setOf(FrameState.CAPTURING,FrameState.CAPTURED,FrameState.LATENT) }
}

interface ObservationPreparationFacade {
    fun prepareObservation(optical: OpticalMaster,recipe: Map<String,Any?>,checkCancel: ()->Unit): com.filmfoundry.kernel.PreparedObservation?
}

interface WorkflowFacade {
    suspend fun run(request: WorkflowRequest): WorkflowResult
}

object WorkflowRequests {
    fun expose(
        outputPath: String,
        recipe: Map<String, Any?>,
        width: Int,
        height: Int,
        linearRgb: FloatArray,
        captureRecord: Map<String, Any?>? = null,
        frameIdentity: FrameIdentity? = null,
        control: WorkflowControl = WorkflowControl(),
        rowIo: RowMediumIo? = null,
    ): WorkflowRequest {
        return WorkflowRequest(
            command = Command.EXPOSE,
            input = MediumRef("camera"),
            output = MediumRef(outputPath),
            recipe = recipe,
            frameIdentity = frameIdentity,
            control = control,
            linearRgb = RgbVolume(width, height, 3, linearRgb),
            captureRecord = captureRecord,
            rowIo = rowIo,
        )
    }

    fun developLatent(inputPath: String, outputPath: String, recipe: Map<String, Any?>,
        archive: LatentArchive? = null, rowIo: RowMediumIo? = null, control: WorkflowControl = WorkflowControl()): WorkflowRequest =
        WorkflowRequest(Command.DEVELOP_LATENT, MediumRef(inputPath), MediumRef(outputPath), recipe,
            control = control, latent = archive?.let { LatentState(it.arrays, it.metadata) }, rowIo = rowIo)
}

fun WorkflowResult.requireLatentArchive(): LatentArchive {
    val latent = latent ?: throw WorkflowException("runtime_failure", "expose did not return a latent")
    return LatentArchive(latent.arrays, latent.metadata)
}

fun WorkflowResult.requireOpticalArchive(): DevelopedOpticalArchive {
    val value = optical ?: throw WorkflowException("runtime_failure", "develop did not return an optical master")
    return DevelopedOpticalArchive(value.opticalDensityRgb, value.metadata, value.clearBaseOpticalDensityRgb,
        value.spectralComponentAmounts, value.spectralPostProcessDensityRgb)
}
