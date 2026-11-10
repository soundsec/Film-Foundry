package com.filmfoundry.workflow

import com.filmfoundry.kernel.*
import com.filmfoundry.interchange.*
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneFinishRecipesTest {
    @Test fun automaticFinishPreservesColourAndUsesActualMediumPolarityAndBase() {
        val curve=listOf(listOf(0.0,0.0),listOf(.4,.5),listOf(1.0,1.0))
        val colors=mapOf("scan_saturation" to 1.37,"tone_curve" to curve,"color_curve_rgb" to List(3) { curve },
            "color_mixer" to List(8) { if(it==0) listOf(12,17,-8) else listOf(0,0,0) })
        val original=colors+mapOf("invert_transmission" to false,"remove_base_mask" to false,"scan_normalize_mode" to "rgb","scan_normalize" to false)
        val auto=PhoneFinishRecipes.observation(mapOf("scanner" to original),emptyMap(),PhoneFinishRecipes.AUTO)
        colors.forEach { (key,value)->assertEquals(value,auto[key]) }
        assertFalse(auto.containsKey("invert_transmission"));assertFalse(auto.containsKey("remove_base_mask"))
        assertEquals("luma",auto["scan_normalize_mode"]);assertEquals(true,auto["scan_normalize"])
        val neutral=PhoneFinishRecipes.scanner(PhoneFinishRecipes.AUTO)
        assertFalse(neutral.containsKey("scan_saturation"));assertFalse(neutral.containsKey("tone_curve"))
        val k=PhoneReducedKernel()
        for(positive in listOf(false,true)) for(masked in listOf(false,true)) {
            val base=if(masked) floatArrayOf(.15f,.35f,.6f) else FloatArray(3) { .1f }
            val d=FloatNd(intArrayOf(1,128,3),FloatArray(384) { base[it%3]+.01f+(it/3)/70f })
            val master=OpticalMaster(d,mapOf("image_polarity" to if(positive) "positive" else "negative"),base)
            val before=d.values.copyOf()
            // Original stock says the opposite: only the formed image controls interpretation.
            val output=k.observe(master,mapOf("film" to mapOf("image_polarity" to if(positive) "negative" else "positive"),"scanner" to auto))
            assertEquals(!positive,output.metadata["invert_transmission"])
            assertEquals(!positive || masked,output.metadata["remove_base_mask"])
            assertTrue(output.outputRgb.values.all { it.isFinite() })
            assertArrayEquals(before,d.values,0f)
        }
        assertEquals(1.18,PhoneFinishRecipes.scanner("ready")["scan_saturation"])
    }
    @Test fun clearNegativeRecommendedFinishRetainsDarkDetailAndMiddleGrey() {
        val catalog=BuiltinRecipeCatalog.read(java.io.File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        val film=catalog.materials.first { it.id=="clear_modern_negative" }.recipe["film"] as Map<String,Any?>
        val exposure=PhoneMaterialDefaults.exposureRecipe(film,mapOf("format" to "135"))
        val recipe=exposure+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT,"develop" to mapOf("program_key" to "auto"),"scanner" to PhoneFinishRecipes.scanner("ready"))
        val kernel=PhoneReducedKernel()
        val source=kernel.develop(kernel.expose(RgbVolume(256,1,linearRgb=FloatArray(768){it/3/255f}),recipe),recipe)
        val before=source.opticalDensityRgb.values.copyOf()
        val output=kernel.observe(source,recipe).outputRgb.values
        assertTrue(output.slice(15..17).max()<.15f)
        assertTrue(output.slice(138..140).all { it in .3f..65f/100 })
        assertTrue(output.takeLast(3).max()>.9f)
        assertArrayEquals(before,source.opticalDensityRgb.values,0f)
    }
    @Test fun recommendedFinishEstablishesBlackAndRetainsReadonlyMediumAndCurves() {
        val kernel=PhoneReducedKernel()
        val rgb=FloatArray(128*3) { (it/3)/127f }
        val optical=kernel.develop(kernel.expose(RgbVolume(128,1,linearRgb=rgb),emptyMap()),emptyMap())
        val before=optical.opticalDensityRgb.values.copyOf()
        val legacy=kernel.observe(optical,emptyMap()).outputRgb.values
        val recipe=mapOf("scanner" to PhoneFinishRecipes.scanner("ready"))
        val ready=kernel.observe(optical,recipe).outputRgb.values
        assertTrue(legacy[0]>.25f);assertTrue(ready[0]<.03f)
        assertTrue(ready.last()>.9f)
        assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
        val bright=kernel.observe(optical,recipe+("look" to mapOf("print_exposure_ev" to 1.0))).outputRgb.values
        assertTrue(bright[3*64]>ready[3*64])
        val custom=mapOf("invert_transmission" to true,"tone_curve" to listOf(listOf(0.0,.1),listOf(1.0,.9)))
        assertEquals(custom,PhoneFinishRecipes.observation(emptyMap(),custom,"custom"))
        for(style in PhoneFinishRecipes.styles-"custom") {
            val code=RecipeShareCodec.encode(mapOf("scanner" to PhoneFinishRecipes.scanner(style)))
            assertEquals(code,RecipeShareCodec.encode(RecipeShareCodec.decode(code)))
        }
    }

    @Test fun optionalPhotoFailureDoesNotStopRemainingNegativesAndResumeAvoidsDuplicates()= runBlocking {
        val delivery=PhotoDeliverySession(setOf("already-saved"),WorkflowControl())
        var writes=0
        assertTrue(delivery.deliver("already-saved") { error("must not duplicate") })
        assertFalse(delivery.deliver("bad") { throw IllegalStateException("PNG unavailable") })
        assertTrue(delivery.deliver("good") { writes++ })
        assertTrue(delivery.deliver("good") { error("must not duplicate") })
        assertEquals(1,writes);assertEquals(1,delivery.failed)
        val cancelled=PhotoDeliverySession(emptySet(),WorkflowControl(cancelRequested={true}))
        assertThrows(WorkflowException::class.java) { runBlocking { cancelled.deliver("later") { error("no write") } } }
        Unit
    }
}
