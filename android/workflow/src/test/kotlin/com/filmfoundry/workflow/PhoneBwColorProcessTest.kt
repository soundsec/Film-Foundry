package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import com.filmfoundry.kernel.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PhoneBwColorProcessTest {
    private val k=PhoneReducedKernel()
    private val film=mapOf("color_process" to "monochrome","image_polarity" to "negative",
        "film_base_density_rgb" to listOf(.04,.04,.04),"retained_halide_density_rgb" to listOf(0.0,0.0,0.0),"grain_strength" to 0.0)
    private val rgb=RgbVolume(5,1,linearRgb=floatArrayOf(.0001f,.0001f,.0001f,.02f,.02f,.02f,.18f,.18f,.18f,1f,1f,1f,32f,32f,32f))
    @Test fun bwInCompleteColorProcessesLosesSilverWithoutFabricatingDyesAndRetainingSilverMakesANegative() {
        val latent=k.expose(rgb,mapOf("film" to film,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT))
        assertFalse(latent.arrays.containsKey("coupler"));assertFalse(latent.arrays.containsKey("dye"))
        val original=latent.arrays.mapValues { it.value.values.copyOf() }
        for(key in listOf("color_negative","color_reversal","color_negative_bleach_bypass","bw_negative","bw_reversal")) {
            val develop=mapOf("program_key" to key)
            val recipe=mapOf("film" to film,"develop" to develop,PhoneProcessModels.OPTION to PhoneProcessModels.CURRENT)
            val m=k.develop(latent,recipe);val advice=RecipeDrafts.inspectProcess(film,develop)
            val cleared=key in listOf("color_negative","color_reversal")
            assertEquals(!cleared,advice.variableImage,key)
            assertFalse("dye" in advice.components)
            assertEquals("bw_silver",m.metadata["color_system"])
            assertEquals(if(cleared) "no_developed_image" else "image_bearing",m.metadata["image_formation_state"])
            if(cleared) assertTrue(m.opticalDensityRgb.values.all { it==.04f })
            else {
                assertTrue("metallic_silver" in advice.components)
                assertEquals(if(advice.positive) "positive" else "negative",m.metadata["image_polarity"])
                assertTrue(m.opticalDensityRgb.values[0]!=m.opticalDensityRgb.values[12])
            }
            original.forEach { (name,values)->assertArrayEquals(values,latent.arrays.getValue(name).values,0f) }
        }
    }
    @Test fun retainedButUniformSilverInAReversalSequenceIsNotPresentedAsAnImage() {
        val steps=ProcessSteps.template("color_reversal").map { if(it["action"]=="bleach_silver") it+("strength" to 0.0) else it }
        val develop=mapOf("program_key" to "custom","process_steps" to steps)
        val advice=RecipeDrafts.inspectProcess(film,develop)
        assertTrue("metallic_silver" in advice.components)
        assertFalse(advice.variableImage,"First and second silver images add to uniform silver; retention alone cannot invent contrast")
    }
    @Test fun materialTypeNamesAndProcessChoicesAreDistinctWithoutChangingSavedIds() {
        assertEquals("黑白负片 · 自己的配方",MaterialSelection.displayName(film,"自己的配方"))
        assertEquals("B&W negative · Fine grain",MaterialSelection.displayName(film,"B&W negative · Fine grain",true))
        assertEquals("黑白反转片",MaterialSelection.displayName(film+("image_polarity" to "positive"),""))
        val entries=PhoneProcessPresets.entries.associateBy { it.id }
        assertEquals("color_negative",entries.getValue("phone_color_negative").recipe["program_key"])
        assertEquals("bw_negative",entries.getValue("phone_bw_negative").recipe["program_key"])
        assertEquals("color_negative_bleach_bypass",entries.getValue("phone_silver_bypass").recipe["program_key"])
        assertFalse(PhoneProcessPresets.visible().any { it.id=="phone_slide_negative" })
        assertTrue(PhoneProcessPresets.visible("phone_slide_negative").any { it.id=="phone_slide_negative" })
    }
}
