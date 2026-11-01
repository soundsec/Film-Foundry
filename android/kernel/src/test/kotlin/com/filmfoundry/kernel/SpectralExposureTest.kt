package com.filmfoundry.kernel

import com.filmfoundry.interchange.*
import org.junit.jupiter.api.Assertions.*
import org.junit.jupiter.api.Test
import java.nio.file.Files
import java.nio.file.Path

class SpectralExposureTest {
    private val kernel = PhoneReducedKernel()
    private fun recipe(): Map<String, Any?> {
        val text = javaClass.getResourceAsStream("/spectral_exposure_sample.ffc2")!!.bufferedReader().use { it.readText() }
        return RecipeShareCodec.decode(text)
    }

    @Suppress("UNCHECKED_CAST")
    private fun profile(recipe: Map<String, Any?>): Map<String, Any?> =
        (recipe["film"] as Map<String, Any?>)[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY] as Map<String, Any?>

    @Test
    fun setupMatchesAnalyticMatrixAndPhoneUsesItBeforeHalfSaturation() {
        val recipe = recipe()
        val setup = PhoneSpectralExposure.from(profile(recipe), 3)
        val expected = arrayOf(
            floatArrayOf(0.72f, 0.30f, 0.06f),
            floatArrayOf(0.384f, 0.663f, 0.342f),
            floatArrayOf(0.088f, 0.28f, 0.264f),
        )
        for (layer in 0 until 3) assertArrayEquals(expected[layer], setup.matrix[layer], 1e-7f)
        val image = RgbVolume(3, 1, linearRgb = floatArrayOf(1f, 0f, 0f, 0f, 1f, 0f, 0f, 0f, 1f))
        val latent = kernel.expose(image, recipe)
        val half = PhoneReducedKernel.HALF_SATURATION / 1.08f
        for (pixel in 0 until 3) for (layer in 0 until 3) {
            val exposure = expected[layer][pixel]
            assertEquals(exposure / (exposure + half), latent.arrays.getValue("developability").values[pixel * 3 + layer], 1e-7f)
        }
    }

    @Test
    fun portableLatentFreezesProfileAndDevelopKeepsReducedModelIdentity() {
        val recipe = recipe()
        val image = RgbVolume(3, 2, linearRgb = FloatArray(18) { 0.4f })
        val physicalRecipe = recipe + ("film" to ((recipe["film"] as Map<*, *>) + ("spatial_reference_short_edge_mm" to 24.0)))
        val latent = kernel.expose(image, physicalRecipe)
        val codec = NpzMediumArchive()
        val bytes = codec.writeLatent(LatentArchive(latent.arrays, latent.metadata))
        val read = codec.readLatent(bytes)
        val state = LatentState(read.arrays, read.metadata)
        val changed = recipe + ("film" to mapOf("spectral_exposure_profile" to mapOf("model" to "unused_later_exposure")))
        val first = kernel.develop(state, recipe)
        val second = kernel.develop(state, changed)
        assertArrayEquals(first.opticalDensityRgb.values, second.opticalDensityRgb.values, 0f)
        val process = second.metadata["film_process_model"] as Map<*, *>
        val frozen = PhoneSpectralExposure.from(process[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY], 3)
        assertEquals(PhoneSpectralExposure.from(profile(recipe), 3).metadata, frozen.metadata)
        val implementation = process["execution_implementation"] as Map<*, *>
        assertEquals(DataKernel.PHONE_REDUCED_PROFILE_ID, implementation["profile_id"])
        val models = implementation["component_models"] as Map<*, *>
        assertEquals(DataKernel.SPECTRAL_EXPOSURE_MODEL_ID, models["exposure_mapping"])
        val opticalBytes = codec.writeDevelopedOptical(DevelopedOpticalArchive(
            second.opticalDensityRgb, second.metadata, second.clearBaseOpticalDensityRgb,
        ))
        val output = System.getenv("FILM_FOUNDRY_INTEROP_OUTPUT")
        if (output != null) {
            val directory = Path.of(output)
            Files.createDirectories(directory)
            Files.write(directory.resolve("phone_spectral.darkroom_latent.npz"), bytes)
            Files.write(directory.resolve("phone_spectral.darkroom_negative.npz"), opticalBytes)
        }
    }

