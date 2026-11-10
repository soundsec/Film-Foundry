package com.filmfoundry.app.session

import com.filmfoundry.interchange.*
import com.filmfoundry.platform.MediumStore
import com.filmfoundry.workflow.*
import java.util.Collections

/** Persistent application coordinator: one source frame, one result, then release and continue. */
class RollDevelopmentSession(private val library: FilmLibrary,private val medium: MediumStore,
    private val tasks: DevelopmentTaskStore,private val captures: DeferredShootSession,
    private val facade: WorkflowFacade=DefaultWorkflowFacade(),
    private val rowBudget: ()->Long?={ null },private val rowWorkers: ()->Int={1}) {
    companion object {
        const val LIGHTWEIGHT_REVISION="phone_roll_lightweight_v12"
        const val FORMATION_REVISION="phone_roll_layer_grain_v11"
        private const val DENSITY_REVISION="phone_roll_stock_density_v10"
        private const val REBATE_REVISION="phone_roll_formed_rebate_v9"
        private const val LAYER_REVISION="phone_roll_layer_material_v8"
        private const val SPATIAL_REVISION="phone_roll_spatial_bath_v7"
        private const val LEDGER_REVISION="phone_roll_activation_ledger_v6"
        private const val BATH_REVISION="phone_roll_material_bath_v5"
        private const val GRAIN_REVISION="phone_roll_grain_dispatch_v4"
        private const val ORDERED_REVISION="phone_roll_ordered_process_v3"
        private const val MATERIAL_REVISION="phone_roll_material_effects_v2"
        private const val PREVIOUS_REVISION="phone_roll_material_effects_v1"
    }
    data class Progress(val taskId: String,val frameIndex: Int,val total: Int,val done: Int,val phase: String)
    fun latest(rollId: String)=tasks.latest(rollId)?.let(::freeze)

    suspend fun create(rollId: String,taskId: String,recipe: Map<String,Any?>): DevelopmentTask =
        library.withSealedRollJob(rollId) {
            val old=latest(rollId)
            require(old == null || old.status in listOf("completed","abandoned")) { "resume or explicitly finish the previous task first" }
            val roll=library.roll(rollId)
            val frozen=MovieMasterCodec.snapshot(recipe).toMutableMap()
            frozen.remove("film_border")
            roll.captureSettings["film_border"]?.let { frozen["film_border"]=it }
            frozen["film"]=MovieMasterCodec.snapshot(roll.filmRecipe)
            if("develop" !in frozen) frozen["develop"]=MovieMasterCodec.snapshot(roll.developRecipe)
            val develop=(frozen["develop"] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
            frozen["develop"]=RecipeDrafts.process(if("program_key" in develop || develop["process_steps"]!=null) develop else develop+("program_key" to "auto"))
            val frames=roll.frames.filter { it.state in listOf(FrameState.CAPTURED,FrameState.LATENT) }.map {
                DevelopmentTaskFrame(it.frameIndex,it.frameId,it.latentPath ?: cameraLatentPath(rollId,it.frameIndex),it.capturePath)
            }
            val profile=PhoneFormationProfile.validate(roll.captureSettings[PhoneFormationProfile.ROLL_SETTING])
            frozen.remove(PhoneFormationProfile.OPTION)
            val task=freeze(DevelopmentTask(taskId,rollId,frozen,frames,
                revision=if(profile==PhoneFormationProfile.LIGHTWEIGHT) LIGHTWEIGHT_REVISION else FORMATION_REVISION))
            tasks.create(task);task
        }

    suspend fun abandon(rollId: String,taskId: String) = library.withSealedRollJob(rollId) {
        val task=tasks.read(rollId,taskId)
        require(task.status !in listOf("completed","abandoned"))
        tasks.save(task.copy(status="abandoned",error=null))
    }

    suspend fun run(rollId: String,taskId: String,control: WorkflowControl=WorkflowControl(),
        onProgress: (Progress)->Unit={},onFrame: suspend (DevelopedResult)->Unit={}): List<DevelopedResult> =
        runWithRows(rollId,taskId,control,onProgress,retainRows=false) { result,_,_->onFrame(result) }

    /** Row lease is valid only inside onFrame, after durable media and library registration. */
    suspend fun runWithRows(rollId: String,taskId: String,control: WorkflowControl=WorkflowControl(),
        onProgress: (Progress)->Unit={},retainRows: Boolean=true,
        onFrame: suspend (DevelopedResult,RowMediumIo?,RowMediumHandoff)->Unit): List<DevelopedResult> =
        library.withSealedRollJob(rollId) {
            var task=freeze(tasks.read(rollId,taskId))
            require(task.status != "abandoned") { "task was explicitly ended" }
            require(task.profile == DataKernel.PHONE_REDUCED_PROFILE_ID && task.revision in listOf(DevelopmentTaskCodec.KIND,PREVIOUS_REVISION,MATERIAL_REVISION,ORDERED_REVISION,GRAIN_REVISION,BATH_REVISION,LEDGER_REVISION,SPATIAL_REVISION,LAYER_REVISION,REBATE_REVISION,DENSITY_REVISION,FORMATION_REVISION,LIGHTWEIGHT_REVISION)) { "task requires a different implementation" }
            val roll=library.roll(rollId)
            require(RecipeJson.equivalent(task.recipe["film"],MovieMasterCodec.snapshot(roll.filmRecipe))) { "roll material differs from frozen task" }
            val frameIds=roll.frames.filter { it.state in listOf(FrameState.CAPTURED,FrameState.LATENT) }.map { it.frameIndex to it.frameId }
            require(task.frames.map { it.index to it.frameId } == frameIds) { "roll frame list differs from frozen task" }
            for(f in task.frames) {
                val actual=roll.frames[f.index-1]
                require(actual.capturePath == f.capturePath && (actual.latentPath ?: cameraLatentPath(rollId,f.index)) == f.sourcePath) { "task source path changed" }
            }
            fun checkpoint(next: DevelopmentTask) { tasks.save(next);task=freeze(next) }
            fun mark(frame: DevelopmentTaskFrame) { checkpoint(task.copy(frames=task.frames.map { if(it.index == frame.index) frame else it })) }
            fun resultId(f: DevelopmentTaskFrame)="${task.id}-${f.index}"
            fun outputPath(f: DevelopmentTaskFrame)="results/${resultId(f)}${DataKernel.NEGATIVE_SUFFIX}"
            fun indexed(f: DevelopmentTaskFrame): DevelopedResult? = library.developedResults().firstOrNull { it.resultId == resultId(f) }?.also {
                require(it.sourceRollId == rollId && it.sourceFrameIndex == f.index && it.opticalPath == outputPath(f) &&
                    RecipeJson.equivalent(MovieMasterCodec.snapshot(it.developRecipe),task.recipe) && it.sourceRollPresent && medium.opticalExists(it.opticalPath)) { "registered result does not match frozen task" }
            }
            if(task.status == "completed") return@withSealedRollJob task.frames.map { indexed(it) ?: error("completed result missing") }
            val completed=mutableListOf<DevelopedResult>()
            try {
                control.checkCancel();checkpoint(task.copy(status="running",error=null))
                for(initial in task.frames.toList()) {
                    control.checkCancel()
                    var frame=initial
                    var result=indexed(frame)
                    val rowIo=medium.rowExecutionIo()
                    RowMediumHandoff().use { handoff ->
                      if(result == null) {
                        onProgress(Progress(task.id,frame.index,task.frames.size,completed.size,"forming"))
                        mark(frame.copy(phase="forming"))
                        val beforeFormation=System.nanoTime()
                        val source=captures.prepareFrame(rollId,frame.index,control,
                            if(task.revision in listOf(FORMATION_REVISION,LIGHTWEIGHT_REVISION) && roll.captureSettings[PhoneGrainModels.ROLL_SETTING]==PhoneGrainModels.LAYERED) PhoneGrainModels.LAYERED
                            else if(task.revision in listOf(LIGHTWEIGHT_REVISION,FORMATION_REVISION,DENSITY_REVISION,REBATE_REVISION,LAYER_REVISION,SPATIAL_REVISION,LEDGER_REVISION,BATH_REVISION,GRAIN_REVISION)) PhoneGrainModels.CURRENT else PhoneGrainModels.PREVIOUS,
                            rowIo,handoff)
                        require(source.latentPath == frame.sourcePath && source.frameId == frame.frameId)
                        frame=frame.copy(phase="developing",formationMs=frame.formationMs+elapsed(beforeFormation))
                        mark(frame)
                        onProgress(Progress(task.id,frame.index,task.frames.size,completed.size,"developing"))
                        control.checkCancel()
                        val beforeDevelopment=System.nanoTime()
                        val path=outputPath(frame)
                        val record=DevelopmentTaskCodec.provenance(task,frame)
                        if(!medium.hasRecoverableTaskOptical(path,record,control)) {
                            val request=medium.developRequest(frame.sourcePath,path,formationRecipe(task),control).copy(
                                frameIdentity=FrameIdentity(rollId,frame.frameId,frame.index),developmentRecord=record,
                                rowBudgetBytes=rowBudget(),rowIo=rowIo,rowHandoff=handoff,
                                opticalHandoff=handoff.takeIf { retainRows },
                                rowWorkers=rowWorkers())
                            val output=facade.run(request)
                            if(!output.mediumCommitted) medium.writeOptical(path,output.requireOpticalArchive())
                        }
                        // A completed optical write is not revoked by a late cancellation: register it first.
                        result=library.recordDevelopedResult(resultId(frame),rollId,frame.index,path,task.recipe)
                        frame=frame.copy(developmentMs=frame.developmentMs+elapsed(beforeDevelopment))
                      }
                    val registered=requireNotNull(result)
                    mark(frame.copy(phase="registered"))
                    completed.add(registered)
                    onProgress(Progress(task.id,frame.index,task.frames.size,completed.size,"registered"))
                    onFrame(registered,rowIo,handoff)
                    }
                }
                checkpoint(task.copy(status="completed",error=null))
                completed
            } catch(error: Exception) {
                val status=if(error is WorkflowException && error.code == "cancelled") "paused" else "failed"
                try { checkpoint(task.copy(status=status,error=error.message?.take(1000))) }
                catch(writeError: Exception) { error.addSuppressed(writeError) }
                throw error
            }
        }
    private fun elapsed(start: Long)=((System.nanoTime()-start)/1_000_000).coerceAtLeast(0)
    /** Resume legacy tasks with their previous omitted effects, even if old JSON contained these fields. */
    private fun formationRecipe(task: DevelopmentTask): Map<String,Any?> {
        // Execution selectors belong to the task revision, never to imported recipe extras.
        val original=(task.recipe-PhoneMaterialPhenomena.DEVELOPMENT_OPTION-PhoneOpticalDensityModels.OPTION-PhoneFormationProfile.OPTION).let { if(task.revision in listOf(LIGHTWEIGHT_REVISION,FORMATION_REVISION,DENSITY_REVISION,REBATE_REVISION)) it else it-"film_border" }
        if(task.revision==LIGHTWEIGHT_REVISION) return original+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT,
            PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT)
        if(task.revision in listOf(FORMATION_REVISION,DENSITY_REVISION)) return original+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT)
        if(task.revision in listOf(REBATE_REVISION,LAYER_REVISION)) return original+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        if(task.revision==SPATIAL_REVISION) return original+(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
        if(task.revision==LEDGER_REVISION) return original+(PhoneProcessModels.OPTION to PhoneProcessModels.LEDGER)
        if(task.revision==BATH_REVISION) return original+(PhoneProcessModels.OPTION to PhoneProcessModels.PREVIOUS)
        if(task.revision in listOf(GRAIN_REVISION,ORDERED_REVISION)) return original
        fun section(key: String)=(task.recipe[key] as? Map<*,*>)?.entries?.associate { it.key.toString() to it.value }.orEmpty()
        val legacy=original+("develop" to (section("develop")-setOf("program_key","process_steps","temperature_c","concentration","agitation","agitation_time_s")))
        if(task.revision in listOf(MATERIAL_REVISION,PREVIOUS_REVISION)) return legacy
        return legacy+("film" to (section("film")+("grain_strength" to 0.0)))+
            ("develop" to (((legacy["develop"] as Map<String,Any?>))+mapOf("uneven_development" to 0.0,"chemical_stain" to 0.0,"reticulation_strength" to 0.0)))
    }
    private fun freeze(task: DevelopmentTask)=task.copy(recipe=frozenRecipe(task.recipe),frames=Collections.unmodifiableList(task.frames.toList()))
}
