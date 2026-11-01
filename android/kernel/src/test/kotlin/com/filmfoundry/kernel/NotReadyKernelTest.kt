package com.filmfoundry.kernel

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.interchange.NpzMediumArchive
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Test

class NotReadyKernelTest {
    @Test
    fun refusesToFabricateFormation() {
        val kernel = NotReadyKernel()
        assertThrows(KernelNotReady::class.java) {
            kernel.expose(RgbVolume(8, 8), emptyMap())
        }
        assertThrows(KernelNotReady::class.java) {
            kernel.develop(LatentState(emptyMap(), emptyMap()), emptyMap())
        }
        assertThrows(KernelNotReady::class.java) {
            kernel.observe(
                OpticalMaster(FloatNd(intArrayOf(1, 1, 3), floatArrayOf(0f, 0f, 0f)), emptyMap()),
                emptyMap(),
            )
        }
    }
}

class PhoneReducedKernelTest {
    private val kernel = PhoneReducedKernel()
    private val recipe = mapOf(
        "film" to mapOf("contrast" to 1.08, "name" to "Pocket Custom"),
        "develop" to mapOf("time_min" to 8.0),
        "look" to mapOf("exposure_ev" to 0.0),
        "scanner" to mapOf("scan_saturation" to 1.0),
        "enable_subtractive" to true,
        "frame_identity" to mapOf("roll_id" to "pocket-roll", "frame_index" to 1),
    )

    @Test
    fun brighterLinearExposureRaisesDevelopability() {
        val dark = kernel.expose(flat(0.04f), recipe)
        val bright = kernel.expose(flat(0.80f), recipe)
        val darkE = mean(dark.arrays.getValue("developability"))
        val brightE = mean(bright.arrays.getValue("developability"))
        assertTrue(brightE > darkE)
        assertEquals(1f, dark.arrays.getValue("halide").values[0], 1e-6f)
        assertEquals(0f, dark.arrays.getValue("metallic_silver").values[0], 1e-6f)
        assertEquals(DataKernel.LATENT_KIND, dark.metadata["kind"])
        assertEquals(false, dark.metadata["consumed"])
    }

    @Test
    fun colorDevelopBleachesSilverAndFormsDye() {
        val latent = kernel.expose(flat(0.70f), recipe)
        val beforeHalide = mean(latent.arrays.getValue("halide"))
        val optical = kernel.develop(latent, recipe)
        val model = nested(optical.metadata, "film_process_model", "execution_implementation")
        assertEquals(DataKernel.PHONE_REDUCED_PROFILE_ID, model["profile_id"])
        assertTrue(optical.opticalDensityRgb.values.all { it >= 0f && it.isFinite() })
        assertEquals("negative", optical.metadata["image_polarity"])
        val coupler = latent.arrays.getValue("coupler")
        val dye = latent.arrays.getValue("dye")
        assertEquals(beforeHalide, 1f, 1e-5f)
        assertEquals(1f, coupler.values[0], 1e-6f)
        assertEquals(0f, dye.values[0], 1e-6f)
    }

    @Test
    fun observeDoesNotMutateOpticalMasterAndInvertsNegative() {
        val width = 2
        val height = 1
        val pixels = floatArrayOf(0.05f, 0.05f, 0.05f, 0.90f, 0.90f, 0.90f)
        val latent = kernel.expose(RgbVolume(width, height, 3, pixels), recipe)
        val optical = kernel.develop(latent, recipe)
        val before = optical.opticalDensityRgb.values.copyOf()
        val observed = kernel.observe(optical, recipe)
        assertArrayEquals(before, optical.opticalDensityRgb.values, 0f)
        val left = luma(observed.outputRgb, 0)
        val right = luma(observed.outputRgb, 1)
        assertTrue(right > left)
        assertTrue(observed.outputRgb.values.all { it in 0f..1f })
        assertEquals(DataKernel.PHONE_SCANNER_MODEL_ID, observed.metadata["observation_model"])
        assertEquals(DataKernel.PHONE_TRANSMISSION_CAPTURE_ID, observed.metadata["transmission_capture"])
        assertEquals("scanner", observed.metadata["observation_mode"])
    }

