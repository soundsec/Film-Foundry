package com.filmfoundry.kernel

import com.filmfoundry.interchange.BuiltinRecipeCatalog
import java.io.File
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class BuiltinPhoneRecipeTest {
    @Test fun allBundledScannerStylesEstablishBlackInsteadOfLegacyGreyWithoutChangingCustomRecipes() {
        val catalog=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        val kernel=PhoneReducedKernel()
        val input=RgbVolume(128,1,linearRgb=FloatArray(128*3) { (it/3)/127f })
        val optical=kernel.develop(kernel.expose(input,emptyMap()),emptyMap())
        val before=optical.opticalDensityRgb.values.copyOf()
        for(entry in catalog.scanners) {
            val scanner=entry.recipe["scanner"] as Map<*,*>
            assertEquals("sigmoid",scanner["print_mapping_mode"])
            val rgb=kernel.observe(optical,entry.recipe).outputRgb.values
            assertTrue(rgb[0]<.03f,"${entry.id} retains a grey pedestal")
            assertTrue(rgb.last()>.9f,entry.id)
            assertArrayEquals(before,optical.opticalDensityRgb.values,0f)
        }
        // Explicit legacy/custom maps continue to use their frozen readout, even after asset updates.
        val legacy=mapOf("scanner" to mapOf("scan_normalize_strength" to .45))
        assertTrue(kernel.observe(optical,legacy).outputRgb.values[0]>.25f)
    }
    @Test fun bundledMaterialsFormDifferentMastersAndScannerCurvesOnlyChangeReadout() {
        val catalog=BuiltinRecipeCatalog.read(File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        val kernel=PhoneReducedKernel()
        val input=RgbVolume(6,4,linearRgb=FloatArray(72){ .01f+(it%23)*.04f })
        val masters=catalog.materials.map { material ->
            kernel.develop(kernel.expose(input,material.recipe),material.recipe).also {
                assertArrayEquals(intArrayOf(4,6,3),it.opticalDensityRgb.shape)
                assertTrue(it.opticalDensityRgb.values.all { v -> v.isFinite() && v >= 0 })
            }
        }
        for(i in masters.indices) for(j in 0 until i) assertFalse(masters[i].opticalDensityRgb.values.contentEquals(masters[j].opticalDensityRgb.values))
        val master=masters.first();val original=master.opticalDensityRgb.values.copyOf()
        val scans=catalog.scanners.map { kernel.observe(master,it.recipe).outputRgb.values.also { rgb -> assertTrue(rgb.all { v -> v.isFinite() && v in 0f..1f }) } }
        assertFalse(scans[0].contentEquals(scans[2]));assertFalse(scans[2].contentEquals(scans[3]))
        assertArrayEquals(original,master.opticalDensityRgb.values)
    }
}
