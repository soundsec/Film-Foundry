package com.filmfoundry.app.session

import com.filmfoundry.interchange.*
import com.filmfoundry.platform.*
import com.filmfoundry.workflow.*
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class RollDevelopmentSessionTest {
    @Test fun committedFilmRowsReachScanningAndFailedConsumerCanResumeFromDurableFilm() = runBlocking {
        val library=FilmLibrary();roll(library,1);val engine=session(library)
        val task=engine.create("roll","optical-handoff",emptyMap())
        val medium=MediumStore(storage());val times=mutableListOf<String>()
        val control=WorkflowControl(onTiming={command,phase,_->times.add("$command.$phase")})
        assertThrows(IllegalStateException::class.java) { runBlocking {
            engine.runWithRows("roll",task.id,control) { result,rowIo,handoff ->
                assertTrue(library.developedResults().any { it.resultId==result.resultId })
                assertTrue(storage().existsPrivate(result.opticalPath))
                val actual=ByteArrayOutputStream()
                medium.scanPng(result.opticalPath,emptyMap(),actual,control,bitDepth=16,rowIo=rowIo,handoff=handoff)
                val reference=ByteArrayOutputStream();medium.scanPng(result.opticalPath,emptyMap(),reference,bitDepth=16)
                assertArrayEquals(reference.toByteArray(),actual.toByteArray())
                assertTrue("scan.handoff_hit" in times);assertFalse("scan.archive_read" in times)
                error("consumer interrupted after scan")
            }
        } }
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        val before=storage().readPrivate("results/${task.id}-1.darkroom_negative.npz");times.clear()
        engine.runWithRows("roll",task.id,control) { result,rowIo,handoff ->
            medium.scanPng(result.opticalPath,emptyMap(),ByteArrayOutputStream(),control,rowIo=rowIo,handoff=handoff)
        }
        assertTrue("scan.archive_read" in times);assertFalse("scan.handoff_hit" in times)
        assertEquals(1,developRuns);assertEquals(1,exposeRuns);assertEquals("completed",engine.latest("roll")!!.status)
        assertArrayEquals(before,storage().readPrivate("results/${task.id}-1.darkroom_negative.npz"))
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }
    @Test fun newRollsFreezeLayerDyeCloudAndLaterBatchesKeepTheOriginalLatent() = runBlocking {
        val film=PhoneMaterialDefaults.withEffects(mapOf("grain_strength" to .025),0.0,0.0,1.0)
        val library=FilmLibrary()
        library.loadRoll("roll",12,film,captureSettings=mapOf("spatial_effects_revision" to PhoneMaterialDefaults.REVISION,
            PhoneGrainModels.ROLL_SETTING to PhoneGrainModels.LAYERED))
        val shoot=capture(library);shoot.capture("roll",camera);shoot.seal("roll")
        val disk=PrivateDevelopmentTaskStore(storage());val frame=library.roll("roll").frames.first()
        val r=mapOf("film" to film,"develop" to mapOf("program_key" to "auto"))
        val engine=session(library,disk);engine.create("roll","layers",r);engine.run("roll","layers")
        assertEquals(PhoneGrainModels.LAYERED,(MediumStore(storage()).readLatent(cameraLatentPath("roll",1)).metadata["phone_grain_profile"] as Map<*,*>)["model"])
        // Existing archives win even when a later batch selects a different implementation.
        disk.create(DevelopmentTask("old-density","roll",r,listOf(DevelopmentTaskFrame(1,frame.frameId,cameraLatentPath("roll",1),frame.capturePath)),revision="phone_roll_stock_density_v10"))
        engine.run("roll","old-density")
        assertEquals(PhoneGrainModels.LAYERED,(MediumStore(storage()).readLatent(cameraLatentPath("roll",1)).metadata["phone_grain_profile"] as Map<*,*>)["model"])
        Unit
    }
    @Test fun v10TaskStillFormsV3GrainWhenNoLatentHasBeenWritten() = runBlocking {
        val library=FilmLibrary()
        val film=PhoneMaterialDefaults.withEffects(mapOf("grain_strength" to .025),0.0,0.0,1.0)
        library.loadRoll("roll",12,film,captureSettings=mapOf("spatial_effects_revision" to PhoneMaterialDefaults.REVISION))
        val shoot=capture(library);shoot.capture("roll",camera);shoot.seal("roll")
        val disk=PrivateDevelopmentTaskStore(storage());val frame=library.roll("roll").frames.first()
        disk.create(DevelopmentTask("old","roll",mapOf("film" to film,"develop" to mapOf("program_key" to "auto")),
            listOf(DevelopmentTaskFrame(1,frame.frameId,cameraLatentPath("roll",1),frame.capturePath)),revision="phone_roll_stock_density_v10"))
        session(library,disk).run("roll","old")
        assertEquals(PhoneGrainModels.CURRENT,(MediumStore(storage()).readLatent(cameraLatentPath("roll",1)).metadata["phone_grain_profile"] as Map<*,*>)["model"])
        Unit
    }
    @Test fun v9TasksKeepTheirDensityRangeAndNewTasksSelectTheCorrectedStockRange() = runBlocking {
        val library=FilmLibrary();roll(library,1,mapOf("grain_strength" to 0.0,"density_max" to listOf(2.4,2.2,2.0)))
        val disk=PrivateDevelopmentTaskStore(storage());val frame=library.roll("roll").frames.first()
        val recipe=mapOf("film" to library.roll("roll").filmRecipe,"develop" to mapOf("program_key" to "color_reversal"))
        disk.create(DevelopmentTask("v9","roll",recipe,listOf(DevelopmentTaskFrame(1,frame.frameId,cameraLatentPath("roll",1),frame.capturePath)),revision="phone_roll_formed_rebate_v9"))
        val engine=session(library,disk)
        val old=MediumStore(storage()).readOptical(engine.run("roll","v9").single().opticalPath)
        val oldValues=old.opticalDensityRgb.values.copyOf()
        fun model(a: DevelopedOpticalArchive)=(((a.metadata["film_process_model"] as Map<*,*>)["execution_implementation"] as Map<*,*>)["component_models"] as Map<*,*>)["optical"]
        assertEquals(PhoneOpticalDensityModels.PREVIOUS,model(old))
        val task=engine.create("roll","v10",recipe)
        assertEquals(RollDevelopmentSession.FORMATION_REVISION,task.revision)
        val current=MediumStore(storage()).readOptical(engine.run("roll","v10").single().opticalPath)
        assertEquals(PhoneOpticalDensityModels.CURRENT,model(current))
        assertTrue(current.opticalDensityRgb.values.average()>oldValues.average())
        assertArrayEquals(oldValues,MediumStore(storage()).readOptical("results/v9-1.darkroom_negative.npz").opticalDensityRgb.values,0f)
        Unit
    }
    @TempDir lateinit var root: Path
    private var exposeRuns=0;private var developRuns=0
    private val real=DefaultWorkflowFacade()
    private val facade=object: WorkflowFacade {
        override suspend fun run(request: WorkflowRequest): WorkflowResult {
            if(request.command == Command.EXPOSE) exposeRuns++
            if(request.command == Command.DEVELOP_LATENT) developRuns++
            return real.run(request.copy(rowBlockPixels=3))
        }
    }
    private val camera=object: StoredStillCamera {
        override fun saveStill(target: File): StoredStillInfo {
            target.writeBytes(byteArrayOf(1,2,3))
            return StoredStillInfo(3,2,CaptureRecord(CaptureKind.DISPLAY_REFERRED_SRGB,true,sourceEncoding="iec61966_2_1_srgb"))
        }
    }
    private fun capture(library: FilmLibrary,engine: WorkflowFacade=facade)=DeferredShootSession(library,MediumStore(storage()),
        CaptureReceiptStore(root.toFile()),root.resolve("scratch").toFile(),engine,openPixels= {
        object: StoredExposurePixels {
            override val width=3;override val height=2
            override fun encodedRows(start: Int,count: Int)=FloatArray(width*count*3){.4f}
            override fun close()=Unit
        }
    })
    private suspend fun roll(library: FilmLibrary,count: Int=2,film: Map<String,Any?> = emptyMap()) {
        library.loadRoll("roll",12,film);val shoot=capture(library)
        repeat(count) { shoot.capture("roll",camera) };shoot.seal("roll")
    }
    private fun session(library: FilmLibrary,store: DevelopmentTaskStore=PrivateDevelopmentTaskStore(storage()),engine: WorkflowFacade=facade)=
        RollDevelopmentSession(library,MediumStore(storage()),store,capture(library,engine),engine)

    @Test fun newFormationHandsRowsToDevelopmentButLaterBatchesReadTheOriginalLatent() = runBlocking {
        val library=FilmLibrary();roll(library,1);val session=session(library)
        val times=mutableListOf<String>();val control=WorkflowControl(onTiming={ command,section,_ -> times.add("$command.$section") })
        session.create("roll","handoff",emptyMap());session.run("roll","handoff",control)
        assertTrue("develop_latent.archive_handoff" in times);assertFalse("develop_latent.archive_read" in times)
        val source=storage().readPrivate(cameraLatentPath("roll",1));times.clear()
        session.create("roll","again",emptyMap());session.run("roll","again",control)
        assertTrue("develop_latent.archive_read" in times);assertArrayEquals(source,storage().readPrivate(cameraLatentPath("roll",1)))
        assertEquals(1,exposeRuns);assertEquals(2,developRuns)
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }
    @Test fun pauseAfterLatentCommitCleansHandoffAndResumeUsesThatSameSource() = runBlocking {
        val library=FilmLibrary();roll(library,1);val session=session(library)
        val task=session.create("roll","handoff-pause",emptyMap());var cancel=false
        assertThrows(WorkflowException::class.java) { runBlocking {
            session.run("roll",task.id,WorkflowControl(cancelRequested={cancel}),onProgress={ if(it.phase=="developing") cancel=true })
        } }
        val source=storage().readPrivate(cameraLatentPath("roll",1))
        assertEquals("paused",session.latest("roll")!!.status)
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        session.run("roll",task.id)
        assertEquals(1,exposeRuns);assertEquals(1,developRuns)
        assertArrayEquals(source,storage().readPrivate(cameraLatentPath("roll",1)))
        assertEquals("completed",session.latest("roll")!!.status)
    }

    @Test fun invalidAutomaticScannerLeavesDevelopmentAndItsOriginalExposureIntact() = runBlocking {
        val library=FilmLibrary();roll(library,1)
        val photos=DevelopmentPhotoPlan.prepare(true) { throw IllegalArgumentException("invalid output size") }
        val engine=session(library);engine.create("roll","scan-invalid",emptyMap())
        val results=engine.run("roll","scan-invalid",onFrame={ if(photos.enabled) fail<Unit>("Invalid scanner must not run") })
        assertFalse(photos.enabled);assertEquals("invalid output size",photos.error)
        assertEquals(1,results.size);assertEquals("completed",engine.latest("roll")!!.status)
        assertTrue(MediumStore(storage()).opticalExists(results.single().opticalPath))
        assertArrayEquals(byteArrayOf(1,2,3),Files.readAllBytes(root.resolve("roll/frame_01.capture/exposure_000001/source.jpg")))
        assertEquals(1,exposeRuns);assertEquals(1,developRuns);Unit
    }

    @Test fun newTaskFreezesRollRebateWhileV8ResumesWithoutRetrofittingIt() = runBlocking {
        val library=FilmLibrary();val settings=mapOf("film_border" to mapOf("enabled" to true,"format_key" to "135","stock_label" to "TEST"))
        library.loadRoll("roll",12,emptyMap(),captureSettings=settings);val shoot=capture(library);shoot.capture("roll",camera);shoot.seal("roll")
        val job=session(library);val task=job.create("roll","rebate",mapOf("film_border" to mapOf("enabled" to false)))
        assertEquals(settings["film_border"],task.recipe["film_border"])
        val result=job.run("roll","rebate").single();val medium=MediumStore(storage()).readOptical(result.opticalPath)
        assertEquals(4,medium.opticalDensityRgb.shape[0]);assertEquals(FilmRebateGeometry.MODEL,(medium.metadata[FilmRebateGeometry.KEY] as Map<*,*>)["model"])
        val old=task.copy(id="old-rebate",revision="phone_roll_layer_material_v8",status="ready")
        PrivateDevelopmentTaskStore(storage()).create(old)
        val restored=job.run("roll",old.id).single();val legacy=MediumStore(storage()).readOptical(restored.opticalPath)
        assertEquals(2,legacy.opticalDensityRgb.shape[0]);assertFalse(legacy.metadata.containsKey(FilmRebateGeometry.KEY))
    }

    @Test fun newRollLayerExposureIsFrozenAndDeferredOutsideShutterSaving() = runBlocking {
        val library=FilmLibrary();val film=mapOf<String,Any?>("spatial_reference_short_edge_mm" to 24.0,"light_piping_strength" to .3,"light_piping_edge_mode" to "left")
        library.loadRoll("roll",12,film,captureSettings=mapOf("format" to "135","material_model" to PhoneProcessModels.CURRENT,
            "spatial_effects_revision" to PhoneMaterialDefaults.REVISION,PhoneMaterialPhenomena.ROLL_SETTING to PhoneMaterialPhenomena.CURRENT))
        val shoot=capture(library);shoot.capture("roll",camera)
        assertEquals(0,exposeRuns);assertEquals(0,developRuns)
        shoot.seal("roll");val job=session(library);job.create("roll","new-layer",emptyMap());val out=job.run("roll","new-layer").first()
        val medium=MediumStore(storage());val latent=medium.readLatent(cameraLatentPath("roll",1))
        assertEquals("phone_layer_exposure_additions_v1",(latent.metadata["phone_layer_exposure"] as Map<*,*>)["model"])
        assertTrue((medium.readOptical(out.opticalPath).metadata["film_process_model"] as Map<*,*>).containsKey("layer_exposure"))
        assertArrayEquals(byteArrayOf(1,2,3),Files.readAllBytes(root.resolve("roll/frame_01.capture/exposure_000001/source.jpg")))
        Unit
    }

    @Test fun pausedV7KeepsItsGrainWhileNewV8AddsSilverAndOldRollExposureStaysFrozen() = runBlocking {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index)
        val film=mapOf<String,Any?>("color_process" to "bw","grain_strength" to 0.0,"silver_grain_strength" to .15,
            "light_piping_strength" to .3,"light_piping_edge_mode" to "left")
        roll(library,2,film)
        val recipe=mapOf("film" to library.roll("roll").filmRecipe,"develop" to mapOf("program_key" to "bw_negative"),
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val frames=library.roll("roll").frames.filter { it.state==FrameState.CAPTURED }.map { DevelopmentTaskFrame(it.frameIndex,it.frameId,cameraLatentPath("roll",it.frameIndex),it.capturePath) }
        val disk=PrivateDevelopmentTaskStore(storage());disk.create(DevelopmentTask("old-v7","roll",recipe,frames,revision="phone_roll_spatial_bath_v7"))
        var cancel=false;var first=byteArrayOf()
        assertThrows(WorkflowException::class.java) { runBlocking { session(library).run("roll","old-v7",WorkflowControl(cancelRequested={cancel}),onFrame={ first=storage().readPrivate(it.opticalPath);cancel=true }) } }
        val restored=FilmLibrary(index);val old=session(restored).run("roll","old-v7")
        val medium=MediumStore(storage())
        for(result in old) {
            assertFalse((medium.readOptical(result.opticalPath).metadata["film_process_model"] as Map<*,*>).containsKey("independent_silver_grain"))
            assertFalse(medium.readLatent(cameraLatentPath("roll",result.sourceFrameIndex)).metadata.containsKey("phone_layer_exposure"))
        }
        val current=session(restored);val task=current.create("roll","v8",recipe);assertEquals(RollDevelopmentSession.FORMATION_REVISION,task.revision)
        val modern=current.run("roll","v8")
        assertTrue((medium.readOptical(modern[0].opticalPath).metadata["film_process_model"] as Map<*,*>).containsKey("independent_silver_grain"))
        assertFalse(medium.readOptical(old[0].opticalPath).opticalDensityRgb.values.contentEquals(medium.readOptical(modern[0].opticalPath).opticalDensityRgb.values))
        assertArrayEquals(first,storage().readPrivate(old[0].opticalPath))
        Unit
    }

    @Test fun pausedV6RetainsPointwiseLedgerWhileNewV7UsesDirWithoutChangingTheOldOutput() = runBlocking {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index)
        val film=mapOf<String,Any?>("dir_material_response_matrix" to listOf(listOf(0.0,.5,0.0),listOf(0.0,0.0,0.0),listOf(0.0,0.0,0.0)))
        roll(library,2,film)
        val recipe=mapOf("film" to library.roll("roll").filmRecipe,"develop" to mapOf("program_key" to "color_negative"))
        val frames=library.roll("roll").frames.filter { it.state==FrameState.CAPTURED }.map { DevelopmentTaskFrame(it.frameIndex,it.frameId,cameraLatentPath("roll",it.frameIndex),it.capturePath) }
        val disk=PrivateDevelopmentTaskStore(storage());disk.create(DevelopmentTask("v6","roll",recipe,frames,revision="phone_roll_activation_ledger_v6"))
        var cancel=false;var before=byteArrayOf()
        assertThrows(WorkflowException::class.java) { runBlocking { session(library).run("roll","v6",WorkflowControl(cancelRequested={cancel}),onFrame={ before=storage().readPrivate(it.opticalPath);cancel=true }) } }
        val restored=FilmLibrary(index);val old=session(restored).run("roll","v6")
        val medium=MediumStore(storage())
        for(result in old) {
            val process=medium.readOptical(result.opticalPath).metadata["film_process_model"] as Map<*,*>
            assertEquals(PhoneProcessModels.LEDGER,(process["ordered_process"] as Map<*,*>)["model"])
            assertFalse(process.containsKey("spatial_development"))
        }
        val fresh=session(restored);fresh.create("roll","v7",recipe);val modern=fresh.run("roll","v7")
        assertFalse(medium.readOptical(old[0].opticalPath).opticalDensityRgb.values.contentEquals(medium.readOptical(modern[0].opticalPath).opticalDensityRgb.values))
        assertArrayEquals(before,storage().readPrivate(old[0].opticalPath))
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        Unit
    }

    @Test fun pausedV5KeepsItsBathMathAndExposureWhileNewTaskUsesTheActivationLedger() = runBlocking {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index)
        val film=mapOf<String,Any?>("color_process" to "bw","spatial_reference_short_edge_mm" to 24.0,"hd_gamma" to listOf(.62,.62,.62))
        library.loadRoll("roll",12,film,captureSettings=mapOf("format" to "135","material_model" to PhoneProcessModels.PREVIOUS,"spatial_effects_revision" to PhoneMaterialDefaults.REVISION))
        val shoot=capture(library);repeat(2) { shoot.capture("roll",camera) };shoot.seal("roll")
        val recipe=mapOf("film" to library.roll("roll").filmRecipe,"develop" to mapOf("program_key" to "custom","process_steps" to List(2) { mapOf("action" to "develop_silver") }))
        val frames=library.roll("roll").frames.filter { it.state==FrameState.CAPTURED }.map { DevelopmentTaskFrame(it.frameIndex,it.frameId,cameraLatentPath("roll",it.frameIndex),it.capturePath) }
        val disk=PrivateDevelopmentTaskStore(storage());disk.create(DevelopmentTask("old-v5","roll",recipe,frames,revision="phone_roll_material_bath_v5"))
        var cancel=false;var first=byteArrayOf()
        assertThrows(WorkflowException::class.java) { runBlocking {
            session(library,disk).run("roll","old-v5",WorkflowControl(cancelRequested={cancel}),onFrame={ result -> first=storage().readPrivate(result.opticalPath);cancel=true })
        } }
        val restored=FilmLibrary(index);val resumed=session(restored).run("roll","old-v5")
        assertArrayEquals(first,storage().readPrivate(resumed.first().opticalPath))
        for(result in resumed) {
            val master=MediumStore(storage()).readOptical(result.opticalPath)
            val process=(master.metadata["film_process_model"] as Map<*,*>)["ordered_process"] as Map<*,*>
            assertEquals(PhoneProcessModels.PREVIOUS,process["model"]);assertFalse(process.containsKey("activation_ledger"))
            assertEquals("phone_stock_logistic_v1",MediumStore(storage()).readLatent(cameraLatentPath("roll",result.sourceFrameIndex)).metadata["phone_layer_response"])
        }
        val session=session(restored);val modern=session.create("roll","new-v6",recipe)
        assertEquals(RollDevelopmentSession.FORMATION_REVISION,modern.revision)
        val new=session.run("roll","new-v6")
        val master=MediumStore(storage()).readOptical(new.first().opticalPath)
        assertEquals(PhoneProcessModels.CURRENT,((master.metadata["film_process_model"] as Map<*,*>)["ordered_process"] as Map<*,*>)["model"])
        assertFalse(master.opticalDensityRgb.values.contentEquals(MediumStore(storage()).readOptical(resumed.first().opticalPath).opticalDensityRgb.values))
        assertArrayEquals(first,storage().readPrivate(resumed.first().opticalPath))
        Unit
    }

    @Test fun newRollOpticsAreDeferredUntilDevelopmentAndStoredInSourceLatent() = runBlocking {
        val library=FilmLibrary();val film=PhoneMaterialDefaults.withEffects(mapOf("grain_strength" to .025),.3,.25,1.0)
        library.loadRoll("roll",12,film,captureSettings=mapOf("spatial_effects_revision" to PhoneMaterialDefaults.REVISION))
        val shoot=capture(library);shoot.capture("roll",camera)
        assertEquals(0,exposeRuns);assertEquals(0,developRuns)
        shoot.seal("roll");val session=session(library);session.create("roll","spatial",emptyMap())
        val result=session.run("roll","spatial").first()
        val latent=MediumStore(storage()).readLatent(cameraLatentPath("roll",1))
        assertTrue(latent.metadata.containsKey("phone_exposure_optics"));assertTrue(latent.metadata.containsKey("phone_grain_profile"))
        assertEquals(PhoneGrainModels.CURRENT,(latent.metadata["phone_grain_profile"] as Map<*,*>)["model"])
        val optical=MediumStore(storage()).readOptical(result.opticalPath)
        assertTrue((optical.metadata["film_process_model"] as Map<*,*>).containsKey("exposure_optics"))
        assertEquals(FrameState.LATENT,library.roll("roll").frames.first().state)
        Unit
    }

    @Test fun pausedOrderedV3TaskFormsRemainingFramesWithItsHistoricalGrainImplementation() = runBlocking {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index)
        val film=PhoneMaterialDefaults.withEffects(mapOf("grain_strength" to .025),0.0,0.0,1.0)
        library.loadRoll("roll",12,film,captureSettings=mapOf("spatial_effects_revision" to PhoneMaterialDefaults.REVISION))
        val shoot=capture(library);repeat(2) { shoot.capture("roll",camera) };shoot.seal("roll")
        val disk=PrivateDevelopmentTaskStore(storage());val session=session(library,disk)
        val frames=library.roll("roll").frames.filter { it.state==FrameState.CAPTURED }.map {
            DevelopmentTaskFrame(it.frameIndex,it.frameId,cameraLatentPath("roll",it.frameIndex),it.capturePath)
        }
        disk.create(DevelopmentTask("old-ordered","roll",mapOf("film" to library.roll("roll").filmRecipe,"develop" to mapOf("program_key" to "auto")),frames,revision="phone_roll_ordered_process_v3"))
        var cancelled=false;var first=byteArrayOf()
        assertThrows(WorkflowException::class.java) { runBlocking {
            session.run("roll","old-ordered",WorkflowControl(cancelRequested={cancelled}),onFrame={ result->
                first=storage().readPrivate(result.opticalPath);cancelled=true
            })
        } }
        assertFalse(storage().existsPrivate(cameraLatentPath("roll",2)))
        val results=session(FilmLibrary(index)).run("roll","old-ordered")
        assertArrayEquals(first,storage().readPrivate(results.first().opticalPath))
        for(frame in 1..2) {
            val latent=MediumStore(storage()).readLatent(cameraLatentPath("roll",frame))
            assertEquals(PhoneGrainModels.PREVIOUS,(latent.metadata["phone_grain_profile"] as Map<*,*>)["model"])
            val optical=MediumStore(storage()).readOptical(results[frame-1].opticalPath)
            val effects=(optical.metadata["film_process_model"] as Map<*,*>)["phone_material_effects"] as Map<*,*>
            assertEquals(PhoneGrainModels.PREVIOUS,effects["grain"])
        }
        Unit
    }

    @Test fun legacyTasksKeepOmittedEffectsWhileNewBatchesUseMaterialEffects() = runBlocking {
        val library=FilmLibrary();library.loadRoll("roll",12,mapOf("grain_strength" to .08))
        val shoot=capture(library);shoot.capture("roll",camera);shoot.seal("roll")
        val disk=PrivateDevelopmentTaskStore(storage())
        val frame=library.roll("roll").frames.first()
        val recipe=mapOf("film" to library.roll("roll").filmRecipe,"develop" to mapOf("chemical_stain" to .5))
        disk.create(DevelopmentTask("legacy","roll",recipe,listOf(DevelopmentTaskFrame(1,frame.frameId,cameraLatentPath("roll",1),frame.capturePath))))
        val old=session(library).run("roll","legacy").first()
        val oldOptical=MediumStore(storage()).readOptical(old.opticalPath)
        val oldProcess=oldOptical.metadata["film_process_model"] as Map<*,*>
        assertFalse(oldProcess.containsKey("phone_material_effects"))
        val session=session(library);assertEquals(RollDevelopmentSession.FORMATION_REVISION,session.create("roll","new",recipe).revision)
        val new=session.run("roll","new").first();val master=MediumStore(storage()).readOptical(new.opticalPath)
        assertTrue((master.metadata["film_process_model"] as Map<*,*>).containsKey("phone_material_effects"))
        assertFalse(master.opticalDensityRgb.values.contentEquals(oldOptical.opticalDensityRgb.values))
        Unit
    }

    @Test fun pipelinePublishesOneFrameBeforeFormingTheNextAndResumesFrozenRecipeAfterRestart() = runBlocking {
        val index=MemoryLibraryIndexStore();val library=FilmLibrary(index);roll(library)
        val session=session(library);val process=mutableMapOf<String,Any?>("time_min" to 2)
        session.create("roll","job",mapOf("develop" to process));process["time_min"]=12
        var cancelled=false;var firstBytes=byteArrayOf()
        assertThrows(WorkflowException::class.java) { runBlocking {
            session.run("roll","job",WorkflowControl(cancelRequested={cancelled}),onFrame={ result ->
                assertEquals(1,result.sourceFrameIndex)
                assertEquals(FrameState.CAPTURED,library.roll("roll").frames[1].state)
                assertFalse(Files.exists(root.resolve(cameraLatentPath("roll",2))))
                firstBytes=Files.readAllBytes(root.resolve(result.opticalPath));cancelled=true
            })
        } }
        assertEquals("paused",session.latest("roll")!!.status)
        assertEquals(1,exposeRuns);assertEquals(1,developRuns)
        val resumed=session(FilmLibrary(index));val results=resumed.run("roll","job")
        assertEquals(2,results.size);assertEquals(2,exposeRuns);assertEquals(2,developRuns)
        assertEquals("completed",resumed.latest("roll")!!.status)
        assertEquals(2,((results[1].developRecipe["develop"] as Map<*,*>)["time_min"] as Number).toInt())
        assertArrayEquals(firstBytes,Files.readAllBytes(root.resolve(results[0].opticalPath)))
        val medium=MediumStore(storage())
        assertArrayEquals(medium.readOptical(results[0].opticalPath).opticalDensityRgb.values,medium.readOptical(results[1].opticalPath).opticalDensityRgb.values)
        assertArrayEquals(byteArrayOf(1,2,3),Files.readAllBytes(root.resolve("roll/frame_01.capture/exposure_000001/source.jpg")))
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        val again=resumed.run("roll","job");assertEquals(2,again.size);assertEquals(2,developRuns)
    }

    @Test fun completedUnindexedOpticalIsVerifiedAndRegisteredWithoutRecomputation() = runBlocking {
        val saved=MemoryLibraryIndexStore();var fail=false
        val index=object: LibraryIndexStore by saved {
            override fun writeIndex(bytes: ByteArray) { if(fail) throw IOException("index unavailable");saved.writeIndex(bytes) }
        }
        val library=FilmLibrary(index);roll(library,1,mapOf("contrast" to 1.0,"grain_strength" to 0.0));val taskSession=session(library)
        val task=taskSession.create("roll","recover",mapOf("develop" to mapOf("time_min" to 2)))
        assertThrows(IOException::class.java) { runBlocking { taskSession.run("roll","recover",onProgress={ if(it.phase == "developing") fail=true }) } }
        val output=root.resolve("results/recover-1.darkroom_negative.npz");val before=Files.readAllBytes(output)
        assertEquals(0,library.developedResults().size);assertEquals(1,developRuns)
        val medium=MediumStore(storage());val record=DevelopmentTaskCodec.provenance(task,task.frames[0])
        assertThrows(CaptureRejected::class.java) { medium.hasRecoverableTaskOptical("results/recover-1.darkroom_negative.npz",record+mapOf("task_id" to "wrong"),WorkflowControl()) }
        assertThrows(CaptureRejected::class.java) { medium.hasRecoverableTaskOptical("results/recover-1.darkroom_negative.npz",record+mapOf("recipe" to mapOf("develop" to mapOf("time_min" to 12))),WorkflowControl()) }
        fail=false
        val resumed=session(FilmLibrary(index));resumed.run("roll","recover")
        assertEquals(1,developRuns);assertEquals(1,exposeRuns);assertArrayEquals(before,Files.readAllBytes(output))
        assertEquals("completed",resumed.latest("roll")!!.status)
        System.getenv("FILM_FOUNDRY_TEST_ARTIFACT_DIR")?.let { directory ->
            val target=Paths.get(directory);Files.createDirectories(target)
            Files.copy(output,target.resolve("task-proof.darkroom_negative.npz"),StandardCopyOption.REPLACE_EXISTING)
        }
        Unit // JUnit must discover the recovery case even when an optional fixture is copied.
    }

    @Test fun failedTaskCheckpointAfterLibraryCommitDoesNotRepeatCompletedWork() = runBlocking {
        val saved=MemoryLibraryIndexStore();val library=FilmLibrary(saved);roll(library,1)
        val disk=PrivateDevelopmentTaskStore(storage());var fail=true
        val bad=object: DevelopmentTaskStore by disk {
            override fun save(task: DevelopmentTask) {
                if(fail && task.frames.any { it.phase == "registered" }) throw IOException("checkpoint unavailable")
                disk.save(task)
            }
        }
        val session=session(library,bad);session.create("roll","checkpoint",emptyMap())
        assertThrows(IOException::class.java) { runBlocking { session.run("roll","checkpoint") } }
        assertEquals(1,library.developedResults().size);assertEquals(1,developRuns)
        val bytes=Files.readAllBytes(root.resolve("results/checkpoint-1.darkroom_negative.npz"))
        fail=false;session(FilmLibrary(saved)).run("roll","checkpoint")
        assertEquals(1,developRuns);assertArrayEquals(bytes,Files.readAllBytes(root.resolve("results/checkpoint-1.darkroom_negative.npz")))
    }

    @Test fun oldOrCorruptUnindexedOutputIsPreservedAndCannotBeGuessedIntoTheTask() = runBlocking {
        val library=FilmLibrary();roll(library,1);val session=session(library);session.create("roll","foreign",emptyMap())
        val target=root.resolve("results/foreign-1.darkroom_negative.npz");Files.createDirectories(target.parent);Files.write(target,byteArrayOf(9,8,7))
        assertThrows(CaptureRejected::class.java) { runBlocking { session.run("roll","foreign") } }
        assertArrayEquals(byteArrayOf(9,8,7),Files.readAllBytes(target));assertEquals(0,developRuns)
        assertEquals("failed",session.latest("roll")!!.status)
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.create("roll","other",emptyMap()) } }
        session.abandon("roll","foreign");session.create("roll","other",emptyMap());session.run("roll","other")
        assertEquals(1,developRuns);assertArrayEquals(byteArrayOf(9,8,7),Files.readAllBytes(target))
    }

    @Test fun activeTaskPreventsAnotherTaskAndSourceDiscardAndReleasesItsLease() = runBlocking {
        val library=FilmLibrary();roll(library,1);val entered=CompletableDeferred<Unit>();val release=CompletableDeferred<Unit>()
        val engine=object: WorkflowFacade {
            override suspend fun run(request: WorkflowRequest): WorkflowResult {
                if(request.command == Command.DEVELOP_LATENT) { entered.complete(Unit);release.await() }
                return facade.run(request)
            }
        }
        val session=session(library,engine=engine);session.create("roll","active",emptyMap())
        val work=async { session.run("roll","active") };entered.await()
        assertThrows(IllegalArgumentException::class.java) { library.discardRoll("roll") }
        assertThrows(IllegalArgumentException::class.java) { runBlocking { session.run("roll","active") } }
        release.complete(Unit);work.await()
        session.create("roll","next",emptyMap())
        assertEquals("next",session.latest("roll")!!.id)
    }

    @Test fun taskMustBeSavedBeforeAnyFormationAndCannotChangeItsRecipe() = runBlocking {
        val library=FilmLibrary();roll(library,1);val disk=PrivateDevelopmentTaskStore(storage())
        val bad=object: DevelopmentTaskStore by disk { override fun create(task: DevelopmentTask) { throw IOException("no space") } }
        assertThrows(IOException::class.java) { runBlocking { session(library,bad).create("roll","unsaved",emptyMap()) } }
        assertEquals(0,exposeRuns);assertEquals(FrameState.CAPTURED,library.roll("roll").frames[0].state)
        val session=session(library);val task=session.create("roll","saved",emptyMap())
        assertThrows(IllegalArgumentException::class.java) { disk.save(task.copy(recipe=mapOf("changed" to true))) }
        assertEquals(task,session.latest("roll"))
    }

    private fun storage(): StorageAdapter=object: StorageAdapter by MemoryStorageAdapter() {
        override fun existsPrivate(relative: String)=Files.isRegularFile(root.resolve(relative))
        override fun rowScratchRoot()=root.resolve("scratch").toFile()
        override fun privateVersion(relative: String): RowMediumVersion?=root.resolve(relative).toFile().let {
            if(it.isFile) RowMediumVersion(it.canonicalPath,it.length(),it.lastModified()) else null
        }
        override fun samePrivateLocation(a: String,b: String)=root.resolve(a).normalize()==root.resolve(b).normalize()
        override fun readPrivate(relative: String)=root.resolve(relative).toFile().readBytes()
        override fun writePrivate(relative: String,bytes: ByteArray)=writePrivate(relative) { it.write(bytes) }
        override fun <T> readPrivate(relative: String,reader: (InputStream)->T)=root.resolve(relative).toFile().inputStream().use(reader)
        override fun writePrivate(relative: String,writer: (OutputStream)->Unit)=writePrivate(relative,{},writer)
        override fun writePrivate(relative: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) {
            val path=root.resolve(relative);Files.createDirectories(path.parent);val pending=Files.createTempFile(path.parent,"pending-",".tmp")
            try { pending.toFile().outputStream().use(writer);beforeCommit();Files.move(pending,path,StandardCopyOption.ATOMIC_MOVE,StandardCopyOption.REPLACE_EXISTING) }
            finally { Files.deleteIfExists(pending) }
        }
    }
}
