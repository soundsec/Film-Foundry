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
    private val io get()=object: RowMediumIo {
        override val scratchRoot=root.resolve("scratch").toFile()
        override fun <T> read(path: String,reader: (InputStream)->T)=root.resolve(path).toFile().inputStream().use(reader)
        override fun write(path: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) { beforeCommit();root.resolve(path).toFile().outputStream().use(writer) }
        override fun sameLocation(a: String,b: String)=a==b
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