    @Test
    fun monochromeUsesOneSpectralSensitivityAndRejectsThreeLayerMismatch() {
        val recipe = recipe()
        val raw = profile(recipe)
        val single = raw + mapOf(
            "layer_sensitivity" to (raw["layer_sensitivity"] as List<*>).take(1),
            "layer_gain" to listOf(1.0),
        )
        val mono = mapOf<String, Any?>("enable_subtractive" to false, "film" to mapOf("spectral_exposure_profile" to single))
        val image = RgbVolume(1, 1, linearRgb = floatArrayOf(1f, 0f, 0f))
        val latent = kernel.expose(image, mono)
        assertEquals(1, latent.arrays.getValue("developability").shape[2])
        assertEquals(0.72f / (0.72f + PhoneReducedKernel.HALF_SATURATION / 1.08f), latent.arrays.getValue("developability").values[0], 1e-7f)
        val declaredMono = kernel.expose(image, mapOf("film" to mapOf(
            "color_process" to "bw", "spectral_exposure_profile" to single,
        )))
        assertArrayEquals(latent.arrays.getValue("developability").values, declaredMono.arrays.getValue("developability").values, 0f)
        assertThrows(KernelException::class.java) { kernel.expose(image, recipe + ("enable_subtractive" to false)) }
    }

    @Test
    fun badProfilesFailBeforeFormationOrConsumption() {
        val recipe = recipe()
        val raw = profile(recipe)
        val badProfiles = listOf(
            raw + ("model" to "unknown"), raw + ("working_space" to "prophoto"),
            raw + ("wavelengths_nm" to listOf(400, 450, 450, 700)),
            raw + ("layer_gain" to listOf(1.0, 0.0, 1.0)),
            raw + ("layer_gain" to listOf(1e300, 1e300, 1e300)),
        )
        val image = RgbVolume(1, 1, linearRgb = FloatArray(3) { 0.4f })
        for (bad in badProfiles) {
            val badRecipe = mapOf<String, Any?>("film" to mapOf("spectral_exposure_profile" to bad))
            assertThrows(KernelException::class.java) { kernel.expose(image, badRecipe) }
            val valid = kernel.expose(image, recipe)
            val badLatent = LatentState(valid.arrays, valid.metadata + (DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY to bad))
            val before = valid.arrays.getValue("halide").values.copyOf()
            assertThrows(KernelException::class.java) { kernel.develop(badLatent, emptyMap(), true) }
            assertArrayEquals(before, valid.arrays.getValue("halide").values, 0f)
        }
    }

    @Test
    fun desktopSpectralLatentCanBeDevelopedByPhoneWithoutReExposure() {
        val bytes = javaClass.getResourceAsStream("/spectral_latent_python_sample.darkroom_latent.npz")!!.use { it.readBytes() }
        val read = NpzMediumArchive().readLatent(bytes)
        val before = read.arrays.getValue("halide").values.copyOf()
        val optical = kernel.develop(LatentState(read.arrays, read.metadata), recipe())
        assertArrayEquals(before, read.arrays.getValue("halide").values, 0f)
        assertTrue(optical.opticalDensityRgb.values.all { it.isFinite() && it >= 0f })
        val process = optical.metadata["film_process_model"] as Map<*, *>
        val frozen = PhoneSpectralExposure.from(process[DataKernel.SPECTRAL_EXPOSURE_PROFILE_KEY], 3)
        assertEquals(PhoneSpectralExposure.from(profile(recipe()), 3).metadata, frozen.metadata)
        assertEquals(listOf(2, 3).toString(), (process[DataKernel.MATERIAL_GEOMETRY_KEY] as Map<*, *>)["full_shape"].toString())
    }
}
