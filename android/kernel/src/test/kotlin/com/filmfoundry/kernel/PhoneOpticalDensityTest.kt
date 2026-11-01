package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneOpticalDensityTest {
    @Test fun normalizedReversalDyeReachesTheDeclaredDensityWithoutChangingLatentOrLegacyMasters() {
        val k=PhoneReducedKernel()
        val film=mapOf("image_polarity" to "positive","grain_strength" to 0.0,"density_max" to listOf(2.4,2.2,2.0),
            "film_base_density_rgb" to listOf(.035,.035,.035),"dye_absorption_matrix" to List(3) { y->List(3) { x->if(x==y) 1.0 else 0.0 } })
        val recipe=mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,"develop" to mapOf("program_key" to "color_reversal"))
        val latent=k.expose(RgbVolume(3,1,linearRgb=floatArrayOf(0f,0f,0f,.18f,.18f,.18f,100f,100f,100f)),recipe)
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        val legacy=k.develop(latent,recipe)
        val current=k.develop(latent,recipe+(PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT))
        assertEquals(2.435f,current.opticalDensityRgb.values[0],1e-5f)
        assertEquals(1.235f,legacy.opticalDensityRgb.values[0],1e-5f)
        assertEquals("positive",current.metadata["image_polarity"])
        for(i in 0..8) assertEquals(2*(legacy.opticalDensityRgb.values[i]-.035f)+.035f,current.opticalDensityRgb.values[i],3e-7f)
        before.forEach { (key,values)->assertArrayEquals(values,latent.arrays.getValue(key).values,0f) }
        assertArrayEquals(legacy.opticalDensityRgb.values,k.develop(latent,recipe).opticalDensityRgb.values,0f)
        val density=current.opticalDensityRgb.values.copyOf();k.observe(current,emptyMap());assertArrayEquals(density,current.opticalDensityRgb.values,0f)
    }
    @Test fun spectralDyeAmountsUseTheSameRangeWhileBaseAndOtherComponentsRemainUnchanged() {
        val k=PhoneReducedKernel();val source=RecipeShareCodec.decode(javaClass.getResource("/spectral_optics_sample.ffc2")!!.readText())
        @Suppress("UNCHECKED_CAST") val film=(source["film"] as Map<String,Any?>)+mapOf("density_max" to listOf(2.4,2.2,2.0),"grain_strength" to 0.0)
        val recipe=source+mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,"develop" to mapOf("program_key" to "color_negative"))
        val latent=k.expose(RgbVolume(3,1,linearRgb=FloatArray(9) { .18f }),recipe)
        val old=k.develop(latent,recipe);val current=k.develop(latent,recipe+(PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT))
        val a=old.spectralComponentAmounts!!.values;val b=current.spectralComponentAmounts!!.values
        for(i in a.indices) assertEquals(if(i%9<3) a[i]*2 else a[i],b[i],1e-7f)
        assertArrayEquals(old.clearBaseOpticalDensityRgb,current.clearBaseOpticalDensityRgb,0f)
        val codec=NpzMediumArchive();val saved=codec.readDevelopedOptical(codec.writeDevelopedOptical(DevelopedOpticalArchive(current.opticalDensityRgb,current.metadata,current.clearBaseOpticalDensityRgb,current.spectralComponentAmounts)))
        assertArrayEquals(current.opticalDensityRgb.values,saved.opticalDensityRgb.values,0f)
        val process=saved.metadata["film_process_model"] as Map<*,*>
        assertEquals(PhoneOpticalDensityModels.CURRENT,((process["execution_implementation"] as Map<*,*>)["component_models"] as Map<*,*>)["optical"])
    }
}
