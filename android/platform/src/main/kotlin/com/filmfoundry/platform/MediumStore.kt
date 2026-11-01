package com.filmfoundry.platform

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.DevelopmentTaskCodec
import com.filmfoundry.interchange.MovieMasterCodec
import com.filmfoundry.interchange.RecipeJson
import com.filmfoundry.interchange.DevelopedOpticalArchive
import com.filmfoundry.interchange.LatentArchive
import com.filmfoundry.interchange.MediumArchive
import com.filmfoundry.interchange.NpzMediumArchive
import com.filmfoundry.workflow.RowMediumIo
import com.filmfoundry.workflow.RowMediumHandoff
import com.filmfoundry.workflow.FrameIdentity
import com.filmfoundry.workflow.WorkflowControl
import com.filmfoundry.workflow.WorkflowRequest
import com.filmfoundry.workflow.WorkflowRequests
import java.io.InputStream
import java.io.OutputStream

class MediumStore(
    private val storage: StorageAdapter,
    private val archive: MediumArchive = NpzMediumArchive(),
) {
    /** Real file storage opts into bounded row formation; legacy/memory adapters keep their API. */
    fun rowExecutionIo(): RowMediumIo? {
        if (archive !is NpzMediumArchive) return null
        return storage.rowScratchRoot()?.let { root -> object : RowMediumIo {
        override val scratchRoot = root
        override fun <T> read(path: String, reader: (InputStream) -> T): T = storage.readPrivate(path, reader)
        override fun write(path: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit) = storage.writePrivate(path, beforeCommit, writer)
        override fun sameLocation(a: String, b: String): Boolean = storage.samePrivateLocation(a, b)
        override fun version(path: String)=storage.privateVersion(path)
        } }
    }

    fun developRequest(input: String, output: String, recipe: Map<String, Any?>,
        control: WorkflowControl = WorkflowControl()): WorkflowRequest {
        requireSuffix(input, DataKernel.LATENT_SUFFIX)
        requireDevelopedSuffix(output)
        if (storage.samePrivateLocation(input, output)) throw CaptureRejected("source roll must not be overwritten")
        val io = rowExecutionIo()
        return WorkflowRequests.developLatent(input, output, recipe, if (io == null) readLatent(input) else null, io, control)
    }
    fun writeLatent(relative: String, latent: LatentArchive) {
        requireSuffix(relative, DataKernel.LATENT_SUFFIX)
        storage.writePrivate(relative) { archive.writeLatent(latent, it) }
    }

    fun readLatent(relative: String, allowConsumed: Boolean = false): LatentArchive {
        requireSuffix(relative, DataKernel.LATENT_SUFFIX)
        return storage.readPrivate(relative) { archive.readLatent(it, allowConsumed) }
    }

    /** Recover a completed camera latent after index failure; never replace a file already present. */
    fun hasRecoverableCameraLatent(relative: String, identity: FrameIdentity,
        control: WorkflowControl = WorkflowControl()): Boolean {
        requireSuffix(relative, DataKernel.LATENT_SUFFIX)
        control.checkCancel()
        if (!storage.existsPrivate(relative)) return false
        val metadata = try {
            storage.readPrivate(relative) { input ->
                if (archive is NpzMediumArchive) archive.inspect(input,true,control::checkCancel).metadata
                else archive.readLatent(input).metadata
            }
        } catch(error: com.filmfoundry.workflow.WorkflowException) { throw error }
        catch(error: Exception) { throw CaptureRejected("Saved latent is invalid and was preserved: ${error.message}") }
        control.checkCancel()
        val frame = metadata[DataKernel.FRAME_IDENTITY_KEY] as? Map<*,*>
        val capture = metadata[DataKernel.CAPTURE_OBJECT_KEY] as? Map<*,*>
        if(frame == null || frame["roll_id"] != identity.rollId ||
            (frame["frame_index"] as? Number)?.toInt() != identity.frameIndex ||
            (frame["frame_id"] != null && frame["frame_id"] != identity.frameId) ||
            capture?.get("kind") !in DataKernel.CAPTURE_KIND_VALUES || metadata["consumed"] == true) {
            throw CaptureRejected("Saved latent does not match this camera frame and was preserved")
        }
        return true
    }

    fun writeOptical(relative: String, optical: DevelopedOpticalArchive) {
        requireDevelopedSuffix(relative)
        storage.writePrivate(relative) { archive.writeDevelopedOptical(optical, it) }
    }

    /** App result IDs cannot reuse any saved output, including an unindexed one. */
    fun requireNewOpticalTarget(relative: String) {
        requireDevelopedSuffix(relative)
        if(storage.existsPrivate(relative))
            throw CaptureRejected("Saved optical result already exists and was preserved; use a new result ID")
    }

    fun opticalExists(relative: String): Boolean { requireDevelopedSuffix(relative);return storage.existsPrivate(relative) }

    /** Only an exact task/recipe/frame match can reconcile a completed but unindexed result. */
    fun hasRecoverableTaskOptical(relative: String,record: Map<String,Any?>,control: WorkflowControl): Boolean {
        requireDevelopedSuffix(relative);control.checkCancel()
        if(!storage.existsPrivate(relative)) return false
        val metadata=try {
            storage.readPrivate(relative) { input ->
                if(archive is NpzMediumArchive) archive.inspect(input,false,control::checkCancel).metadata
                else archive.readDevelopedOptical(input).metadata
            }
        } catch(error: com.filmfoundry.workflow.WorkflowException) { throw error }
        catch(error: Exception) { throw CaptureRejected("Saved task optical master is invalid and was preserved: ${error.message}") }
        control.checkCancel()
        val frame=metadata[DataKernel.FRAME_IDENTITY_KEY] as? Map<*,*>
        if(!RecipeJson.equivalent(metadata[DevelopmentTaskCodec.PROVENANCE_KEY],MovieMasterCodec.snapshot(record)) ||
            frame == null || frame["roll_id"] != record["roll_id"] || frame["frame_id"] != record["frame_id"] ||
            (frame["frame_index"] as? Number)?.toInt() != (record["frame_index"] as? Number)?.toInt())
            throw CaptureRejected("Saved optical master does not match this task/recipe/frame and was preserved")
        return true
    }

    fun readOptical(relative: String): DevelopedOpticalArchive {
        requireDevelopedSuffix(relative)
        return storage.readPrivate(relative) { archive.readDevelopedOptical(it) }
    }
    fun scanRows(relative: String,recipe: Map<String,Any?>,output: com.filmfoundry.workflow.RowObservationSession.Output,
        control: WorkflowControl=WorkflowControl(),memoryBudgetBytes: Long?=null,outputBytesPerPixel: Int=4,compute: DeferredPixelCompute?=null,
        rowIo: RowMediumIo?=null,handoff: RowMediumHandoff?=null): Map<String,Any?> {
        requireDevelopedSuffix(relative)
        val io=rowIo ?: rowExecutionIo() ?: throw CaptureNotReady("Row observation requires file scratch storage")
        return com.filmfoundry.workflow.RowObservationSession.scan(io,relative,recipe,output,control,memoryBudgetBytes,outputBytesPerPixel,compute,handoff)
    }

    fun scanPng(relative: String,recipe: Map<String,Any?>,output: java.io.OutputStream,
        control: WorkflowControl=WorkflowControl(),memoryBudgetBytes: Long?=null,compute: DeferredPixelCompute?=null,bitDepth: Int=8,
        rowIo: RowMediumIo?=null,handoff: RowMediumHandoff?=null): Pair<Int,Int> =
        RgbPngRows(output,control::checkCancel,bitDepth).use { png ->
            val angle=OutputOrientation.read(recipe)
            if(angle==0) scanRows(relative,recipe,png,control,memoryBudgetBytes,outputBytesPerPixel=0,compute=compute,rowIo=rowIo,handoff=handoff)
            else RotatingPngRows(rowExecutionIo()?.scratchRoot ?: error("Rotation requires temporary file storage"),png,angle,control::checkCancel,bitDepth).use { rotated ->
                scanRows(relative,recipe,rotated,control,memoryBudgetBytes,outputBytesPerPixel=0,compute=compute,rowIo=rowIo,handoff=handoff)
                control.measured("scan","output_rotation") { rotated.finish() }
            }
            png.finish();png.width to png.height
        }

    private fun requireSuffix(relative: String, suffix: String) {
        if (!relative.endsWith(suffix)) {
            throw CaptureRejected("medium path must end with $suffix.")
        }
    }

    private fun requireDevelopedSuffix(relative: String) {
        if (
            !relative.endsWith(DataKernel.NEGATIVE_SUFFIX) &&
            !relative.endsWith(DataKernel.POSITIVE_SUFFIX)
        ) {
            throw CaptureRejected(
                "developed path must end with ${DataKernel.NEGATIVE_SUFFIX} or ${DataKernel.POSITIVE_SUFFIX}.",
            )
        }
    }
}
