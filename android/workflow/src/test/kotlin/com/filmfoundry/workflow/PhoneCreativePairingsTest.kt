package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import java.io.File
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneCreativePairingsTest {
    private val expected=mapOf("chrome_day" to "positive","amber_city" to "negative","violet_cross" to "negative","acid_reversal" to "positive","cobalt_red" to "negative","silver_noir" to "negative")
    @Test fun currentModelPairingsKeepPoolsAndBaseAndInterchangePolarityWithoutScannerRepair() {
        val entries=PhoneCreativePairings.read(File("../app/src/main/assets/phone_creative_pairings.json").readBytes())
        assertEquals(expected.keys,entries.map { it.id }.toSet())
        val kernel=PhoneReducedKernel();val codec=NpzMediumArchive()
        val rgb=RgbVolume(32,2,linearRgb=FloatArray(32*2*3) { (it/3%32)/4f })
        for(entry in entries) {
            val recipe=entry.recipe+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
            val develop=recipe["develop"] as Map<String,Any?>;RecipeDrafts.process(develop)
            val latent=kernel.expose(rgb,recipe);val pools=latent.arrays.mapValues { it.value.values.copyOf() }
            val optical=kernel.develop(latent,recipe)
            assertEquals(expected[entry.id],optical.metadata["image_polarity"],entry.id)
            pools.forEach { (key,value)->assertArrayEquals(value,latent.arrays.getValue(key).values,0f) }
            val archive=codec.readDevelopedOptical(codec.writeDevelopedOptical(DevelopedOpticalArchive(optical.opticalDensityRgb,optical.metadata,
                optical.clearBaseOpticalDensityRgb,optical.spectralComponentAmounts,optical.spectralPostProcessDensityRgb)))
            assertEquals(expected[entry.id],archive.metadata["image_polarity"])
            assertArrayEquals(optical.clearBaseOpticalDensityRgb,archive.clearBaseOpticalDensityRgb,0f)
            val before=optical.opticalDensityRgb.values.copyOf()
            // Polarity must work without the pairing's explicit optional inversion choice.
            val scanner=(recipe["scanner"] as Map<String,Any?>)-"invert_transmission"
            val reloaded=OpticalMaster(archive.opticalDensityRgb,archive.metadata,archive.clearBaseOpticalDensityRgb,archive.spectralComponentAmounts,archive.spectralPostProcessDensityRgb)
            val scanned=kernel.observe(reloaded,recipe+("scanner" to scanner)).outputRgb.values
            assertArrayEquals(kernel.observe(optical,recipe+("scanner" to scanner)).outputRgb.values,scanned,0f)
            fun y(i: Int)=.2126f*scanned[i]+.7152f*scanned[i+1]+.0722f*scanned[i+2]
            assertTrue(y(0)<y(31*3),"${entry.id} scanner follows formed polarity")
            assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
            if(entry.id=="acid_reversal") assertTrue(archive.clearBaseOpticalDensityRgb!!.max()>.5f,"orange stock base must remain")
            if(develop["chemistry"]=="cross") {
                val native=kernel.develop(latent,recipe+("develop" to mapOf("program_key" to "auto","chemistry" to "native","time_min" to 8.0)))
                assertFalse(native.opticalDensityRgb.values.contentEquals(before),"${entry.id} must change formation, not just scan")
            }
        }
    }
    @Test fun currentNativeColorAndBwReversalUseTheirFormedPolarityInAllViewingModes() {
        val catalog=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        val kernel=PhoneReducedKernel()
        for(id in listOf("color_reversal_transparency","monochrome_reversal_transparency")) {
            val recipe=catalog.materials.first { it.id==id }.recipe+mapOf(PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT,
                "develop" to mapOf("program_key" to "auto","chemistry" to "native","time_min" to 8.0),"scanner" to PhoneFinishRecipes.scanner("ready"))
            val optical=kernel.develop(kernel.expose(RgbVolume(3,1,linearRgb=floatArrayOf(.001f,.001f,.001f,.18f,.18f,.18f,8f,8f,8f)),recipe),recipe)
            assertEquals("positive",optical.metadata["image_polarity"])
            val before=optical.opticalDensityRgb.values.copyOf()
            for(mode in listOf("viewer_light_table","viewer_projection","scanner")) {
                val seen=kernel.observe(optical,recipe+("observe_mode" to mode)).outputRgb.values
                assertTrue(seen[0]<seen[6],"$id / $mode");assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
            }
        }
    }
}
