package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class PreparedRowsTest {
    private val kernel = PhoneReducedKernel()
    private fun spectralRecipe(): Map<String, Any?> = RecipeShareCodec.decode(
        javaClass.getResourceAsStream("/spectral_exposure_sample.ffc2")!!.bufferedReader().use { it.readText() })

    @Test fun exposureTaskDetachesRecipeAndEachResultOwnsItsArrays() {
        val look = mutableMapOf<String, Any?>("exposure_ev" to 0.0)
        val film = (spectralRecipe()["film"] as Map<*, *>).entries.associate { it.key.toString() to it.value }.toMutableMap()
        val raw = (film[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] as Map<*, *>).entries
            .associate { it.key.toString() to it.value }.toMutableMap()
        val gain = (raw["layer_gain"] as List<*>).toMutableList()
        raw["layer_gain"] = gain
        film[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] = raw
        val recipe = mapOf("film" to film, "look" to look)
        val rgb = RgbVolume(3, 2, linearRgb = FloatArray(18) { .4f })
        val prepared = kernel.prepareExposure(recipe, 3, 5)
        val before = prepared.exposeRows(rgb)
        look["exposure_ev"] = 2.0
        gain[0] = 7.0
        val after = prepared.exposeRows(rgb)
        assertEquals(before.metadata, after.metadata)
        for (key in before.arrays.keys) assertArrayEquals(before.arrays.getValue(key).values, after.arrays.getValue(key).values, 0f)
        before.arrays.getValue("halide").values.fill(0f)
        assertTrue(after.arrays.getValue("halide").values.all { it == 1f })
        val changed = kernel.prepareExposure(recipe, 3, 5).exposeRows(rgb)
        assertFalse(changed.arrays.getValue("developability").values.contentEquals(after.arrays.getValue("developability").values))
        assertThrows(IllegalArgumentException::class.java) { prepared.exposeRows(RgbVolume(4, 1)) }
        @Suppress("UNCHECKED_CAST")
        val frozen = after.metadata[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] as MutableMap<String, Any?>
        assertThrows(UnsupportedOperationException::class.java) { frozen["profile_id"] = "tampered" }
    }

    @Test fun developmentKeepsFrozenSourceAndRejectsOtherDefinitionsBeforeConsumption() {
        val latent = kernel.expose(RgbVolume(3, 2, linearRgb = FloatArray(18) { .4f }), spectralRecipe())
        fun fresh() = LatentState(latent.arrays.mapValues { it.value.copy() }, latent.metadata.toMutableMap())
        val develop = mutableMapOf<String, Any?>("time_min" to 2.5)
        // The current recipe's exposure definition is deliberately invalid: it is not the source.
        val recipe = mapOf("develop" to develop, "film" to mapOf("spectral_exposure_profile" to mapOf("model" to "unused")))
        val expected = kernel.develop(fresh(), recipe)
        val first = fresh()
        val prepared = kernel.prepareDevelopment(recipe, first, 5)
        develop["time_min"] = 12.0
        val actual = prepared.developRows(first)
        assertArrayEquals(expected.opticalDensityRgb.values, actual.opticalDensityRgb.values, 0f)
        assertEquals(expected.metadata, actual.metadata)
        val other = fresh()
        val source = (other.metadata[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] as Map<*, *>).toMutableMap()
        source["profile_id"] = "different-source"
        val wrong = LatentState(other.arrays, other.metadata + (DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY to source))
        val before = wrong.arrays.getValue("halide").values.copyOf()
        assertThrows(KernelException::class.java) { prepared.developRows(wrong) }
        assertArrayEquals(before, wrong.arrays.getValue("halide").values, 0f)
        val second = prepared.developRows(fresh())
        actual.opticalDensityRgb.values.fill(0f)
        actual.clearBaseOpticalDensityRgb!!.fill(0f)
        assertArrayEquals(expected.opticalDensityRgb.values, second.opticalDensityRgb.values, 0f)
        assertArrayEquals(expected.clearBaseOpticalDensityRgb, second.clearBaseOpticalDensityRgb, 0f)
    }
}
