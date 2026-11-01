package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.OpticalPreview
import com.filmfoundry.interchange.DevelopedOpticalArchive
import com.filmfoundry.kernel.OpticalMaster
import com.filmfoundry.kernel.PhoneObservationGrid

/** UI receives only a display buffer; it never constructs numerical kernel objects. */
class OpticalPreviewSession(private val facade: WorkflowFacade = DefaultWorkflowFacade(),private val retainPrepared: Boolean=false) {
    data class OutputDimensions(val width: Int,val height: Int)
    companion object {
        /** Dimensions of a saved scan, resolved from the original film rather than a display proxy. */
        fun outputDimensions(sourceWidth: Int,sourceHeight: Int,longEdge: Any?): OutputDimensions {
            val grid=PhoneObservationGrid.resolve(sourceWidth,sourceHeight,longEdge)
            return OutputDimensions(grid.width,grid.height)
        }
    }
    private var preparedSource=java.lang.ref.WeakReference<DevelopedOpticalArchive>(null)
    private var prepared: com.filmfoundry.kernel.PreparedObservation?=null
    private var generation=0L
    @Synchronized fun clear() { generation++;prepared=null;preparedSource.clear() }
    data class Observation(val width: Int, val height: Int, val rgb: FloatArray,
        val sourceWidth: Int, val sourceHeight: Int, val metadata: Map<String,Any?>)

    suspend fun observe(preview: OpticalPreview, recipe: Map<String,Any?>, viewer: Boolean,
        control: WorkflowControl = WorkflowControl(), mode: String? = null): Observation {
        // A saved-output grid must never turn a small display proxy into an 8K allocation.
        // Keep the requested recipe untouched; only cap this explicitly sampled display path.
        val scanner=(recipe["scanner"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty().toMutableMap()
        val requested=((scanner["scan_grid_long_edge"] ?: recipe["scan_grid_long_edge"]) as? Number)?.toInt()
        val displayEdge=maxOf(preview.archive.opticalDensityRgb.shape[0],preview.archive.opticalDensityRgb.shape[1])
        if(requested != null && requested > displayEdge) {
            if(displayEdge >= 2) scanner["scan_grid_long_edge"]=displayEdge else scanner.remove("scan_grid_long_edge")
        }
        val displayRecipe=if(requested != null) (recipe - "scan_grid_long_edge") + ("scanner" to scanner) else recipe
        val result=observeArchive(preview.archive,displayRecipe,viewer,control,mode,preparedDisplay=true)
        return result.copy(sourceWidth=preview.sourceWidth,sourceHeight=preview.sourceHeight,
            metadata=result.metadata + mapOf("display_proxy" to mapOf(
                "model" to preview.sampler,"stride" to preview.stride,
                "source_width" to preview.sourceWidth,"source_height" to preview.sourceHeight)))
    }

    /** Full-resolution scanner output. No preview sampling or source mutation. */
    suspend fun scan(archive: DevelopedOpticalArchive,recipe: Map<String,Any?>,
        control: WorkflowControl=WorkflowControl(),memoryBudgetBytes: Long?=null,outputExtraBytesPerPixel: Int=0): Observation=
        observeArchive(archive,recipe,false,control,memoryBudgetBytes=memoryBudgetBytes,outputExtraBytesPerPixel=outputExtraBytesPerPixel)

    /** Full-resolution light-table projection. This is the only observation that may be saved outside the app. */
    suspend fun project(archive: DevelopedOpticalArchive,recipe: Map<String,Any?>,
        control: WorkflowControl=WorkflowControl(),memoryBudgetBytes: Long?=null,outputExtraBytesPerPixel: Int=0): Observation=
        observeArchive(archive,recipe,true,control,ObserveMode.VIEWER_PROJECTION.wireName,memoryBudgetBytes,outputExtraBytesPerPixel)

    private suspend fun observeArchive(archive: DevelopedOpticalArchive,recipe: Map<String,Any?>,viewer: Boolean,
        control: WorkflowControl, mode: String? = null,memoryBudgetBytes: Long?=null,outputExtraBytesPerPixel: Int=0,preparedDisplay: Boolean=false): Observation {
        control.checkCancel()
        val payload = recipe.toMutableMap()
        val selected = mode ?: if(viewer) ObserveMode.VIEWER_LIGHT_TABLE.wireName else ObserveMode.SCANNER.wireName
        if(selected !in DataKernel.PHONE_OBSERVE_MODES) throw WorkflowException("invalid_request","unknown observe_mode: $selected")
        payload["observe_mode"] = selected
        if(memoryBudgetBytes != null) {
            require(outputExtraBytesPerPixel >= 0)
            val scanner=payload["scanner"] as? Map<*,*>
            val shape=archive.opticalDensityRgb.shape
            val grid=PhoneObservationGrid.resolve(shape[1],shape[0],scanner?.get("scan_grid_long_edge") ?: payload["scan_grid_long_edge"])
            val pixels=grid.width.toLong()*grid.height
            val resized=grid.width != shape[1] || grid.height != shape[0]
            val channels=3+(if(archive.spectralComponentAmounts != null) 9 else 0)+(if(archive.spectralPostProcessDensityRgb != null) 3 else 0)
            // Source arrays already exist. Account for readout RGB, precise statistics, resize arrays,
            // and explicitly declared frontend encoding buffers, with a small working reserve.
            val needed=pixels*(12L+outputExtraBytesPerPixel+(if(selected == "scanner") 4 else 0)+(if(resized) channels*4L else 0))+8L*1024*1024
            if(needed > memoryBudgetBytes) throw WorkflowException("invalid_request","Output grid ${grid.width}×${grid.height} needs about ${needed/1048576} MiB additional memory; ${memoryBudgetBytes.coerceAtLeast(0)/1048576} MiB available. Choose a smaller output grid; original film is retained.")
        }
        val optical = OpticalMaster(archive.opticalDensityRgb,archive.metadata,archive.clearBaseOpticalDensityRgb,
            archive.spectralComponentAmounts,archive.spectralPostProcessDensityRgb)
        val observation=if(retainPrepared && preparedDisplay) {
            val token: Long
            var ready: com.filmfoundry.kernel.PreparedObservation?
            synchronized(this) { token=generation;ready=if(preparedSource.get()===archive && prepared?.matches(payload)==true) prepared else null }
            if(ready==null) {
                ready=(facade as? ObservationPreparationFacade)?.prepareObservation(optical,payload,control::checkCancel)
                control.checkCancel()
                synchronized(this) { if(token==generation) {
                    prepared=ready?.takeIf { it.retainedBytes<=12L*1048576 };preparedSource=java.lang.ref.WeakReference(if(prepared==null) null else archive)
                } }
            }
            ready?.observe(payload) ?: facade.run(WorkflowRequest(Command.SCAN,MediumRef("optical_preview"),MediumRef("display"),payload,control=control,optical=optical)).observation!!
        } else facade.run(WorkflowRequest(Command.SCAN,MediumRef("optical_preview"),MediumRef("display"),payload,
            control=control,optical=optical)).observation!!
        control.checkCancel()
        return Observation(observation.outputRgb.shape[1],observation.outputRgb.shape[0],observation.outputRgb.values,
            archive.opticalDensityRgb.shape[1],archive.opticalDensityRgb.shape[0],observation.metadata)
    }
}
