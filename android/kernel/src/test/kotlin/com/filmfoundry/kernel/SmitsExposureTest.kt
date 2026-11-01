package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.json.JSONObject
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test

class SmitsExposureTest {
    private fun fixture() = JSONObject(javaClass.getResourceAsStream("/smits7_exposure_sample.json")!!.bufferedReader().use { it.readText() })
    @Test fun sevenBasisMatchesExplicitIntegrationForSixOrdersTiesNegativeAndHdr() {
        val json = fixture()
        val codec = NpzMediumArchive()
        // Recipe JSON travels through the same portable FFC2 codec as UI-created profiles.
        val profile = json.getJSONObject("profile").toMap()
        val recipe = mapOf<String, Any?>("film" to mapOf("spectral_exposure_profile" to profile))
        val shared = RecipeShareCodec.decode(RecipeShareCodec.encode(recipe))
        val setup = PhoneSpectralExposure.from((shared["film"] as Map<*, *>)["spectral_exposure_profile"], 3)
        val pixels = json.getJSONArray("pixels")
        val rgb = FloatArray(pixels.length()*3) { i -> pixels.getJSONArray(i/3).getDouble(i%3).toFloat() }
        val kernel = PhoneReducedKernel()
        val whole = kernel.expose(RgbVolume(pixels.length(), 1, linearRgb=rgb), shared)
        val before = whole.arrays.mapValues { it.value.values.copyOf() }
        for (p in 0 until pixels.length()) for (layer in 0..2) {
            val e = json.getJSONArray("expected").getJSONArray(p).getDouble(layer).toFloat()
            assertEquals(e, setup.exposure(maxOf(rgb[p*3],0f), maxOf(rgb[p*3+1],0f), maxOf(rgb[p*3+2],0f), layer), 3e-7f)
            val floored = maxOf(e, 1e-6f)
            assertEquals(floored/(floored+PhoneReducedKernel.HALF_SATURATION/1.08f), whole.arrays.getValue("developability").values[p*3+layer], 2e-7f)
        }
        val loaded = codec.readLatent(codec.writeLatent(LatentArchive(whole.arrays,whole.metadata)))
        val optical = kernel.develop(LatentState(loaded.arrays,loaded.metadata), emptyMap())
        val process = optical.metadata["film_process_model"] as Map<*, *>
        val implementation = process["execution_implementation"] as Map<*, *>
        assertEquals(DataKernel.SMITS_EXPOSURE_MODEL_ID, (implementation["component_models"] as Map<*, *>)["exposure_mapping"])
        for ((key, values) in before) assertArrayEquals(values, whole.arrays.getValue(key).values,0f)
        for (p in 0 until pixels.length()) {
            val row = kernel.exposeRows(RgbVolume(1,1,linearRgb=rgb.copyOfRange(p*3,p*3+3)),shared,pixels.length())
            assertArrayEquals(whole.arrays.getValue("developability").values.copyOfRange(p*3,p*3+3),row.arrays.getValue("developability").values,0f)
        }
    }
}
