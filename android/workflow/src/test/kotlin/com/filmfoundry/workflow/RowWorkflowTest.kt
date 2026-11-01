package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class RowWorkflowTest {
    @Test fun opticalHandoffChecksIdentityAndCleansUnconsumedOrCancelledRows() = runBlocking {
        val source="optical-source.darkroom_latent.npz";val target="optical-target.darkroom_negative.npz"
        facade.run(WorkflowRequests.expose(source,spectralRecipe(),31,23,FloatArray(31*23*3){(it%27)/28f},rowIo=io))
        val original=Files.readAllBytes(root.resolve(source))
        for(mode in listOf("reuse","changed","other_io","unversioned","missing","cancel","unused")) {
            val disk=if(mode=="unversioned") object: RowMediumIo by io { override fun version(path: String): RowMediumVersion?=null } else io
            RowMediumHandoff().use { handoff ->
                facade.run(WorkflowRequests.developLatent(source,target,emptyMap(),rowIo=disk).copy(opticalHandoff=handoff))
                val saved=Files.readAllBytes(root.resolve(target))
                if(mode=="unused") return@use
                if(mode=="changed") versionOffset++
                if(mode=="missing") Files.delete(root.resolve(target))
                val times=mutableListOf<String>()
                var rendered=FloatArray(0)
                val output=object: RowObservationSession.Output {
                    override fun begin(width: Int,height: Int) { rendered=FloatArray(width*height*3) }
                    override fun rows(start: Int,count: Int,rgb: FloatArray) { rgb.copyInto(rendered,start*31*3) }
                }
                val control=WorkflowControl(cancelRequested={mode=="cancel"},onTiming={c,s,_->times.add("$c.$s")})
                val action={ RowObservationSession.scan(if(mode=="other_io") io else disk,target,emptyMap(),output,control,handoff=handoff) }
                when(mode) {
                    "missing"->assertThrows(FileNotFoundException::class.java) { action() }
                    "cancel"->assertThrows(WorkflowException::class.java) { action() }
                    else->{
                        action();val actual=rendered.copyOf()
                        RowObservationSession.scan(disk,target,emptyMap(),output)
                        assertArrayEquals(actual,rendered,0f)
                        assertEquals(mode=="reuse","scan.handoff_hit" in times)
                        assertEquals(mode!="reuse","scan.archive_read" in times)
                    }
                }
                if(mode!="missing") assertArrayEquals(saved,Files.readAllBytes(root.resolve(target)))
            }
            assertTrue(root.resolve("scratch").toFile().listFiles().orEmpty().isEmpty())
        }
        assertArrayEquals(original,Files.readAllBytes(root.resolve(source)))
    }
    @Test fun formationDevelopmentAndScanRepresentativeTiming() = runBlocking {
        org.junit.jupiter.api.Assumptions.assumeTrue(System.getenv("FF_PIPELINE_TIMING")=="1")
        val width=768;val height=512
        val film=PhoneMaterialDefaults.withEffects(spectralRecipe()["film"] as Map<String,Any?>,.3,.4,1.0)
        val recipe=mapOf("film" to film,"develop" to mapOf("program_key" to "color_negative","uneven_development" to .2),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.LAYERED,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val rgb=FloatArray(width*height*3) { (it%997+.5f)/997f }
        val times=linkedMapOf<String,MutableList<Double>>()
        repeat(5) { pass ->
          for(direct in if(pass%2==0) listOf(false,true) else listOf(true,false)) {
            val measured=linkedMapOf<String,Double>()
            suspend fun measure(label: String,work: suspend ()->Unit) { val begin=System.nanoTime();work();measured[label]=(System.nanoTime()-begin)/1e6 }
            val phases=linkedMapOf<String,Double>()
            val control=WorkflowControl(onTiming={ command,phase,ns -> val key="$command.$phase";phases[key]=(phases[key] ?: 0.0)+ns/1e6 })
            val rawIo=io
            val disk=object: RowMediumIo by rawIo {
                override fun <T> read(path: String,reader: (InputStream)->T)=rawIo.read(path) { reader(it.buffered(65536)) }
                override fun write(path: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit)=rawIo.write(path,beforeCommit) {
                    val buffered=it.buffered(65536);writer(buffered);buffered.flush()
                }
            }
            RowMediumHandoff().use { handoff ->
                measure("formation") { facade.run(WorkflowRequests.expose("bench.darkroom_latent.npz",recipe,width,height,rgb,rowIo=disk,control=control).copy(rowHandoff=handoff)) }
                measure("development") { facade.run(WorkflowRequests.developLatent("bench.darkroom_latent.npz","bench.darkroom_negative.npz",recipe,rowIo=disk,control=control).copy(rowHandoff=handoff,rowWorkers=2,opticalHandoff=handoff.takeIf { direct })) }
                measure("scan") {
                    var rows=0
                    RowObservationSession.scan(disk,"bench.darkroom_negative.npz",emptyMap(),object: RowObservationSession.Output {
                        override fun begin(w: Int,h: Int) { assertEquals(width,w);assertEquals(height,h) }
                        override fun rows(start: Int,count: Int,rgb: FloatArray) { assertEquals(rows,start);rows+=count }
                    },control,handoff=handoff.takeIf { direct })
                    assertEquals(height,rows)
                }
            }
            measured["total"]=measured.values.sum()
            if(pass>=2) for((key,ms) in measured+phases) times.getOrPut((if(direct) "direct." else "archive.")+key){mutableListOf()}.add(ms)
            assertTrue(root.resolve("scratch").toFile().listFiles().orEmpty().isEmpty())
        }
        }
        times.forEach { (key,ms)->println("synthetic_768x512_$key ms=$ms") }
    }
    @Test fun parallelDevelopmentRepresentativeTiming() = runBlocking {
        org.junit.jupiter.api.Assumptions.assumeTrue(System.getenv("FF_KERNEL_TIMING")=="1")
        val width=768;val height=512
        val film=PhoneMaterialDefaults.withEffects(spectralRecipe()["film"] as Map<String,Any?>,.3,.4,1.0)
        val recipe=mapOf("film" to film,"develop" to mapOf("program_key" to "color_negative","uneven_development" to .2),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.LAYERED,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val path="timing"+DataKernel.LATENT_SUFFIX
        facade.run(WorkflowRequests.expose(path,recipe,width,height,FloatArray(width*height*3) { (it%997+.5f)/997f },rowIo=io))
        val totals=Array(2) { mutableListOf<Double>() };val pipelines=Array(2) { mutableListOf<Double>() }
        repeat(5) { pass ->
            for(mode in if(pass%2==0) listOf(0,1) else listOf(1,0)) {
                var pipeline=0L
                val control=WorkflowControl(onTiming={ _,section,ns -> if(section=="row_pipeline_wall") pipeline+=ns })
                val request=WorkflowRequests.developLatent(path,"timed"+DataKernel.NEGATIVE_SUFFIX,recipe,rowIo=io,control=control)
                    .copy(rowBlockPixels=width*24,rowWorkers=mode+1)
                val started=System.nanoTime();facade.run(request)
                if(pass>=2) { totals[mode]+=(System.nanoTime()-started)/1e6;pipelines[mode]+=pipeline/1e6 }
            }
        }
        println("synthetic_768x512_serial_total_ms=${totals[0]} two_workers_total_ms=${totals[1]}")
        println("synthetic_768x512_serial_row_ms=${pipelines[0]} two_workers_row_ms=${pipelines[1]}")
    }
    @Test fun twoWorkersKeepSpectralGrainIncidentsAndCrossProcessingIdenticalToSerialRows() = runBlocking {
        val width=61;val height=37
        val recipe=mapOf("film" to (PhoneMaterialDefaults.withEffects(spectralRecipe()["film"] as Map<String,Any?>,.3,.4,1.0)+mapOf("silver_grain_strength" to .1,"silver_grain_radius" to .02)),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneGrainModels.EXPOSURE_OPTION to PhoneGrainModels.LAYERED,
            PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val path="parallel"+DataKernel.LATENT_SUFFIX
        facade.run(WorkflowRequests.expose(path,recipe,width,height,FloatArray(width*height*3) { (it%313+.5f)/313f },rowIo=io).copy(rowBlockPixels=width*5))
        val original=Files.readAllBytes(root.resolve(path))
        for(program in listOf("color_negative","color_reversal","color_negative_bleach_bypass","bw_negative")) {
            val process=recipe+("develop" to mapOf("program_key" to program,"uneven_development" to .3,"chemical_stain" to .2,"development_adjacency_strength" to .2))
            val request=WorkflowRequests.developLatent(path,"serial"+DataKernel.NEGATIVE_SUFFIX,process,rowIo=io).copy(rowBlockPixels=width*6)
            facade.run(request)
            facade.run(request.copy(output=request.output.copy(path="parallel"+DataKernel.NEGATIVE_SUFFIX),rowWorkers=2))
            val serial=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("serial"+DataKernel.NEGATIVE_SUFFIX)))
            val parallel=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("parallel"+DataKernel.NEGATIVE_SUFFIX)))
            assertEquals(serial.metadata,parallel.metadata)
            assertArrayEquals(serial.opticalDensityRgb.values,parallel.opticalDensityRgb.values,0f,program)
            assertArrayEquals(serial.spectralComponentAmounts!!.values,parallel.spectralComponentAmounts!!.values,0f,program)
            assertArrayEquals(serial.spectralPostProcessDensityRgb!!.values,parallel.spectralPostProcessDensityRgb!!.values,0f,program)
        }
        val cancelled=java.util.concurrent.atomic.AtomicBoolean();var completed=0
        val cancelControl=WorkflowControl(cancelRequested=cancelled::get,onTiming={ _,section,_ -> if(section=="material_compute" && ++completed>=3) cancelled.set(true) })
        val preserved=byteArrayOf(7,3,1);Files.write(root.resolve("cancel"+DataKernel.NEGATIVE_SUFFIX),preserved)
        assertThrows(WorkflowException::class.java) { runBlocking {
            facade.run(WorkflowRequests.developLatent(path,"cancel"+DataKernel.NEGATIVE_SUFFIX,recipe,rowIo=io,control=cancelControl)
                .copy(rowBlockPixels=width*6,rowWorkers=2))
        } }
        assertArrayEquals(preserved,Files.readAllBytes(root.resolve("cancel"+DataKernel.NEGATIVE_SUFFIX)))
        assertArrayEquals(original,Files.readAllBytes(root.resolve(path)))
        assertTrue(root.resolve("scratch").toFile().listFiles().orEmpty().isEmpty())
    }
    @TempDir lateinit var root: Path
    private val codec = NpzMediumArchive()
    private val facade = DefaultWorkflowFacade()
    private var cancelBeforeCommit = false
    private var latentReads=0
    private var versionOffset=0L
    private val io get() = object : RowMediumIo {
        override val scratchRoot = root.resolve("scratch").toFile()
        override fun <T> read(path: String, reader: (InputStream) -> T): T {
            if(path.endsWith(DataKernel.LATENT_SUFFIX)) latentReads++
            return root.resolve(path).toFile().inputStream().use(reader)
        }
        override fun version(path: String): RowMediumVersion? = root.resolve(path).toFile().let {
            if(it.isFile) RowMediumVersion(it.canonicalPath,it.length(),it.lastModified()+versionOffset) else null
        }
        override fun write(path: String, beforeCommit: () -> Unit, writer: (OutputStream) -> Unit) {
            val target = root.resolve(path)
            val pending = Files.createTempFile(root, "pending-", ".tmp")
            try {
                pending.toFile().outputStream().use(writer)
                if (cancelBeforeCommit) throw WorkflowException("cancelled", "cancelled before commit")
                beforeCommit()
                Files.move(pending, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            } finally { Files.deleteIfExists(pending) }
        }
        override fun sameLocation(a: String, b: String) = root.resolve(a).normalize() == root.resolve(b).normalize()
    }

    private fun spectralRecipe(): Map<String, Any?> {
        val wave = listOf(400, 550, 700)
        val material = mapOf("model" to "normalized_pool_absorbance_v1", "profile_id" to "row_fixture",
            "coefficient_unit" to "normalized_final_pool_v1", "component_names" to SpectralOptics.components,
            "wavelengths_nm" to wave, "component_absorbance" to List(9) { k -> List(3) { n -> .05 + k * .02 + n * .03 } })
        val exposure = mapOf("model" to DataKernel.SPECTRAL_EXPOSURE_MODEL_ID, "profile_id" to "row_exposure",
            "working_space" to DataKernel.CAPTURE_WORKING_SPACE, "wavelengths_nm" to wave,
            "rgb_basis" to List(3) { List(3) { .003 } }, "layer_sensitivity" to List(3) { List(3) { 1.0 } }, "layer_gain" to listOf(1, 1.2, .8))
        return mapOf("film" to mapOf("image_polarity" to "positive", "spatial_reference_short_edge_mm" to 24,
            SpectralOptics.MATERIAL_KEY to material, DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY to exposure))
    }
    @Test fun committedLatentHandoffMatchesArchivedSpectralDevelopmentWithoutReopeningTheSource() = runBlocking {
        val w=31;val h=23;val disk=io
        val stock=PhoneMaterialDefaults.withEffects(spectralRecipe()["film"] as Map<String,Any?>,.3,.4,1.0)
        val recipe=mapOf("film" to stock,"develop" to mapOf("program_key" to "color_negative_bleach_bypass"),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val input="handoff.darkroom_latent.npz"
        RowMediumHandoff().use { handoff ->
            facade.run(WorkflowRequests.expose(input,recipe,w,h,FloatArray(w*h*3) { (it%43+.5f)/43f },
                frameIdentity=FrameIdentity("one","frame",1),rowIo=disk).copy(rowBlockPixels=w*4,rowHandoff=handoff))
            val original=Files.readAllBytes(root.resolve(input))
            assertTrue(root.resolve("scratch").toFile().listFiles()!!.isNotEmpty())
            val request=WorkflowRequests.developLatent(input,"fast.darkroom_negative.npz",recipe,rowIo=disk).copy(rowBlockPixels=w*3,rowHandoff=handoff)
            facade.run(request)
            assertEquals(0,latentReads)
            facade.run(request.copy(output=MediumRef("reference.darkroom_negative.npz"))) // lease is consumed once
            assertEquals(1,latentReads)
            val a=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("fast.darkroom_negative.npz")))
            val b=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("reference.darkroom_negative.npz")))
            assertEquals(b.metadata,a.metadata)
            assertArrayEquals(b.opticalDensityRgb.values,a.opticalDensityRgb.values,0f)
            assertArrayEquals(b.clearBaseOpticalDensityRgb!!,a.clearBaseOpticalDensityRgb!!,0f)
            assertArrayEquals(b.spectralComponentAmounts!!.values,a.spectralComponentAmounts!!.values,0f)
            if(b.spectralPostProcessDensityRgb==null) assertNull(a.spectralPostProcessDensityRgb)
            else assertArrayEquals(b.spectralPostProcessDensityRgb!!.values,a.spectralPostProcessDensityRgb!!.values,0f)
            assertArrayEquals(original,Files.readAllBytes(root.resolve(input)))
        }
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }
    @Test fun changedMissingOrUnversionedSourcesUseTheArchiveRatherThanStaleRows() = runBlocking {
        val disk=io;val recipe=spectralRecipe();val input="invalidated.darkroom_latent.npz"
        for(mode in listOf("changed","missing","otherIo","unversioned")) {
            val adapter=if(mode=="unversioned") object: RowMediumIo by disk { override fun version(path: String): RowMediumVersion?=null } else disk
            RowMediumHandoff().use { handoff ->
                facade.run(WorkflowRequests.expose(input,recipe,5,7,FloatArray(5*7*3){.2f},rowIo=adapter).copy(rowHandoff=handoff))
                if(mode=="changed") versionOffset++
                if(mode=="missing") Files.delete(root.resolve(input))
                val reading=if(mode=="otherIo") io else adapter
                val before=latentReads
                val request=WorkflowRequests.developLatent(input,"$mode.darkroom_negative.npz",recipe,rowIo=reading).copy(rowHandoff=handoff)
                if(mode=="missing") assertThrows(WorkflowException::class.java) { runBlocking { facade.run(request) } }
                else facade.run(request)
                assertEquals(before+1,latentReads)
            }
            assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        }
    }
    @Test fun cancelledCommitAndUnusedCommittedHandoffBothReleaseScratchWithoutLosingTheLatent() = runBlocking {
        val disk=io;val input="cancel-handoff.darkroom_latent.npz"
        val request=WorkflowRequests.expose(input,spectralRecipe(),5,7,FloatArray(5*7*3){.2f},rowIo=disk)
        cancelBeforeCommit=true
        RowMediumHandoff().use { handoff ->
            assertThrows(WorkflowException::class.java) { runBlocking { facade.run(request.copy(rowHandoff=handoff)) } }
        }
        assertFalse(Files.exists(root.resolve(input)));assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        cancelBeforeCommit=false
        RowMediumHandoff().use { handoff -> facade.run(request.copy(rowHandoff=handoff)) }
        assertTrue(Files.exists(root.resolve(input)));codec.readLatent(Files.readAllBytes(root.resolve(input)))
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }
    @Test fun timingDiagnosticsStayOutsideArchivesAndRetainAtomicCancellation() = runBlocking {
        val broken=WorkflowControl(onTiming={ _,_,_ -> error("diagnostic unavailable") })
        assertEquals(42,broken.measured("expose","setup") { 42 })
        assertThrows(WorkflowException::class.java) { broken.measured("expose","setup") { throw WorkflowException("cancelled","cancelled") } }
        val w=7;val h=5;val recipe=spectralRecipe()
        val timings=mutableListOf<Triple<String,String,Long>>()
        val control=WorkflowControl(onTiming={ command,section,nanos -> timings.add(Triple(command,section,nanos)) })
        val request=WorkflowRequests.expose("timed.darkroom_latent.npz",recipe,w,h,FloatArray(w*h*3) { .18f },rowIo=io,control=control)
        facade.run(request)
        val original=root.resolve("timed.darkroom_latent.npz").toFile().readBytes()
        facade.run(WorkflowRequests.developLatent("timed.darkroom_latent.npz","timed.darkroom_negative.npz",recipe,rowIo=io,control=control))
        facade.run(WorkflowRequests.developLatent("timed.darkroom_latent.npz","plain.darkroom_negative.npz",recipe,rowIo=io))
        val measured=codec.readDevelopedOptical(root.resolve("timed.darkroom_negative.npz").toFile().readBytes())
        val plain=codec.readDevelopedOptical(root.resolve("plain.darkroom_negative.npz").toFile().readBytes())
        assertEquals(plain.metadata,measured.metadata)
        assertArrayEquals(plain.opticalDensityRgb.values,measured.opticalDensityRgb.values,0f)
        assertArrayEquals(plain.spectralComponentAmounts!!.values,measured.spectralComponentAmounts!!.values,0f)
        for(command in listOf("expose","develop_latent")) {
            assertTrue(timings.any { it.first==command && it.second=="archive_pack" })
            assertTrue(timings.any { it.first==command && it.second=="material_compute" })
        }
        assertTrue(timings.all { it.third>=0 })
        cancelBeforeCommit=true
        assertThrows(WorkflowException::class.java) { runBlocking {
            facade.run(WorkflowRequests.developLatent("timed.darkroom_latent.npz","cancel.darkroom_negative.npz",recipe,rowIo=io,control=control))
        } }
        assertArrayEquals(original,root.resolve("timed.darkroom_latent.npz").toFile().readBytes())
        assertFalse(root.resolve("cancel.darkroom_negative.npz").toFile().exists())
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }
    @Test fun layerExposureAndConservativeSilverReachRealRowArchivesWithoutChangingTheInput() = runBlocking {
        val w=47;val h=29
        val film=(spectralRecipe()["film"] as Map<String,Any?>)+mapOf("light_piping_strength" to .1,"light_piping_edge_mode" to "long_edges",
            "halation_return_model" to "layer_selective","halation_strength" to .5,"emulsion_highlight_spread_strength" to .4,
            "silver_grain_strength" to .1,"silver_grain_radius" to .02)
        val r=mapOf("film" to film,"develop" to mapOf("program_key" to "color_negative_bleach_bypass"),PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneMaterialPhenomena.EXPOSURE_OPTION to PhoneMaterialPhenomena.CURRENT,PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT)
        val pixels=FloatArray(w*h*3) { if(it/3==(h/2)*w+w/2) 20f else .05f }
        val exposure=WorkflowRequests.expose("layer.darkroom_latent.npz",r,w,h,pixels)
        val whole=facade.run(exposure).requireLatentArchive()
        facade.run(exposure.copy(rowIo=io,rowBlockPixels=w*5))
        val bytes=Files.readAllBytes(root.resolve("layer.darkroom_latent.npz"));val actual=codec.readLatent(bytes)
        whole.arrays.forEach { (key,v)->assertArrayEquals(v.values,actual.arrays.getValue(key).values,0f) }
        val expected=facade.run(WorkflowRequests.developLatent("layer.darkroom_latent.npz","unused.darkroom_negative.npz",r,actual)).requireOpticalArchive()
        facade.run(WorkflowRequests.developLatent("layer.darkroom_latent.npz","layer.darkroom_negative.npz",r,rowIo=io).copy(rowBlockPixels=w*4))
        val optical=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("layer.darkroom_negative.npz")))
        assertArrayEquals(expected.opticalDensityRgb.values,optical.opticalDensityRgb.values,0f)
        assertArrayEquals(expected.spectralComponentAmounts!!.values,optical.spectralComponentAmounts!!.values,0f)
        assertEquals(codec.readDevelopedOptical(codec.writeDevelopedOptical(expected)).metadata,optical.metadata)
        assertArrayEquals(bytes,Files.readAllBytes(root.resolve("layer.darkroom_latent.npz")))
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
    }

    @Test fun spatialDevelopmentUsesFullLatentBindingAndMatchesWholeSpectralAmounts() = runBlocking {
        val w=41;val h=23
        val stock=(spectralRecipe()["film"] as Map<String,Any?>)+mapOf("grain_strength" to .025,
            "dir_material_response_matrix" to listOf(listOf(0.0,.45,0.0),listOf(0.0,0.0,.2),listOf(.15,0.0,0.0)))
        val recipe=mapOf("film" to stock,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            "develop" to mapOf("program_key" to "color_negative","development_adjacency_strength" to .65))
        val input="spatial-process.darkroom_latent.npz"
        facade.run(WorkflowRequests.expose(input,recipe,w,h,FloatArray(w*h*3) { if(it/3%w<w/2) .02f else .8f },rowIo=io))
        val source=Files.readAllBytes(root.resolve(input));val latent=codec.readLatent(source)
        val expected=facade.run(WorkflowRequests.developLatent(input,"unused.darkroom_negative.npz",recipe,latent)).requireOpticalArchive()
        val metadata=codec.readDevelopedOptical(codec.writeDevelopedOptical(expected)).metadata
        for(budget in listOf(w,w*7)) {
            facade.run(WorkflowRequests.developLatent(input,"spatial-process.darkroom_negative.npz",recipe,rowIo=io).copy(rowBlockPixels=budget))
            val actual=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("spatial-process.darkroom_negative.npz")))
            assertEquals(metadata,actual.metadata)
            assertArrayEquals(expected.opticalDensityRgb.values,actual.opticalDensityRgb.values,0f)
            assertArrayEquals(expected.spectralComponentAmounts!!.values,actual.spectralComponentAmounts!!.values,0f)
            assertArrayEquals(source,Files.readAllBytes(root.resolve(input)))
            assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        }
    }

    @Test fun spatialExposureAndMultiscaleGrainUseRealRowArchivesAndCancelDuringPrepass() = runBlocking {
        val w=61;val h=43
        val film=PhoneMaterialDefaults.withEffects(mapOf("grain_strength" to .03),.4,.5,1.0)
        val recipe=mapOf("film" to film)
        val pixels=FloatArray(w*h*3) { if(it/3==(h/2)*w+w/2) 12f else .08f }
        val identity=FrameIdentity("spatial-roll",frameIndex=1)
        val whole=facade.run(WorkflowRequests.expose("unused.darkroom_latent.npz",recipe,w,h,pixels,frameIdentity=identity)).requireLatentArchive()
        for(budget in listOf(w,w*9)) {
            facade.run(WorkflowRequests.expose("spatial.darkroom_latent.npz",recipe,w,h,pixels,frameIdentity=identity,rowIo=io).copy(rowBlockPixels=budget))
            val actual=root.resolve("spatial.darkroom_latent.npz").toFile().inputStream().use { codec.readLatent(it) }
            assertEquals(codec.readLatent(codec.writeLatent(whole)).metadata,actual.metadata)
            for(key in whole.arrays.keys) assertArrayEquals(whole.arrays.getValue(key).values,actual.arrays.getValue(key).values,0f)
            val expected=facade.run(WorkflowRequests.developLatent("spatial.darkroom_latent.npz","unused.darkroom_negative.npz",recipe,actual)).requireOpticalArchive()
            facade.run(WorkflowRequests.developLatent("spatial.darkroom_latent.npz","spatial.darkroom_negative.npz",recipe,rowIo=io).copy(rowBlockPixels=budget))
            val optical=root.resolve("spatial.darkroom_negative.npz").toFile().inputStream().use { codec.readDevelopedOptical(it) }
            assertArrayEquals(expected.opticalDensityRgb.values,optical.opticalDensityRgb.values,0f)
        }
        var reads=0
        val control=WorkflowControl(cancelRequested={reads>=3})
        val source=object: RgbRows {
            override val width=w;override val height=h
            override fun readRows(start: Int,count: Int): RgbVolume { reads++;return RgbVolume(w,count,linearRgb=pixels.copyOfRange(start*w*3,(start+count)*w*3)) }
        }
        val error=assertThrows(WorkflowException::class.java) { runBlocking {
            facade.run(WorkflowRequests.expose("cancelled.darkroom_latent.npz",recipe,w,h,pixels,rowIo=io,control=control).copy(rgbRows=source))
        } }
        assertEquals("cancelled",error.code);assertFalse(Files.exists(root.resolve("cancelled.darkroom_latent.npz")))
        assertTrue(root.resolve("scratch").toFile().listFiles()!!.isEmpty())
        val old=PhoneMaterialDefaults.exposureRecipe(film,emptyMap())
        assertEquals(false,old["enable_mtf"]);assertEquals(false,old["enable_halation"])
        assertFalse((old["film"] as Map<*,*>).containsKey("grain_scales"))
        Unit
    }

    @Test fun rowsMatchWholeExposureAndDevelopmentAcrossModelsAndPartialLastRows() = runBlocking {
        val width = 7; val height = 17
        val rgb = FloatArray(width * height * 3) { (it % 31) / 31f }
        val capture = mapOf<String, Any?>("kind" to "linear_working_rgb")
        for (recipe in listOf(emptyMap(), mapOf("enable_subtractive" to false), spectralRecipe())) {
            val wholeRequest = WorkflowRequests.expose("expected.darkroom_latent.npz", recipe, width, height, rgb,
                capture, FrameIdentity("row-roll", frameIndex = 2))
            val whole = facade.run(wholeRequest).requireLatentArchive()
            val expectedLatent = codec.readLatent(codec.writeLatent(whole))
            for (budget in listOf(7, 31, 1000)) {
                val output = "frame.darkroom_latent.npz"
                val committed = facade.run(wholeRequest.copy(output = MediumRef(output), rowIo = io, rowBlockPixels = budget))
                assertTrue(committed.mediumCommitted); assertNull(committed.latent)
                val bytes = Files.readAllBytes(root.resolve(output))
                val actual = codec.readLatent(bytes)
                assertEquals(expectedLatent.metadata, actual.metadata)
                for (key in expectedLatent.arrays.keys) {
                    assertArrayEquals(expectedLatent.arrays.getValue(key).shape, actual.arrays.getValue(key).shape)
                    assertArrayEquals(expectedLatent.arrays.getValue(key).values, actual.arrays.getValue(key).values, 0f)
                }
                for (time in listOf(2.5, 12.0)) {
                    val developRecipe = mapOf("develop" to mapOf("time_min" to time))
                    val expected = facade.run(WorkflowRequests.developLatent(output, "expected.darkroom_negative.npz", developRecipe,
                        codec.readLatent(bytes))).requireOpticalArchive()
                    val expectedRoundTrip = codec.readDevelopedOptical(codec.writeDevelopedOptical(expected))
                    facade.run(WorkflowRequests.developLatent(output, "result.darkroom_negative.npz", developRecipe, rowIo = io).copy(rowBlockPixels = budget))
                    val observed = root.resolve("result.darkroom_negative.npz").toFile().inputStream().use { codec.readDevelopedOptical(it) }
                    assertEquals(expectedRoundTrip.metadata, observed.metadata)
                    assertArrayEquals(expected.opticalDensityRgb.values, observed.opticalDensityRgb.values, 0f)
                    assertArrayEquals(expected.clearBaseOpticalDensityRgb, observed.clearBaseOpticalDensityRgb, 0f)
                    expected.spectralComponentAmounts?.let { assertArrayEquals(it.values, observed.spectralComponentAmounts!!.values, 0f) }
                    assertArrayEquals(bytes, Files.readAllBytes(root.resolve(output)))
                }
                assertEquals(0, root.resolve("scratch").toFile().listFiles()!!.size)
            }
        }
    }

    @Test fun cancelledRowAndFailedCommitPreserveExistingOutputAndCleanScratch() = runBlocking {
        val output = "frame.darkroom_latent.npz"
        Files.write(root.resolve(output), byteArrayOf(1, 2, 3))
        var cancelled = false
        var calls = 0
        val source = object : RgbRows {
            override val width = 5; override val height = 9
            override fun readRows(start: Int, count: Int): RgbVolume {
                if (++calls == 2) cancelled = true
                return RgbVolume(width, count, linearRgb = FloatArray(width * count * 3) { .2f })
            }
        }
        val request = WorkflowRequest(Command.EXPOSE, MediumRef("camera"), MediumRef(output), emptyMap(),
            rowIo = io, rgbRows = source, rowBlockPixels = 5, control = WorkflowControl(cancelRequested = { cancelled }))
        val error = assertThrows(WorkflowException::class.java) { runBlocking { facade.run(request) } }
        assertEquals("cancelled", error.code)
        assertArrayEquals(byteArrayOf(1, 2, 3), Files.readAllBytes(root.resolve(output)))
        assertEquals(0, root.resolve("scratch").toFile().listFiles()!!.size)
        cancelBeforeCommit = true
        assertThrows(WorkflowException::class.java) { runBlocking {
            facade.run(WorkflowRequests.expose(output, emptyMap(), 3, 3, FloatArray(27), rowIo = io))
        } }
        assertArrayEquals(byteArrayOf(1, 2, 3), Files.readAllBytes(root.resolve(output)))
        assertEquals(0, root.resolve("scratch").toFile().listFiles()!!.size)
        assertFalse(root.toFile().listFiles()!!.any { it.name.startsWith("pending-") })
    }

    @Test fun consumedAndMismatchedPoolsFailBeforeOutputCommit() = runBlocking {
        val latent = PhoneReducedKernel().expose(RgbVolume(3, 5), emptyMap())
        val entries = codec.latentEntries(LatentArchive(latent.arrays, latent.metadata)).toMutableMap()
        entries["dye"] = NpyValue.Float32(FloatNd(intArrayOf(2, 3, 3), FloatArray(18)))
        Files.write(root.resolve("bad.darkroom_latent.npz"), NpzFile.write(entries))
        assertThrows(WorkflowException::class.java) { runBlocking { facade.run(WorkflowRequests.developLatent(
            "bad.darkroom_latent.npz", "bad.darkroom_negative.npz", emptyMap(), rowIo = io)) } }
        assertFalse(Files.exists(root.resolve("bad.darkroom_negative.npz")))
        Files.write(root.resolve("bad.darkroom_latent.npz"), codec.writeLatent(LatentArchive(latent.arrays, latent.metadata + ("consumed" to true))))
        assertThrows(WorkflowException::class.java) { runBlocking { facade.run(WorkflowRequests.developLatent(
            "bad.darkroom_latent.npz", "bad.darkroom_negative.npz", emptyMap(), rowIo = io)) } }
        assertEquals(0, root.resolve("scratch").toFile().listFiles()!!.size)
    }

    @Test fun workflowPreparesOncePerTaskAndReleasesTaskOnCancellation() = runBlocking {
        val phone = PhoneReducedKernel()
        var exposurePreparations = 0; var developmentPreparations = 0
        var exposureRows = 0; var developmentRows = 0
        val instrumented = object : RowReducedKernel by phone {
            override fun prepareExposure(recipe: Map<String, Any?>, width: Int, fullHeight: Int): PreparedRowExposure {
                exposurePreparations++
                val task = phone.prepareExposure(recipe, width, fullHeight)
                return PreparedRowExposure { exposureRows++; task.exposeRows(it) }
            }
            override fun prepareDevelopment(recipe: Map<String, Any?>, firstRows: LatentState, fullHeight: Int): PreparedRowDevelopment {
                developmentPreparations++
                val task = phone.prepareDevelopment(recipe, firstRows, fullHeight)
                return PreparedRowDevelopment { developmentRows++; task.developRows(it) }
            }
        }
        val app = DefaultWorkflowFacade(instrumented)
        val exposure = WorkflowRequests.expose("prepared.darkroom_latent.npz", spectralRecipe(), 7, 17,
            FloatArray(7 * 17 * 3) { .4f }, rowIo = io).copy(rowBlockPixels = 21)
        app.run(exposure)
        assertEquals(1, exposurePreparations); assertEquals(6, exposureRows)
        app.run(WorkflowRequests.developLatent("prepared.darkroom_latent.npz", "prepared.darkroom_negative.npz",
            emptyMap(), rowIo = io).copy(rowBlockPixels = 21))
        assertEquals(1, developmentPreparations); assertEquals(6, developmentRows)
        var cancelled = false
        val control = WorkflowControl(cancelRequested = { cancelled })
        val source = object : RgbRows {
            override val width = 7; override val height = 17
            override fun readRows(start: Int, count: Int): RgbVolume {
                cancelled = true
                return RgbVolume(width, count)
            }
        }
        val error = assertThrows(WorkflowException::class.java) { runBlocking {
            app.run(exposure.copy(output = MediumRef("cancelled.darkroom_latent.npz"), rgbRows = source, control = control))
        } }
        assertEquals("cancelled", error.code)
        assertEquals(2, exposurePreparations); assertEquals(6, exposureRows)
        assertFalse(Files.exists(root.resolve("cancelled.darkroom_latent.npz")))
        app.run(exposure)
        assertEquals(3, exposurePreparations); assertEquals(12, exposureRows)
        assertEquals(0, root.resolve("scratch").toFile().listFiles()!!.size)
    }
}