    @Test
    fun viewerKeepsNegativePolarityWhileScannerInverts() {
        val optical = twoToneOptical()
        val before = optical.opticalDensityRgb.values.copyOf()
        val viewer = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "transmission_light_ev" to 0.0,
                "transmission_light_temperature_k" to 5500.0,
            ),
        )
        val scanner = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "scanner",
                "scan_normalize" to false,
                "remove_base_mask" to true,
                "invert_transmission" to true,
            ),
        )
        assertTrue(luma(viewer.outputRgb, 0) > luma(viewer.outputRgb, 1))
        assertTrue(luma(scanner.outputRgb, 1) > luma(scanner.outputRgb, 0))
        assertEquals(DataKernel.PHONE_VIEWER_MODEL_ID, viewer.metadata["observation_model"])
        assertEquals(false, viewer.metadata["invert_transmission"])
        assertEquals(false, viewer.metadata["remove_base_mask"])
        assertEquals(DataKernel.PHONE_SCANNER_MODEL_ID, scanner.metadata["observation_model"])
        assertArrayEquals(before, optical.opticalDensityRgb.values, 0f)
    }

    @Test
    fun viewerEvBrightensAndWarmerKelvinIsRedder() {
        val optical = OpticalMaster(
            FloatNd(intArrayOf(1, 1, 3), floatArrayOf(1.0f, 1.0f, 1.0f)),
            emptyMap(),
            floatArrayOf(0.18f, 0.55f, 0.85f),
        )
        val dim = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "transmission_light_ev" to 0.0,
                "transmission_light_temperature_k" to 5500.0,
            ),
        )
        val bright = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "transmission_light_ev" to 1.5,
                "transmission_light_temperature_k" to 5500.0,
            ),
        )
        assertTrue(luma(bright.outputRgb, 0) > luma(dim.outputRgb, 0))
        val warm = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "transmission_light_ev" to 0.0,
                "transmission_light_temperature_k" to 3200.0,
            ),
        )
        val cool = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "transmission_light_ev" to 0.0,
                "transmission_light_temperature_k" to 8000.0,
            ),
        )
        val warmRB = warm.outputRgb.values[0] / warm.outputRgb.values[2].coerceAtLeast(1e-6f)
        val coolRB = cool.outputRgb.values[0] / cool.outputRgb.values[2].coerceAtLeast(1e-6f)
        assertTrue(warmRB > coolRB)
    }

    @Test
    fun sceneCameraWithoutRgbFailsClosed() {
        val optical = twoToneOptical()
        assertThrows(KernelException::class.java) {
            kernel.observe(
                optical,
                mapOf(
                    "observe_mode" to "viewer_light_table",
                    "viewer_illuminant" to "scene_camera",
                ),
            )
        }
        val lit = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "viewer_illuminant" to "scene_camera",
                "scene_illuminant_rgb" to listOf(1.0, 0.15, 0.08),
            ),
        )
        assertTrue(lit.outputRgb.values[0] > lit.outputRgb.values[2])
        assertEquals(DataKernel.PHONE_VIEWER_MODEL_ID, lit.metadata["observation_model"])
    }

    @Test
    fun sceneCameraSpatialImageLightsEachPixel() {
        val optical = OpticalMaster(
            FloatNd(intArrayOf(1, 2, 3), floatArrayOf(0.4f, 0.4f, 0.4f, 0.4f, 0.4f, 0.4f)),
            emptyMap(),
            floatArrayOf(0.18f, 0.55f, 0.85f),
        )
        val image = FloatNd(
            intArrayOf(1, 2, 3),
            floatArrayOf(1.0f, 0.05f, 0.05f, 0.05f, 0.05f, 1.0f),
        )
        val observed = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "viewer_light_table",
                "viewer_illuminant" to "scene_camera",
                "scene_illuminant_rgb" to listOf(0.5, 0.05, 0.5),
                "scene_illuminant_rgb_image" to image,
            ),
        )
        assertTrue(observed.outputRgb.values[0] > observed.outputRgb.values[2])
        assertTrue(observed.outputRgb.values[5] > observed.outputRgb.values[3])
        assertEquals("spatial_linear_rgb_v0", observed.metadata["viewer_illuminant_reduction"])
        assertThrows(KernelException::class.java) {
            kernel.observe(
                optical,
                mapOf(
                    "observe_mode" to "viewer_light_table",
                    "viewer_illuminant" to "scene_camera",
                    "scene_illuminant_rgb" to listOf(0.5, 0.05, 0.5),
                    "scene_illuminant_rgb_image" to FloatNd(intArrayOf(1, 1, 3), floatArrayOf(1f, 1f, 1f)),
                ),
            )
        }
    }

    @Test
    fun scannerNormalizeAndCurveDoNotMutateOptical() {
        val optical = twoToneOptical()
        val before = optical.opticalDensityRgb.values.copyOf()
        val curve = listOf(
            listOf(listOf(0.0, 0.25), listOf(1.0, 1.0)),
            listOf(listOf(0.0, 0.0), listOf(1.0, 1.0)),
            listOf(listOf(0.0, 0.0), listOf(1.0, 1.0)),
        )
        val observed = kernel.observe(
            optical,
            mapOf(
                "observe_mode" to "scanner",
                "scan_normalize" to true,
                "scan_normalize_mode" to "luma",
                "color_curve_rgb" to curve,
            ),
        )
        assertArrayEquals(before, optical.opticalDensityRgb.values, 0f)
        assertTrue(observed.outputRgb.values.all { it in 0f..1f })
        val scannerColor = observed.metadata["scanner_color_response"] as Map<*, *>
        assertEquals("color_curve_rgb", scannerColor["curve"])
        assertEquals("omitted_v0", scannerColor["device"])
    }

    @Test
    fun bwPathKeepsSilverAndSkipsDye() {
        val bw = recipe + ("enable_subtractive" to false)
        val latent = kernel.expose(flat(0.6f), bw)
        assertEquals(1, latent.arrays.getValue("halide").shape[2])
        assertTrue("dye" !in latent.arrays)
        val optical = kernel.develop(latent, bw)
        assertTrue(optical.opticalDensityRgb.values.all { it >= 0f })
        assertEquals("bw_silver", optical.metadata["color_system"])
    }

    @Test
    fun latentAndOpticalRoundTripThroughNpz() {
        val archive = NpzMediumArchive()
        val latent = kernel.expose(flat(0.45f), recipe)
        val latentBytes = archive.writeLatent(
            com.filmfoundry.interchange.LatentArchive(latent.arrays, latent.metadata),
        )
        val loadedLatent = archive.readLatent(latentBytes)
        assertEquals(DataKernel.LATENT_KIND, loadedLatent.metadata["kind"])
        val optical = kernel.develop(
            LatentState(loadedLatent.arrays, loadedLatent.metadata),
            recipe,
        )
        val opticalBytes = archive.writeDevelopedOptical(
            com.filmfoundry.interchange.DevelopedOpticalArchive(
                optical.opticalDensityRgb,
                optical.metadata,
                optical.clearBaseOpticalDensityRgb,
            ),
        )
        val loadedOptical = archive.readDevelopedOptical(opticalBytes)
        assertEquals(DataKernel.PHONE_REDUCED_PROFILE_ID, nested(loadedOptical.metadata, "film_process_model", "execution_implementation")["profile_id"])
        assertTrue("density_cmy" !in com.filmfoundry.interchange.NpzFile.read(opticalBytes).keys)
    }

    @Test
    fun developRejectsEmptyLatent() {
        assertThrows(KernelException::class.java) {
            kernel.develop(LatentState(emptyMap(), emptyMap()), recipe)
        }
    }

    @Test
    fun consumeLatentMatchesCopyAndMutatesSource() {
        val keep = kernel.expose(flat(0.55f), recipe)
        val consume = kernel.expose(flat(0.55f), recipe)
        val copied = kernel.develop(keep, recipe, consumeLatent = false)
        val consumed = kernel.develop(consume, recipe, consumeLatent = true)
        assertArrayEquals(copied.opticalDensityRgb.values, consumed.opticalDensityRgb.values, 1e-6f)
        assertEquals(1f, keep.arrays.getValue("halide").values[0], 1e-6f)
        assertTrue(consume.arrays.getValue("halide").values[0] < 1f)
        assertTrue(consume.arrays.getValue("dye")!!.values[0] > 0f)
    }

    @Test
    fun crossChemistryIsExplicitAndNativeDevelopStaysFirstOrder() {
        val native = kernel.develop(kernel.expose(flat(0.7f), recipe), recipe, consumeLatent = false)
        val crossRecipe = recipe + mapOf("develop" to mapOf("time_min" to 8.0, "chemistry" to "cross"))
        val crossed = kernel.develop(kernel.expose(flat(0.7f), recipe), crossRecipe, consumeLatent = false)
        assertEquals("negative", native.metadata["image_polarity"])
        assertEquals("positive", crossed.metadata["image_polarity"])
        assertEquals("cross_positive", crossed.metadata["medium_process"])
        assertEquals("phone_first_order_develop_v0", nested(native.metadata, "film_process_model", "execution_implementation", "component_models")["develop"])
        assertEquals(DataKernel.PHONE_CROSS_CHEMISTRY_ID, nested(crossed.metadata, "film_process_model", "execution_implementation", "component_models")["develop"])
        assertFalse(native.opticalDensityRgb.values.contentEquals(crossed.opticalDensityRgb.values))
        val bw = recipe + mapOf("enable_subtractive" to false, "develop" to mapOf("time_min" to 8.0, "chemistry" to "cross"))
        val bwCross = kernel.develop(kernel.expose(flat(0.6f), bw), bw)
        assertEquals("cross_bw_dye", bwCross.metadata["color_system"])
        assertThrows(KernelException::class.java) {
            kernel.develop(kernel.expose(flat(0.2f), recipe), recipe + mapOf("develop" to mapOf("chemistry" to "other")))
        }
    }

    @Test
    fun projectionInvertsNegativesAndScanGridDoesNotRewriteTheMaster() {
        val optical = twoToneOptical()
        val before = optical.opticalDensityRgb.values.copyOf()
        val light = kernel.observe(optical, mapOf("observe_mode" to "viewer_light_table"))
        val projected = kernel.observe(optical, mapOf("observe_mode" to "viewer_projection"))
        assertTrue(luma(light.outputRgb, 0) > luma(light.outputRgb, 1))
        assertTrue(luma(projected.outputRgb, 1) > luma(projected.outputRgb, 0))
        assertEquals(DataKernel.PHONE_PROJECTION_MODEL_ID, projected.metadata["observation_model"])
        assertEquals(true, projected.metadata["invert_transmission"])
        val positive = OpticalMaster(optical.opticalDensityRgb, mapOf("image_polarity" to "positive"), optical.clearBaseOpticalDensityRgb)
        val projectedPositive = kernel.observe(positive, mapOf("observe_mode" to "viewer_projection"))
        assertEquals(false, projectedPositive.metadata["invert_transmission"])
        assertArrayEquals(before, optical.opticalDensityRgb.values, 0f)
        val grid = OpticalMaster(FloatNd(intArrayOf(4, 4, 3), FloatArray(48) { 0.4f }), mapOf("image_polarity" to "negative"))
        val scanned = kernel.observe(grid, mapOf("observe_mode" to "scanner", "scanner" to mapOf("scan_grid_long_edge" to 2, "scan_normalize" to false)))
        assertArrayEquals(intArrayOf(2, 2, 3), scanned.outputRgb.shape)
        assertEquals("box_average_v1", (scanned.metadata["observation_grid"] as Map<*, *>)["sampler"])
        assertArrayEquals(intArrayOf(4, 4, 3), grid.opticalDensityRgb.shape)
    }

    private fun twoToneOptical(): OpticalMaster {
        return OpticalMaster(
            FloatNd(
                intArrayOf(1, 2, 3),
                floatArrayOf(0.12f, 0.12f, 0.12f, 1.35f, 1.35f, 1.35f),
            ),
            mapOf("image_polarity" to "negative"),
            floatArrayOf(0.18f, 0.55f, 0.85f),
        )
    }

    private fun flat(value: Float): RgbVolume {
        return RgbVolume(2, 2, 3, FloatArray(2 * 2 * 3) { value })
    }

    private fun mean(array: FloatNd): Float {
        return array.values.average().toFloat()
    }

    private fun luma(rgb: FloatNd, pixel: Int): Float {
        val i = pixel * 3
        return 0.2126f * rgb.values[i] + 0.7152f * rgb.values[i + 1] + 0.0722f * rgb.values[i + 2]
    }

    @Suppress("UNCHECKED_CAST")
    private fun nested(root: Map<String, Any?>, vararg keys: String): Map<String, Any?> {
        var current: Any? = root
        for (key in keys) {
            current = (current as Map<*, *>)[key]
        }
        return current as Map<String, Any?>
    }
}
