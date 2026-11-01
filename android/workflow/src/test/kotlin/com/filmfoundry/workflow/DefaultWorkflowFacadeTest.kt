package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import com.filmfoundry.kernel.OpticalMaster
import com.filmfoundry.kernel.PhoneReducedKernel
import com.filmfoundry.kernel.RgbVolume
import kotlinx.coroutines.runBlocking
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class DefaultWorkflowFacadeTest {
    private val facade = DefaultWorkflowFacade(PhoneReducedKernel())

    @Test
    fun inMemoryExposeDevelopScanKeepsTopology() = runBlocking {
        val recipe = mapOf(
            "enable_subtractive" to true,
            "develop" to mapOf("time_min" to 8.0),
        )
        val exposed = facade.run(
            WorkflowRequest(
                command = Command.EXPOSE,
                input = MediumRef("in"),
                output = MediumRef("frame.darkroom_latent.npz"),
                recipe = recipe,
                linearRgb = RgbVolume(2, 2, 3, FloatArray(12) { 0.4f }),
                frameIdentity = FrameIdentity("pocket-roll", frameIndex = 1),
            ),
        )
        val latent = exposed.latent!!
        assertEquals(DataKernel.LATENT_KIND, latent.metadata["kind"])
        val developed = facade.run(
            WorkflowRequest(
                command = Command.DEVELOP_LATENT,
                input = MediumRef("frame.darkroom_latent.npz"),
                output = MediumRef("frame.darkroom_negative.npz"),
                recipe = recipe,
                latent = latent,
            ),
        )
        val optical = developed.optical!!
        val impl = (optical.metadata["film_process_model"] as Map<*, *>)["execution_implementation"] as Map<*, *>
        assertEquals(DataKernel.PHONE_REDUCED_PROFILE_ID, impl["profile_id"])
        val scanned = facade.run(
            WorkflowRequest(
                command = Command.SCAN,
                input = MediumRef("frame.darkroom_negative.npz"),
                output = MediumRef("frame.png"),
                recipe = recipe,
                optical = optical,
            ),
        )
        assertNotNull(scanned.observation)
        assertTrue(scanned.observation!!.outputRgb.values.all { it in 0f..1f })
        assertEquals(DataKernel.PHONE_SCANNER_MODEL_ID, scanned.observation!!.metadata["observation_model"])
        val before = optical.opticalDensityRgb.values.copyOf()
        facade.run(
            WorkflowRequest(
                command = Command.SCAN,
                input = MediumRef("frame.darkroom_negative.npz"),
                output = MediumRef("frame.png"),
                recipe = recipe,
                optical = optical,
            ),
        )
        assertTrue(before.contentEquals(optical.opticalDensityRgb.values))
    }

    @Test
    fun exposeStampsCaptureAndDevelopCopiesIt() = runBlocking {
        val capture = mapOf(
            "kind" to "display_referred_srgb",
            "isp_processed" to true,
            "working_space" to DataKernel.CAPTURE_WORKING_SPACE,
            "scene_referred_claim" to false,
            "hdr_gainmap" to "absent",
            "frame_count" to 1,
        )
        val exposed = facade.run(
            WorkflowRequest(
                command = Command.EXPOSE,
                input = MediumRef("in"),
                output = MediumRef("frame.darkroom_latent.npz"),
                recipe = mapOf("enable_subtractive" to true),
                linearRgb = RgbVolume(2, 2, 3, FloatArray(12) { 0.4f }),
                captureRecord = capture,
            ),
        )
        val latentCapture = exposed.latent!!.metadata[DataKernel.CAPTURE_OBJECT_KEY] as Map<*, *>
        assertEquals("display_referred_srgb", latentCapture["kind"])
        val developed = facade.run(
            WorkflowRequest(
                command = Command.DEVELOP_LATENT,
                input = MediumRef("frame.darkroom_latent.npz"),
                output = MediumRef("frame.darkroom_negative.npz"),
                recipe = mapOf("enable_subtractive" to true),
                latent = exposed.latent,
            ),
        )
        val developedCapture = developed.optical!!.metadata[DataKernel.CAPTURE_OBJECT_KEY] as Map<*, *>
        assertEquals(false, developedCapture["scene_referred_claim"])
    }

    @Test
    fun exposeWithoutPixelsIsInvalid() {
        assertThrows(WorkflowException::class.java) {
            runBlocking {
                DefaultWorkflowFacade().run(
                    WorkflowRequest(
                        command = Command.EXPOSE,
                        input = MediumRef("in"),
                        output = MediumRef("out"),
                        recipe = emptyMap(),
                    ),
                )
            }
        }
    }

    @Test
    fun scanDefaultsToScannerAndHonorsViewerRecipe() = runBlocking {
        val optical = OpticalMaster(
            FloatNd(
                intArrayOf(1, 2, 3),
                floatArrayOf(0.12f, 0.12f, 0.12f, 1.35f, 1.35f, 1.35f),
            ),
            emptyMap(),
            floatArrayOf(0.18f, 0.55f, 0.85f),
        )
        val scanned = facade.run(
            WorkflowRequest(
                command = Command.SCAN,
                input = MediumRef("in"),
                output = MediumRef("frame.png"),
                recipe = emptyMap(),
                optical = optical,
            ),
        )
        val viewer = facade.run(
            WorkflowRequest(
                command = Command.SCAN,
                input = MediumRef("in"),
                output = MediumRef("frame.png"),
                recipe = mapOf("observe_mode" to "viewer_light_table"),
                optical = optical,
            ),
        )
        assertEquals(DataKernel.PHONE_SCANNER_MODEL_ID, scanned.observation!!.metadata["observation_model"])
        assertEquals(DataKernel.PHONE_VIEWER_MODEL_ID, viewer.observation!!.metadata["observation_model"])
        assertTrue(luma(viewer.observation!!.outputRgb, 0) > luma(viewer.observation!!.outputRgb, 1))
        assertTrue(luma(scanned.observation!!.outputRgb, 1) > luma(scanned.observation!!.outputRgb, 0))
        val before = optical.opticalDensityRgb.values.copyOf()
        assertTrue(before.contentEquals(optical.opticalDensityRgb.values))
    }

    private fun luma(rgb: FloatNd, pixel: Int): Float {
        val i = pixel * 3
        return 0.2126f * rgb.values[i] + 0.7152f * rgb.values[i + 1] + 0.0722f * rgb.values[i + 2]
    }
}
