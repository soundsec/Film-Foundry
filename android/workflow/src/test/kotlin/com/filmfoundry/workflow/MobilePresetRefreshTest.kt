package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.io.File

class MobilePresetRefreshTest {
    @Test fun currentBuiltinsUseSupportedFormationAndCreativeMaterialsStayInSync() {
        val assets=File("../app/src/main/assets")
        val catalog=BuiltinRecipeCatalog.read(File(assets,"phone_builtin_recipes.json").readBytes())
        val pairs=PhoneCreativePairings.read(File(assets,"phone_creative_pairings.json").readBytes())
        val source=RgbVolume(24,16,linearRgb=FloatArray(24*16*3) { if(it/3%24<12) (it%13)/16f else .9f })
        val cpu=PhoneReducedKernel()
        for(stock in catalog.materials) {
            val r=stock.recipe+mapOf(PhoneFormationProfile.OPTION to PhoneFormationProfile.LIGHTWEIGHT,
                PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
                PhoneMaterialPhenomena.EXPOSURE_OPTION to PhoneMaterialPhenomena.CURRENT,
                PhoneMaterialPhenomena.DEVELOPMENT_OPTION to PhoneMaterialPhenomena.CURRENT,
                "develop" to mapOf("program_key" to "auto"),"scanner" to PhoneFinishRecipes.scanner(PhoneFinishRecipes.AUTO))
            val latent=cpu.expose(source,r);val medium=cpu.develop(latent,r)
            assertTrue(cpu.observe(medium,r).outputRgb.values.all {it.isFinite() && it in 0f..1f},stock.id)
            val film=stock.recipe["film"] as Map<*,*>
            if(film["time_response"]!=null)assertNotNull(latent.metadata["phone_exposure_time"])
        }
        assertTrue(catalog.materials.any {it.id=="monochrome_ortho_negative"})
        assertTrue(catalog.materials.any {it.id=="monochrome_blue_sensitive_negative"})
        val preview=CameraEffectsPreviewCache()
        val mono=listOf("monochrome_ortho_negative","monochrome_blue_sensitive_negative").map { id ->
            @Suppress("UNCHECKED_CAST") val film=catalog.materials.single {it.id==id}.recipe["film"] as Map<String,Any?>
            preview.render(CameraEffectsPreview.Request(film,capture=PhoneMaterialDefaults.previewCaptureSettings(),longEdge=64),emptyMap()).values
        }
        assertFalse(mono[0].contentEquals(mono[1]),"Stock previews must use the new declared monochrome response")
        for(pair in pairs) assertEquals(catalog.materials.single {it.id==pair.materialId}.recipe["film"],pair.recipe["film"])
    }
}
