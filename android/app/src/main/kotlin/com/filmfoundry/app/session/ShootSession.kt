package com.filmfoundry.app.session

import com.filmfoundry.platform.CameraAdapter
import com.filmfoundry.platform.CaptureNormalizer
import com.filmfoundry.platform.CaptureRejected
import com.filmfoundry.platform.DefaultCaptureNormalizer
import com.filmfoundry.platform.MediumStore
import com.filmfoundry.platform.PreviewCapture
import com.filmfoundry.platform.StillCaptureRequest
import com.filmfoundry.workflow.FilmLibrary
import com.filmfoundry.workflow.Frame
import com.filmfoundry.workflow.FrameIdentity
import com.filmfoundry.workflow.Roll
import com.filmfoundry.workflow.WorkflowFacade
import com.filmfoundry.workflow.WorkflowRequests
import com.filmfoundry.workflow.WorkflowControl
import com.filmfoundry.workflow.requireLatentArchive

internal fun cameraLatentPath(rollId: String, frameIndex: Int): String =
    "$rollId/frame_${frameIndex.toString().padStart(2, '0')}.darkroom_latent.npz"

/**
 * Shoot one camera still into a roll. Preview cannot enter this path.
 */
class ShootSession(
    private val camera: CameraAdapter,
    private val library: FilmLibrary,
    private val store: MediumStore,
    private val facade: WorkflowFacade,
    private val normalizer: CaptureNormalizer = DefaultCaptureNormalizer(),
) {
    suspend fun captureFrame(
        rollId: String,
        frameIndex: Int,
        request: StillCaptureRequest = StillCaptureRequest(),
        control: WorkflowControl = WorkflowControl(),
        onRecovered: () -> Unit = {},
    ): Frame {
        control.checkCancel()
        return library.captureCameraShot(rollId, frameIndex) { roll ->
            exposeToPath(roll, frameIndex, request, control, onRecovered)
        }
    }

    suspend fun captureNextFrame(
        rollId: String, request: StillCaptureRequest = StillCaptureRequest(),
        control: WorkflowControl = WorkflowControl(),
        onRecovered: () -> Unit = {},
    ): Frame {
        control.checkCancel()
        return library.captureNextCameraShot(rollId) { roll, frame ->
            exposeToPath(roll, frame.frameIndex, request, control, onRecovered)
        }
    }

    private suspend fun exposeToPath(roll: Roll, frameIndex: Int, request: StillCaptureRequest,
        control: WorkflowControl, onRecovered: () -> Unit): String {
        control.checkCancel()
        val outputPath = cameraLatentPath(roll.rollId,frameIndex)
        val identity = FrameIdentity(rollId=roll.rollId,frameId=roll.frames[frameIndex-1].frameId,frameIndex=frameIndex)
        if(store.hasRecoverableCameraLatent(outputPath,identity,control)) {
            onRecovered()
            return outputPath
        }
        val source = camera.captureStill(request)
        control.checkCancel()
        if (source is PreviewCapture) {
            throw CaptureRejected("preview YUV/JPEG is not a still capture and is not a latent.")
        }
        val working = if(camera.transfersStillOwnership()) normalizer.normalizeOwned(source) else normalizer.normalize(source)
        val exposed = facade.run(
            WorkflowRequests.expose(
                outputPath = outputPath,
                recipe = mapOf("film" to roll.filmRecipe),
                width = working.width,
                height = working.height,
                linearRgb = working.linearRgb,
                captureRecord = working.record.toMetadata(),
                frameIdentity = identity,
                rowIo = store.rowExecutionIo(),
                control = control,
            ),
        )
        if (!exposed.mediumCommitted) store.writeLatent(outputPath, exposed.requireLatentArchive())
        return outputPath
    }
}
