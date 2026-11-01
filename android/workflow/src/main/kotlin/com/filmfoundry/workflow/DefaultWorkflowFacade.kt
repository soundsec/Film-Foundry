package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.kernel.KernelException
import com.filmfoundry.kernel.KernelNotReady
import com.filmfoundry.kernel.LatentState
import com.filmfoundry.kernel.OpticalMaster
import com.filmfoundry.kernel.PhoneReducedKernel
import com.filmfoundry.kernel.ReducedKernel

class DefaultWorkflowFacade(
    private val kernel: ReducedKernel,
) : WorkflowFacade, ObservationPreparationFacade {
    override fun prepareObservation(optical: OpticalMaster,recipe: Map<String,Any?>,checkCancel: ()->Unit)=
        (kernel as? com.filmfoundry.kernel.ObservationPreparingKernel)?.prepareObservation(optical,recipe,checkCancel)
    constructor() : this(PhoneReducedKernel())
    override suspend fun run(request: WorkflowRequest): WorkflowResult {
        val control = request.control
        control.checkCancel()
        control.report(request.command.wireName, "starting")
        val recipe = recipeWithIdentity(request)
        try {
            if (request.rowIo != null) return RowWorkflowExecutor(kernel).run(request, recipe)
            val result = when (request.command) {
                Command.EXPOSE -> {
                    val rgb = request.linearRgb
                        ?: throw WorkflowException("invalid_request", "expose requires linear RGB")
                    control.report(request.command.wireName, "loading")
                    control.report(request.command.wireName, "exposing")
                    val latent = stampCapture(kernel.expose(rgb, recipe), request.captureRecord)
                    control.report(request.command.wireName, "saving")
                    control.report(request.command.wireName, "completed")
                    WorkflowResult(
                        command = request.command,
                        outputPath = request.output.path,
                        mediumPath = request.output.path,
                        latent = latent,
                    )
                }
                Command.DEVELOP_LATENT -> {
                    val latent = request.latent
                        ?: throw WorkflowException("invalid_request", "develop_latent requires a latent state")
                    val materialRecipe=com.filmfoundry.interchange.MaterialSnapshot.resolve(recipe,latent.metadata)
                    verifyDevelopmentSource(latent.metadata,request.developmentRecord)
                    control.report(request.command.wireName, "loading")
                    control.report(request.command.wireName, "developing")
                    val optical = stampDevelopment(copyCapture(
                        // Requests borrow their input. The pointwise kernel can preserve it
                        // without copying the full material pools into another image volume.
                        kernel.develop(latent, materialRecipe, consumeLatent = false).let { formed ->
                            com.filmfoundry.kernel.FormedFilmBorder.prepare(kernel,materialRecipe,formed.opticalDensityRgb.shape[1],formed.opticalDensityRgb.shape[0])?.surround(formed) ?: formed
                        },
                        latent.metadata,
                    ),request.developmentRecord)
                    control.report(request.command.wireName, "saving")
                    control.report(request.command.wireName, "completed")
                    WorkflowResult(
                        command = request.command,
                        outputPath = request.output.path,
                        mediumPath = request.output.path,
                        optical = optical,
                    )
                }
                Command.SCAN -> {
                    val optical = request.optical
                        ?: throw WorkflowException("invalid_request", "scan requires an optical master")
                    control.report(request.command.wireName, "loading")
                    control.report(request.command.wireName, "scanning")
                    val observation = kernel.observe(optical, observeRecipe(recipe))
                    control.report(request.command.wireName, "completed")
                    WorkflowResult(
                        command = request.command,
                        outputPath = request.output.path,
                        mediumPath = request.output.path,
                        observation = observation,
                    )
                }
            }
            return result
        } catch (exc: KernelNotReady) {
            throw WorkflowException("runtime_failure", exc.message ?: "kernel is not ready")
        } catch (exc: KernelException) {
            throw WorkflowException("invalid_request", exc.message ?: "invalid kernel request")
        } catch (exc: IllegalArgumentException) {
            if (request.rowIo == null) throw exc
            throw WorkflowException("invalid_request", exc.message ?: "invalid row request")
        } catch (exc: java.io.IOException) {
            if (request.rowIo == null) throw exc
            throw WorkflowException("runtime_failure", exc.message ?: "row I/O failed")
        }
    }

    private fun recipeWithIdentity(request: WorkflowRequest): Map<String, Any?> {
        val identity = request.frameIdentity ?: return request.recipe
        val payload = request.recipe.toMutableMap()
        val frame = linkedMapOf<String, Any?>("roll_id" to identity.rollId)
        identity.frameId?.let { frame["frame_id"] = it }
        identity.frameIndex?.let { frame["frame_index"] = it }
        payload[DataKernel.FRAME_IDENTITY_KEY] = frame
        return payload
    }

    private fun observeRecipe(recipe: Map<String, Any?>): Map<String, Any?> {
        if (recipe["observe_mode"] != null) {
            return recipe
        }
        val payload = recipe.toMutableMap()
        payload["observe_mode"] = ObserveMode.SCANNER.wireName
        return payload
    }
}

    internal fun stampCapture(latent: LatentState, capture: Map<String, Any?>?): LatentState {
        if (capture == null) {
            return latent
        }
        val kind = capture["kind"]?.toString()
        if (kind == null || kind !in DataKernel.CAPTURE_KIND_VALUES) {
            throw WorkflowException("invalid_request", "capture.kind is missing or unknown")
        }
        val metadata = latent.metadata.toMutableMap()
        metadata[DataKernel.CAPTURE_OBJECT_KEY] = capture
        return LatentState(latent.arrays, metadata)
    }

    internal fun copyCapture(optical: OpticalMaster, latentMeta: Map<String, Any?>): OpticalMaster {
        val capture = latentMeta[DataKernel.CAPTURE_OBJECT_KEY] ?: return optical
        val metadata = optical.metadata.toMutableMap()
        metadata[DataKernel.CAPTURE_OBJECT_KEY] = capture
        return OpticalMaster(
            optical.opticalDensityRgb,
            metadata,
            optical.clearBaseOpticalDensityRgb,
            optical.spectralComponentAmounts,
            optical.spectralPostProcessDensityRgb,
        )
    }


