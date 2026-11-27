package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class FormedFilmBorderTest {
    @TempDir lateinit var root: Path

    @Test fun laserBleedFollowsLettersWithPerforationsSwitchedOff() {
        val w=320;val h=180;val kernel=PhoneReducedKernel()
        fun density(method: String,glow: Double): FloatArray {
            val settings=FilmEdgeSettings.create("135",true,"SILVER",patternSettings=mapOf("factory_mark_type" to method,
                "factory_pattern_top" to "FFEM1:3x1:A0","rebate_glow" to glow))
            val recipe=mapOf("film_border" to (settings+("sprocket_rows" to "none")))
            val input=RgbVolume(w,h,linearRgb=FloatArray(w*h*3){.18f})
            val developed=kernel.develop(kernel.expose(input,recipe),recipe)
            return FormedFilmBorder.prepare(kernel,recipe,w,h)!!.surround(developed).opticalDensityRgb.values
        }
        val laser=density("laser",1.0);val dry=density("laser",0.0)
        val ink=density("ink",1.0);val inkDry=density("ink",0.0)
        assertTrue(laser.indices.any { ink[it]==inkDry[it] && kotlin.math.abs(laser[it]-dry[it])>.001f })
    }

    @Test fun printedInkAndWarmEdgesSurviveSpectralRowArchives() = runBlocking {
        val w=144;val h=96
        val base=RecipeShareCodec.decode(File("../kernel/src/test/resources/spectral_optics_sample.ffc2").readText())+
            (PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
        val facade=DefaultWorkflowFacade();val codec=NpzMediumArchive()
        for(mode in listOf("attached","native")) {
            fun recipe(transmission: Double,glow: Double)=base+("film_border" to FilmEdgeSettings.create("135",true,"SILVER",patternSettings=mapOf(
                "formation_mode" to mode,"factory_mark_type" to "ink","factory_ink_transmission_rgb" to List(3){transmission},"rebate_glow" to glow)))
            suspend fun form(transmission: Double,glow: Double): DevelopedOpticalArchive {
                val r=recipe(transmission,glow)
                val request=WorkflowRequests.expose("frame.darkroom_latent.npz",r,w,h,FloatArray(w*h*3){.18f},rowIo=io)
                facade.run(request)
                val output="print.darkroom_negative.npz"
                facade.run(WorkflowRequests.developLatent(request.output.path,output,r,rowIo=io).copy(rowBlockPixels=w*6))
                return codec.readDevelopedOptical(Files.readAllBytes(root.resolve(output)))
            }
            val film=form(.25,.35);val clear=form(1.0,.35);val cold=form(.25,0.0)
            assertArrayEquals(clear.spectralComponentAmounts!!.values,film.spectralComponentAmounts!!.values,0f)
            val surface=film.spectralPostProcessDensityRgb!!.values
            val density=film.opticalDensityRgb.values
            assertTrue(surface.any { it>.60f })
            assertTrue(density.indices.any { kotlin.math.abs(density[it]-cold.opticalDensityRgb.values[it])>.001f })
            for(i in density.indices) {
                assertEquals(surface[i],density[i]-clear.opticalDensityRgb.values[i],1e-6f)
                if(clear.opticalDensityRgb.values[i]==0f) assertEquals(0f,density[i])
            }
        }
    }

    private val io get()=object: RowMediumIo {
        override val scratchRoot=root.resolve("scratch").toFile()
        override fun <T> read(path: String,reader: (InputStream)->T)=root.resolve(path).toFile().inputStream().use(reader)
        override fun write(path: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) { beforeCommit();root.resolve(path).toFile().outputStream().use(writer) }
        override fun sameLocation(a: String,b: String)=a==b
    }
    @Test fun storedCameraLeakReachesEmulsionWithoutExposingHoles() = runBlocking {
        val w=240;val h=160;val codec=NpzMediumArchive();val facade=DefaultWorkflowFacade()
        val conditions=mapOf("model" to PhoneCaptureConditions.MODEL,"camera" to mapOf("leak" to .85,"leak_side" to "left"))
        val capture=mapOf("kind" to "display_referred_srgb","stored_exposures" to listOf(mapOf("capture" to mapOf("exposure_conditions" to conditions))))
        val r=mapOf("film_border" to FilmEdgeSettings.create("135",true,"FOUNDRY"),
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
        val rgb=FloatArray(w*h*3) { .18f }
        PhoneCaptureConditions(w,h,conditions).apply(rgb,0,h)
        val source=WorkflowRequests.expose("source.darkroom_latent.npz",r,w,h,rgb,rowIo=io).copy(captureRecord=capture)
        facade.run(source)
        facade.run(WorkflowRequests.developLatent(source.output.path,"leak.darkroom_negative.npz",r,rowIo=io).copy(rowBlockPixels=7*w))
        val archived=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("leak.darkroom_negative.npz")))
        val kernel=PhoneReducedKernel()
        val loaded=codec.readLatent(Files.readAllBytes(root.resolve(source.output.path)))
        val gate=kernel.develop(LatentState(loaded.arrays,loaded.metadata),r)
        val dry=FormedFilmBorder.prepare(kernel,r,w,h)!!.surround(gate).opticalDensityRgb.values
        val plan=FormedFilmBorder.prepare(kernel,r,w,h,capture)!!
        val wet=plan.surround(gate).opticalDensityRgb.values
        assertArrayEquals(wet,archived.opticalDensityRgb.values,1e-6f)
        var changed=0;var air=0
        for(y in 0 until plan.layout.rebate) for(x in 0 until w) {
            val i=(y*w+x)*3
            if(plan.layout.hole(x,plan.layout.rebate-1-y)) {
                air++;for(c in 0..2) assertEquals(0f,wet[i+c])
            } else if(kotlin.math.abs(wet[i]-dry[i])>.001f) changed++
        }
        assertTrue(air>0 && changed>0)
        val master = plan.surround(gate)
        val x = w/16
        val y = (plan.layout.rebate*.63).toInt()
        for (normalize in listOf(true,false)) {
            val scan = mapOf("scanner" to mapOf("transmission_light_ev" to -1.5,
                "transmission_light_temperature_k" to 3200, "scanner_light_color" to listOf(.9,1.0,.8),
                "scan_normalize" to normalize))
            val full = kernel.observe(master,scan).outputRgb.values
            val table = kernel.observe(master,scan+("observe_mode" to "viewer_light_table")).outputRgb.values
            val i = (y*w+x)*3
            assertTrue(full[i] in .5f.. .7f && full[i]>full[i+1] && full[i+1]>full[i+2])
            for(c in 0..2) assertEquals(table[i+c],full[i+c],1e-6f)
            val rows = FloatArray(full.size)
            RowObservationSession.scan(io,"leak.darkroom_negative.npz",scan,object: RowObservationSession.Output {
                override fun begin(width: Int,height: Int) = Unit
                override fun rows(start: Int,count: Int,rgb: FloatArray) { rgb.copyInto(rows,start*w*3) }
            })
            assertArrayEquals(full,rows,1e-6f)
        }
    }

    @Test fun nativeFilmThroughMemoryAndRowArchives() = runBlocking {
        val w=144;val h=96;val facade=DefaultWorkflowFacade();val codec=NpzMediumArchive()
        val rgb=FloatArray(w*h*3) { i -> .05f+(i%101)/120f }
        for(mode in listOf("native","sprocket_panorama")) {
            val border=FilmEdgeSettings.create("135",true,"SILVER",patternSettings=mapOf("formation_mode" to mode))
            val recipe=mapOf("film_border" to border, PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
            val geometry=FilmRebateGeometry.forStock(border,w,h)
            val memory=facade.run(WorkflowRequests.expose("frame.darkroom_latent.npz",recipe,w,h,rgb)).requireLatentArchive()
            assertEquals(geometry.height,memory.arrays.getValue("halide").shape[0])
            val exposed=WorkflowRequests.expose("frame.darkroom_latent.npz",recipe,w,h,rgb,rowIo=io)
            facade.run(exposed.copy(rowBlockPixels=w*5))
            val file=codec.readLatent(Files.readAllBytes(root.resolve(exposed.output.path)))
            assertArrayEquals(memory.arrays.getValue("developability").values,file.arrays.getValue("developability").values,1e-6f)
            val changed=recipe+("film_border" to emptyMap<String,Any?>())
            val mem=facade.run(WorkflowRequests.developLatent("frame.darkroom_latent.npz","out.darkroom_negative.npz",changed)
                .copy(latent=LatentState(memory.arrays,memory.metadata))).requireOpticalArchive()
            facade.run(WorkflowRequests.developLatent(exposed.output.path,"out.darkroom_negative.npz",changed,rowIo=io).copy(rowBlockPixels=w*5))
            val medium=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("out.darkroom_negative.npz")))
            assertEquals(mode,(medium.metadata[FilmRebateGeometry.KEY] as Map<*,*>)["formation_mode"])
            assertEquals(geometry.height,medium.opticalDensityRgb.shape[0])
            assertArrayEquals(mem.opticalDensityRgb.values,medium.opticalDensityRgb.values,1e-6f)
            val y=(geometry.rebate*.63).toInt()
            val x=w/(2*geometry.holesPerRow)
            assertEquals(0f,medium.opticalDensityRgb.values[(y*w+x)*3])
            assertEquals(0f,file.arrays.getValue("halide").values[(y*w+x)*3])
            val edge=file.arrays.getValue("developability").values
            assertTrue(if(mode=="native") edge[0]<.01f else edge[0]>.01f)
        }
    }

    @Test fun rotatedHalfFrameUsesTheShortSidesInRowsAndScan() = runBlocking {
        val w=128;val h=96;val kernel=PhoneReducedKernel();val facade=DefaultWorkflowFacade()
        val codec=NpzMediumArchive()
        val rgb=FloatArray(w*h*3) { .06f+(it%83)/100f }
        for(mode in listOf("attached","native")) {
            val border=FilmEdgeSettings.create("half",true,"SILVER",patternSettings=mapOf("formation_mode" to mode,"loading_offset" to .21))
            assertThrows(IllegalArgumentException::class.java) { FilmRebateGeometry.forStock(border+("formation_mode" to "sprocket_panorama"),w,h) }
            val recipe=mapOf("film_border" to border,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
            val geometry=FilmRebateGeometry.forStock(border,w,h)
            assertTrue(geometry.vertical);assertEquals(4,geometry.holesPerRow)
            val ex=WorkflowRequests.expose("half.darkroom_latent.npz",recipe,w,h,rgb,rowIo=io)
            facade.run(ex.copy(rowBlockPixels=5*w))
            facade.run(WorkflowRequests.developLatent(ex.output.path,"half.darkroom_negative.npz",recipe,rowIo=io).copy(rowBlockPixels=w*5))
            val archive=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("half.darkroom_negative.npz")))
            val mem=facade.run(ex.copy(rowIo=null)).requireLatentArchive()
            val memory=facade.run(WorkflowRequests.developLatent(ex.output.path,"half.darkroom_negative.npz",recipe)
                .copy(latent=LatentState(mem.arrays,mem.metadata))).requireOpticalArchive()
            assertArrayEquals(memory.opticalDensityRgb.values,archive.opticalDensityRgb.values,1e-6f)
            assertArrayEquals(intArrayOf(h,geometry.outputWidth,3),archive.opticalDensityRgb.shape)
            val master=OpticalMaster(archive.opticalDensityRgb,archive.metadata,archive.clearBaseOpticalDensityRgb,
                archive.spectralComponentAmounts,archive.spectralPostProcessDensityRgb)
            val scanRecipe=mapOf("scanner" to mapOf("transmission_light_temperature_k" to 3200,"transmission_light_ev" to -1.5))
            val full=kernel.observe(master,scanRecipe).outputRgb.values
            val check=FloatArray(full.size)
            RowObservationSession.scan(io,"half.darkroom_negative.npz",scanRecipe,object: RowObservationSession.Output {
                override fun begin(width: Int,height: Int)=Unit
                override fun rows(start: Int,count: Int,rgb: FloatArray) { rgb.copyInto(check,start*geometry.outputWidth*3) }
            })
            assertArrayEquals(full,check,1e-6f)
            val window=geometry.windows("both").first()
            val y=(((window[1]+window[3])*.5+.21)*h).toInt()
            val x=((window[0]+window[2])*.5*geometry.outputWidth).toInt()
            val at=(y*geometry.outputWidth+x)*3
            assertEquals(0f,archive.opticalDensityRgb.values[at])
            assertTrue(full[at]>full[at+1] && full[at+1]>full[at+2])
        }
    }

    @Test fun pairedHalfFramesReconstructOneStockPlate() {
        val w=144;val h=192;val kernel=PhoneReducedKernel()
        for(type in listOf("hard","laser","laser_etched")) for(offset in listOf(0.0,-.25,.25)) {
            fun film(format: String,slot: Int,width: Int,offsetFrames: Double): OpticalMaster {
                val border=FilmEdgeSettings.create(format,true,"SILVER SERIES",patternSettings=mapOf(
                    "factory_mark_type" to type,"factory_barcode" to "code128c","loading_offset" to offsetFrames))
                val r=mapOf("film_border" to (border+("frame_number" to slot)))
                val gate=kernel.develop(kernel.expose(RgbVolume(width,h,linearRgb=FloatArray(width*h*3){.18f}),r),r)
                return FormedFilmBorder.prepare(kernel,r,width,h)!!.surround(gate)
            }
            val full=film("135",12,w*2,offset/2)
            for((part,slot) in listOf(23,24).withIndex()) {
                val half=film("half",slot,w,offset)
                val record=half.metadata[FilmRebateGeometry.KEY] as Map<*,*>
                assertEquals(mapOf("frame" to 12,"half" to part+1),record["factory_window"])
                val rows=(record["rebate_rows"] as Number).toInt()
                val height=half.opticalDensityRgb.shape[0]
                for(y in (0 until rows)+(height-rows until height)) {
                    val from=(y*w*2+part*w)*3
                    assertArrayEquals(full.opticalDensityRgb.values.copyOfRange(from,from+w*3),
                        half.opticalDensityRgb.values.copyOfRange(y*w*3,(y+1)*w*3),1e-6f)
                }
            }
        }
    }

    @Test fun spectralHolesContainNoPoolsAndMaskBleachingKeepsTheFormedSpectrum() {
        val r=RecipeShareCodec.decode(File("../kernel/src/test/resources/spectral_optics_sample.ffc2").readText())+mapOf(
            PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,"develop" to mapOf("program_key" to "color_reversal","mask_bleach_completion" to 1.0),
            "film_border" to mapOf("enabled" to true,"format_key" to "135"))
        val k=PhoneReducedKernel();val main=k.develop(k.expose(RgbVolume(32,18,linearRgb=FloatArray(32*18*3){.2f}),r),r)
        val plan=FormedFilmBorder.prepare(k,r,32,18)!!;val film=plan.surround(main);val pools=film.spectralComponentAmounts!!.values
        val x=2;val out=(plan.layout.rebate*.3).toInt();val pixel=(plan.layout.rebate-1-out)*32+x
        for(c in 0..8) assertEquals(0f,pools[pixel*9+c],0f)
        assertTrue(k.observe(film,r).outputRgb.values.all { it.isFinite() })
        val archive=DevelopedOpticalArchive(film.opticalDensityRgb,film.metadata,film.clearBaseOpticalDensityRgb,film.spectralComponentAmounts)
        System.getenv("FILM_FOUNDRY_REVIEW_OUTPUT")?.let { path -> File(path,"rebate-spectral.darkroom_negative.npz").writeBytes(NpzMediumArchive().writeDevelopedOptical(archive)) }
    }

    @Test fun stripOrderSameBathsReadOnlyScanAndRowOutputAgreeAnd120HasNoHoles() = runBlocking {
        val w=240;val h=160;val k=PhoneReducedKernel();val codec=NpzMediumArchive();val facade=DefaultWorkflowFacade()
        val film=mapOf("name" to "TEST FILM","film_base_density_rgb" to listOf(.3,.2,.1),"grain_strength" to 0.0)
        for(format in listOf("135","120")) {
            val r=mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
                "develop" to mapOf("program_key" to "color_reversal","mask_bleach_completion" to 1.0),
                "film_border" to mapOf("enabled" to true,"format_key" to format),
                "scanner" to mapOf("scan_normalize" to true),"frame_identity" to mapOf("roll_id" to "test","frame_index" to 2))
            val source=WorkflowRequests.expose("source.darkroom_latent.npz",r,w,h,FloatArray(w*h*3) { (it%137)/136f },rowIo=io)
            facade.run(source);val bytes=Files.readAllBytes(root.resolve(source.output.path));val latent=codec.readLatent(bytes)
            val main=k.develop(LatentState(latent.arrays,latent.metadata),r)
            val border=FormedFilmBorder.prepare(k,r,w,h)!!;val result=border.surround(main);val layout=border.layout
            assertEquals(if(format=="120") 0 else 8,layout.holesPerRow)
            assertEquals(layout.height,result.opticalDensityRgb.shape[0])
            assertArrayEquals(main.opticalDensityRgb.values,result.opticalDensityRgb.values.copyOfRange(layout.rebate*w*3,(layout.rebate+h)*w*3),0f)
            if(format=="135") { val x=(w*.5/8).toInt();val out=(layout.rebate*.3).toInt()
                val pixel=(layout.rebate-1-out)*w+x;assertEquals(0f,result.opticalDensityRgb.values[pixel*3],0f) }
            val expected=k.observe(main,r).outputRgb.values;val density=result.opticalDensityRgb.values.copyOf();val observed=k.observe(result,r).outputRgb
            assertArrayEquals(expected,observed.values.copyOfRange(layout.rebate*w*3,(layout.rebate+h)*w*3),0f)
            assertArrayEquals(density,result.opticalDensityRgb.values,0f)
            facade.run(WorkflowRequests.developLatent(source.output.path,"out.darkroom_negative.npz",r,rowIo=io).copy(rowBlockPixels=w*7))
            val saved=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("out.darkroom_negative.npz")))
            assertArrayEquals(density,saved.opticalDensityRgb.values,0f);assertArrayEquals(bytes,Files.readAllBytes(root.resolve(source.output.path)))
            val rowValues=FloatArray(observed.values.size)
            RowObservationSession.scan(io,"out.darkroom_negative.npz",r,object: RowObservationSession.Output {
                override fun begin(width: Int,height: Int) { assertEquals(w,width);assertEquals(layout.height,height) }
                override fun rows(start: Int,count: Int,rgb: FloatArray) { rgb.copyInto(rowValues,start*w*3) }
            })
            assertArrayEquals(observed.values,rowValues,1e-6f)
            System.getenv("FILM_FOUNDRY_REVIEW_OUTPUT")?.let { path -> File(path).apply { mkdirs() }.let { dir ->
                File(dir,"rebate-$format.darkroom_negative.npz").writeBytes(codec.writeDevelopedOptical(saved))
                val image=java.awt.image.BufferedImage(w,layout.height,java.awt.image.BufferedImage.TYPE_INT_RGB)
                val light=k.observe(result,r+("observe_mode" to "viewer_light_table")).outputRgb
                for(y in 0 until layout.height) for(x in 0 until w) { var rgb=0;for(c in 0..2) rgb=rgb or ((light.values[(y*w+x)*3+c].coerceIn(0f,1f)*255).toInt() shl (16-c*8));image.setRGB(x,y,rgb) }
                javax.imageio.ImageIO.write(image,"png",File(dir,"rebate-$format.png"))
            } }
        }
    }
}
