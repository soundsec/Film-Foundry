package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneProcessPresetsTest {
    @Test fun matchThisFilmIdentifiesOnlyTheLoadedStocksNativeStandard() {
        val catalog = BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        for ((key, standard) in mapOf("clear_modern_negative" to "phone_color_negative",
                "color_reversal_transparency" to "phone_color_reversal", "monochrome_push" to "phone_bw_negative",
                "monochrome_reversal_transparency" to "phone_bw_reversal")) {
            val film = RecipeDrafts.filmFromRecipe(catalog.materials.single { it.id == key }.recipe)
            val routes = PhoneProcessPresets.routesForRoll(film, PhoneFormationProfile.LIGHTWEIGHT)
            assertEquals(standard, routes.matchingStandardId)
            if (standard.startsWith("phone_bw")) {
                assertTrue(routes.noImageIds.containsAll(listOf("phone_color_negative", "phone_color_reversal", "phone_negative_clear_reversal")))
                assertFalse("phone_silver_bypass" in routes.noImageIds)
            } else assertTrue(routes.noImageIds.isEmpty())
            assertEquals(PhoneProcessPresets.RollRoutes(), PhoneProcessPresets.routesForRoll(film, null))
        }
        assertEquals(PhoneProcessPresets.RollRoutes(), PhoneProcessPresets.routesForRoll(emptyMap(), PhoneFormationProfile.LIGHTWEIGHT))
    }

    @Test fun shippedPositiveMaterialsAndExplicitCrossRoutesFormTheirOwnPolarityAndKeepBase() {
        val materials=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes()).materials.associateBy { it.id }
        val presets=PhoneProcessPresets.entries.associateBy { it.id }
        // Validate every shipped process, then exercise the actual material + recipe combinations.
        presets.values.forEach { RecipeDrafts.process(it.recipe) }
        val cases=listOf(
            Triple("color_reversal_transparency","phone_native_8","positive"),
            Triple("monochrome_reversal_transparency","phone_native_8","positive"),
            Triple("color_reversal_transparency","phone_color_reversal","positive"),
            Triple("monochrome_reversal_transparency","phone_bw_reversal","positive"),
            Triple("clear_modern_negative","phone_negative_reversal","positive"),
            Triple("monochrome_push","phone_bw_negative_reversal","positive"),
            Triple("color_reversal_transparency","phone_slide_negative","negative")
        )
        val kernel=PhoneReducedKernel()
        for((material,process,polarity) in cases) {
            val film=RecipeDrafts.material(materials.getValue(material).recipe["film"] as Map<String,Any?>)
            val develop=presets.getValue(process).recipe.let { if("program_key" in it) it else it+("program_key" to "auto") }
            val recipe=mapOf("film" to film,"develop" to develop,"scanner" to mapOf("scan_normalize" to false))
            val latent=kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.001f,.001f,.001f,.18f,.18f,.18f,8f,8f,8f)),recipe)
            val optical=kernel.develop(latent,recipe)
            assertEquals(polarity,optical.metadata["image_polarity"],"$material / $process")
            val before=optical.opticalDensityRgb.values.copyOf()
            val scan=kernel.observe(optical,recipe)
            assertTrue(scan.outputRgb.values[0]<scan.outputRgb.values[6],"default observation must follow actual polarity")
            assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
            val base=optical.clearBaseOpticalDensityRgb!!
            val declared=film["film_base_density_rgb"] as List<*>
            for(c in 0..2) assertEquals((declared[c] as Number).toFloat(),base[c],1e-6f,"processing must retain the stock base")
        }
    }
}
