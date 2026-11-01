package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class MaterialBindingTest {
    private val kernel=PhoneReducedKernel()
    private val rgb=RgbVolume(3,1,linearRgb=floatArrayOf(.02f,.03f,.04f,.18f,.18f,.18f,1f,.8f,.6f))
    private val options=mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
        PhoneOpticalDensityModels.OPTION to PhoneOpticalDensityModels.CURRENT)
    @Test fun frozenFilmReplacesCallerStockWhileBathChoiceRemainsEditable() {
        val film=mapOf("name" to "Source","image_polarity" to "negative","film_base_density_rgb" to listOf(.17,.51,.82),
            "density_max" to listOf(2.1,1.9,2.0),"grain_strength" to 0.0)
        val latent=kernel.expose(rgb,options+("film" to film))
        val legacy=LatentState(latent.arrays,latent.metadata-DataKernel.MATERIAL_SNAPSHOT_KEY)
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        for(program in listOf("color_negative","color_reversal","bw_negative")) {
            val original=options+mapOf("film" to film,"develop" to mapOf("program_key" to program))
            val wrong=original+("film" to mapOf("color_process" to "monochrome","image_polarity" to "positive",
                "film_base_density_rgb" to listOf(1.0,1.0,1.0),"density_max" to listOf(4.0,4.0,4.0)))
            val expected=kernel.develop(legacy,original)
            val actual=kernel.develop(latent,wrong)
            assertArrayEquals(expected.opticalDensityRgb.values,actual.opticalDensityRgb.values,0f,program)
            assertEquals("latent_snapshot",(actual.metadata["material_binding"] as Map<*,*>)["source"])
            before.forEach { (key,values)->assertArrayEquals(values,latent.arrays.getValue(key).values,0f) }
        }
    }
    @Test fun absentSnapshotUsesCallerLegacyButSparseSnapshotDoesNotMergeAnotherStock() {
        val latent=kernel.expose(rgb,options)
        val legacy=LatentState(latent.arrays,latent.metadata-DataKernel.MATERIAL_SNAPSHOT_KEY)
        val original=options+("develop" to mapOf("program_key" to "color_negative"))
        val wrong=original+("film" to mapOf("film_base_density_rgb" to listOf(1.0,1.0,1.0),"density_max" to listOf(4.0,4.0,4.0)))
        val expected=kernel.develop(legacy,original)
        assertArrayEquals(expected.opticalDensityRgb.values,kernel.develop(latent,wrong).opticalDensityRgb.values,0f)
        val fallback=kernel.develop(legacy,wrong)
        assertFalse(expected.opticalDensityRgb.values.contentEquals(fallback.opticalDensityRgb.values))
        assertEquals("caller_legacy",(fallback.metadata["material_binding"] as Map<*,*>)["source"])
    }
    @Test fun invalidSnapshotFailsBeforeMaterialConsumptionAndCodecRejectsUnknownVersion() {
        val latent=kernel.expose(rgb,options)
        val bad=LatentState(latent.arrays,latent.metadata+(DataKernel.MATERIAL_SNAPSHOT_KEY to mapOf("model" to "future","film" to emptyMap<String,Any?>())))
        val before=latent.arrays.mapValues { it.value.values.copyOf() }
        assertThrows(IllegalArgumentException::class.java) { kernel.develop(bad,options,true) }
        assertThrows(IllegalArgumentException::class.java) { NpzMediumArchive().writeLatent(LatentArchive(bad.arrays,bad.metadata)) }
        before.forEach { (key,values)->assertArrayEquals(values,latent.arrays.getValue(key).values,0f) }
    }
}
