package com.filmfoundry.workflow

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class RecipeInputTest {
    @Test fun cachedShareValuesAreDetachedAndMalformedInputNeverReturnsThePreviousRecipe() {
        val input=RecipeInput();val values=mutableListOf<Any?>(.1,.2,.3)
        val code=RecipeShareCodec.encode(mapOf("film" to mapOf("film_base_density_rgb" to values),
            "develop" to mapOf("mask_bleach_completion" to .8,"process_steps" to ProcessSteps.template("color_reversal"))))
        val parsed=input.read(code);values[0]=8.0
        assertSame(parsed,input.read(" $code\n"))
        assertThrows(UnsupportedOperationException::class.java) { (parsed as MutableMap<String,Any?>).clear() }
        val layers=(parsed["film"] as Map<*,*>)["film_base_density_rgb"] as List<*>
        assertThrows(UnsupportedOperationException::class.java) { (layers as MutableList<Any?>)[0]=9.0 }
        assertThrows(IllegalArgumentException::class.java) { input.read("FFC2:bad") }
        assertSame(parsed,input.read(code));assertTrue(input.read("").isEmpty())
        assertTrue(RecipeJson.equivalent(parsed,RecipeShareCodec.decode(RecipeShareCodec.encode(parsed))))
    }
    @Test fun dailyPresetsHideCompatibilityEntriesButRetainTheirFrozenValuesAndSelectedIds() {
        assertFalse(PhoneProcessPresets.visible().any { it.id=="phone_native_4" })
        assertTrue(PhoneProcessPresets.visible("phone_native_4").any { it.id=="phone_native_4" })
        assertEquals(4.0,PhoneProcessPresets.entries.first { it.id=="phone_native_4" }.recipe["time_min"])
        assertEquals(1.0,PhoneProcessPresets.visible().first { it.id=="phone_negative_clear_reversal" }.recipe["mask_bleach_completion"])
        val catalog=BuiltinRecipeCatalog.read(java.io.File("../app/src/main/assets/phone_builtin_recipes.json").readBytes())
        assertTrue(catalog.dailyScanners().size<catalog.scanners.size)
        val hidden=catalog.scanners.first { it !in catalog.dailyScanners() }
        assertTrue(hidden in catalog.dailyScanners(hidden.id))
    }
}
