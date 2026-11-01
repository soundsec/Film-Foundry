package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.*
import java.nio.file.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class MaterialBindingRowsTest {
    @TempDir lateinit var root: Path
    @Test fun rowAndMemoryDevelopmentBindTheSameFilmIncludingFactoryBorder() = runBlocking {
        val io=object: RowMediumIo {
            override val scratchRoot=root.resolve("scratch").toFile()
            override fun <T> read(path: String,reader: (InputStream)->T)=root.resolve(path).toFile().inputStream().use(reader)
            override fun write(path: String,beforeCommit: ()->Unit,writer: (OutputStream)->Unit) {
                val pending=Files.createTempFile(root,"pending-",".tmp")
                try {
                    pending.toFile().outputStream().use(writer);beforeCommit()
                    Files.move(pending,root.resolve(path),StandardCopyOption.REPLACE_EXISTING)
                } finally { Files.deleteIfExists(pending) }
            }
            override fun sameLocation(a: String,b: String)=root.resolve(a).normalize()==root.resolve(b).normalize()
        }
        val options=mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT,
            "enable_grain" to false,"enable_mtf" to false,"enable_halation" to false)
        val film=mapOf("name" to "SOURCE","grain_strength" to 0.0,"film_base_density_rgb" to listOf(.17,.51,.82),
            "density_max" to listOf(2.1,1.9,2.0))
        val kernel=PhoneReducedKernel();val codec=NpzMediumArchive();val facade=DefaultWorkflowFacade(kernel)
        val latent=kernel.expose(RgbVolume(47,31,linearRgb=FloatArray(47*31*3){.2f}),options+("film" to film))
        val bytes=codec.writeLatent(LatentArchive(latent.arrays,latent.metadata))
        Files.write(root.resolve("source.darkroom_latent.npz"),bytes)
        val source=codec.readLatent(bytes)
        val original=options+mapOf("film" to film,"develop" to mapOf("program_key" to "color_negative"),
            "film_border" to mapOf("enabled" to true,"format_key" to "135"))
        val wrong=original+("film" to mapOf("name" to "WRONG","film_base_density_rgb" to listOf(1.0,1.0,1.0)))
        val expected=facade.run(WorkflowRequests.developLatent("source.darkroom_latent.npz","expected.darkroom_negative.npz",original,source)).requireOpticalArchive()
        val memory=facade.run(WorkflowRequests.developLatent("source.darkroom_latent.npz","memory.darkroom_negative.npz",wrong,source)).requireOpticalArchive()
        facade.run(WorkflowRequests.developLatent("source.darkroom_latent.npz","rows.darkroom_negative.npz",wrong,rowIo=io).copy(rowBlockPixels=47*3))
        val rows=codec.readDevelopedOptical(Files.readAllBytes(root.resolve("rows.darkroom_negative.npz")))
        for(result in listOf(memory,rows)) {
            assertArrayEquals(expected.opticalDensityRgb.values,result.opticalDensityRgb.values,0f)
            assertTrue(RecipeJson.equivalent(expected.metadata,result.metadata))
            assertTrue((result.metadata[FilmRebateGeometry.KEY] as Map<*,*>)["label"].toString().startsWith("SOURCE"))
        }
        assertArrayEquals(bytes,Files.readAllBytes(root.resolve("source.darkroom_latent.npz")))
        assertTrue(io.scratchRoot.listFiles()!!.isEmpty())
    }
}
