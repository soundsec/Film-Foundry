package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class BaseBleachTest {
    @Test fun bleachingRemovesMaskButKeepsColoredSupportAndScanningCannotChangeEither() {
        val k=PhoneReducedKernel()
        val film=mapOf("film_base_density_rgb" to listOf(.3,.5,.7),"clear_support_density_rgb" to listOf(.08,.2,.04),
            "experimental_mask_bleach_susceptibility" to 1.0,"experimental_mask_bleach_dye_damage" to .1)
        val recipe=mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
            "develop" to mapOf("program_key" to "color_reversal","chemistry" to "cross"))
        val latent=k.expose(RgbVolume(3,1,linearRgb=FloatArray(9) { (it/3+1)*.12f }),recipe)
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val keep=k.develop(latent,recipe)
        val zero=k.develop(latent,recipe+("develop" to mapOf("program_key" to "color_reversal","chemistry" to "cross","mask_bleach_completion" to 0.0)))
        assertArrayEquals(keep.opticalDensityRgb.values,zero.opticalDensityRgb.values,0f)
        val clearRecipe=recipe+("develop" to mapOf("program_key" to "color_reversal","chemistry" to "cross","mask_bleach_completion" to 1.0))
        val clear=k.develop(latent,clearRecipe)
        assertEquals("positive",clear.metadata["image_polarity"])
        assertArrayEquals(floatArrayOf(.08f,.2f,.04f),clear.clearBaseOpticalDensityRgb!!,1e-6f)
        assertFalse(keep.opticalDensityRgb.values.contentEquals(clear.opticalDensityRgb.values))
        val density=clear.opticalDensityRgb.values.copyOf()
        for(mask in listOf(true,false)) for(mode in listOf("scanner","viewer_light_table")) {
            val result=k.observe(clear,clearRecipe+mapOf("scanner" to mapOf("remove_base_mask" to mask),"observe_mode" to mode))
            assertTrue(result.outputRgb.values.all { it.isFinite() });assertArrayEquals(density,clear.opticalDensityRgb.values,0f)
            assertArrayEquals(floatArrayOf(.08f,.2f,.04f),clear.clearBaseOpticalDensityRgb,1e-6f)
        }
        before.forEach { (key,v)->assertArrayEquals(v,latent.arrays.getValue(key).values,0f) }
        assertThrows(IllegalArgumentException::class.java) { ProcessSteps.validateConditions(mapOf("mask_bleach_completion" to 1.1)) }
    }
    @Test fun spectralMaskComponentIsBleachedAndSupportComponentSurvivesArchive() {
        val k=PhoneReducedKernel();val codec=NpzMediumArchive()
        val r=RecipeShareCodec.decode(javaClass.getResource("/spectral_optics_sample.ffc2")!!.readText())+
            (PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
        val latent=k.expose(RgbVolume(3,2,linearRgb=FloatArray(18){.2f}),r)
        val clear=k.develop(latent,r+("develop" to mapOf("program_key" to "color_reversal","mask_bleach_completion" to 1.0)))
        val a=clear.spectralComponentAmounts!!
        for(i in 0 until 6) { assertEquals(0f,a.values[i*9+7],0f);assertTrue(a.values[i*9+6]>0) }
        val bytes=codec.writeDevelopedOptical(DevelopedOpticalArchive(clear.opticalDensityRgb,clear.metadata,clear.clearBaseOpticalDensityRgb,a))
        val saved=codec.readDevelopedOptical(bytes);assertArrayEquals(a.values,saved.spectralComponentAmounts!!.values,0f)
        assertTrue(k.observe(clear,r).outputRgb.values.all { it.isFinite() });assertArrayEquals(a.values,saved.spectralComponentAmounts!!.values,0f)
    }
}
