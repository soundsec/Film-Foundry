package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class MobileConfigurationFlowTest {
    @Test fun legacyBlackWhiteShareKeepsItsLayerCountWhenLoadedAsFilmDefinition() {
        val film=RecipeDrafts.filmFromRecipe(mapOf("enable_subtractive" to false,"film" to mapOf("contrast" to 1.2)))
        assertEquals("bw",film["color_process"])
        val latent=PhoneReducedKernel().expose(RgbVolume(1,1,linearRgb=floatArrayOf(.1f,.2f,.3f)),mapOf("film" to film))
        assertEquals(1,latent.arrays.getValue("halide").shape[2])
        val original=mapOf("enable_subtractive" to false,"film" to mapOf("color_process" to "color_negative"))
        val explicit=RecipeDrafts.filmFromRecipe(original)
        assertEquals("bw",explicit["color_process"])
        val kernel=PhoneReducedKernel();val input=RgbVolume(1,1,linearRgb=floatArrayOf(.1f,.2f,.3f))
        val legacy=kernel.expose(input,original);val normalized=kernel.expose(input,mapOf("film" to explicit))
        for((key,value) in legacy.arrays) assertArrayEquals(value.values,normalized.arrays.getValue(key).values)
    }
    @Test fun materialSmitsAndToneRgbPresetsSurviveSaveShareAndActualObservation() = runBlocking {
        val builtins=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        val profiles=RecipeJson.objectMap(File("../app/src/main/assets/phone_smits_profiles.json").readText())
        for(layers in listOf(1,3)) {
            val original=(builtins.materials[if(layers == 1) 3 else 0].recipe["film"] as Map<*,*>).entries.associate { it.key.toString() to it.value }
            val film=RecipeDrafts.material(original+("spectral_exposure_profile" to profiles[layers.toString()]))
            val scanner=RecipeDrafts.scanner(mapOf("scan_grid_long_edge" to 12,"tone_curve" to listOf(listOf(0.0,.05),listOf(1.0,.95)),
                "color_curve_rgb" to List(3) { listOf(listOf(0.0,0.0),listOf(1.0,.85+it*.04)) }))
            val store=MemoryRecipeCatalogStore();val catalog=RecipeCatalog(store)
            catalog.saveFilm("film-$layers","Film",film);catalog.saveScanner("scanner-$layers","Scanner",scanner)
            val restored=RecipeCatalog(store)
            val shared=RecipeShareCodec.decode(RecipeShareCodec.encode(mapOf("film" to restored.film("film-$layers").recipe,
                "develop" to mapOf("time_min" to 4.0,"chemistry" to "native"),"scanner" to restored.scanner("scanner-$layers").recipe)))
            val kernel=PhoneReducedKernel();val latent=kernel.expose(RgbVolume(6,4,linearRgb=FloatArray(72){.01f+(it%19)*.04f}),shared)
            val master=kernel.develop(latent,shared);val before=master.opticalDensityRgb.values.copyOf()
            val archive=DevelopedOpticalArchive(master.opticalDensityRgb,master.metadata,master.clearBaseOpticalDensityRgb,master.spectralComponentAmounts,master.spectralPostProcessDensityRgb)
            val observed=OpticalPreviewSession().scan(archive,shared)
            assertEquals(12,observed.width);assertTrue(observed.rgb.all { it.isFinite() && it in 0f..1f })
            assertArrayEquals(before,master.opticalDensityRgb.values)
            assertEquals(DataKernel.SMITS_EXPOSURE_MODEL_ID,(latent.metadata["spectral_exposure_profile"] as Map<*,*>)["model"])
        }
        Unit
    }
    @Test fun invalidDraftsFailRatherThanSilentlySelectingDefaultsOrAllocatingAnOutputGrid() {
        assertThrows(IllegalArgumentException::class.java) { RecipeDrafts.material(mapOf("layer_sensitivity_matrix" to listOf(listOf(1.0)))) }
        assertThrows(IllegalArgumentException::class.java) { RecipeDrafts.scanner(mapOf("color_curve_rgb" to listOf(listOf(listOf(0.0,0.0))))) }
        assertThrows(IllegalArgumentException::class.java) { RecipeDrafts.process(mapOf("time_min" to "eight")) }
        // An 8K definition is validated with a tiny observer, without creating an 8K image.
        assertEquals(8192,(RecipeDrafts.scanner(mapOf("scan_grid_long_edge" to 8192))["scan_grid_long_edge"] as Number).toInt())
    }
}
