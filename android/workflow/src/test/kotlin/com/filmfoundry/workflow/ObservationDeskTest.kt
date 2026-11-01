package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class ObservationDeskTest {
    private val desk = ObservationDesk()
    private val result = DevelopedResult(
        resultId = "r1",
        sourceRollId = "roll",
        sourceFrameIndex = 1,
        opticalPath = "frame.darkroom_negative.npz",
        developRecipe = emptyMap(),
    )

    @Test
    fun viewerUsesSharedLightTableAndCannotEnterGallery() {
        val session = desk.openViewer(
            result,
            illuminant = ViewerIlluminant.SYNTHETIC_LIGHT_TABLE,
            lightTable = LightTableSettings(transmissionLightEv = 1.5f, transmissionLightTemperatureK = 4000f),
        )
        assertEquals("synthetic_light_table", session.illuminant.wireName)
        assertEquals(
            setOf("transmission_light_ev", "transmission_light_temperature_k"),
            session.lightTable.toRecipe().keys,
        )
        desk.switchViewerIlluminant("r1", ViewerIlluminant.SCENE_CAMERA)
        val error = assertThrows(WorkflowException::class.java) {
            desk.publishViewerToGallery("r1")
        }
        assertTrue(error.message!!.contains("system gallery"))
        assertEquals("viewer_light_table", ObserveMode.VIEWER_LIGHT_TABLE.wireName)
    }

    @Test
    fun scannerCommitEntersGalleryAndNpzDoesNot() {
        val interpretation = ScannerInterpretation(
            removeBaseMask = true,
            invertTransmission = true,
            scanNormalize = true,
            scanNormalizeMode = "luma",
            scanBlackPercentile = 0.3f,
            scanWhitePercentile = 99.7f,
        )
        assertTrue(interpretation.toRecipe().keys.containsAll(listOf(
            "remove_base_mask",
            "invert_transmission",
            "scan_black_percentile",
            "scan_white_percentile",
            "scan_normalize",
        )))
        assertThrows(WorkflowException::class.java) {
            desk.commitScannerObservation(result, displayName = "scan.png", mimeType = "image/png", interpretation = interpretation)
        }
        val item = desk.commitProjectionObservation(result, displayName = "frame.png", mimeType = "image/png")
        assertEquals(DataKernel.PHONE_GALLERY_ITEM_KIND, item.kind)
        assertEquals("viewer_projection", item.interpretation["observe_mode"])
        assertEquals(1, desk.galleryItems().size)
        assertThrows(WorkflowException::class.java) {
            desk.commitProjectionObservation(result, displayName = "frame.darkroom_negative.npz", mimeType = "image/png")
        }
        assertThrows(WorkflowException::class.java) {
            desk.commitProjectionObservation(result, displayName = "frame.png", mimeType = "application/zip")
        }
    }

    @Test
    fun catalogMatchesDesktopScannerFieldNames() {
        assertEquals(
            listOf("viewer_light_table", "viewer_projection", "scanner"),
            DataKernel.PHONE_OBSERVE_MODES,
        )
        assertEquals("projection_observation", DataKernel.PHONE_GALLERY_ITEM_KIND)
        assertTrue(DataKernel.PHONE_SCANNER_INTERPRETATION_FIELDS.contains("remove_base_mask"))
        assertTrue(DataKernel.PHONE_SCANNER_INTERPRETATION_FIELDS.contains("invert_transmission"))
        assertTrue(DataKernel.PHONE_SCANNER_INTERPRETATION_FIELDS.contains("color_curve_rgb"))
        assertEquals(listOf("luma", "rgb"), DataKernel.PHONE_SCAN_NORMALIZE_MODES)
        assertEquals("phone_transmission_capture_v0", DataKernel.PHONE_TRANSMISSION_CAPTURE_ID)
        assertEquals("phone_light_table_viewer_v0", DataKernel.PHONE_VIEWER_MODEL_ID)
        assertEquals("phone_projection_viewer_v0", DataKernel.PHONE_PROJECTION_MODEL_ID)
        assertEquals("phone_reduced_scanner_v0", DataKernel.PHONE_SCANNER_MODEL_ID)
        assertEquals("viewer_preview_illuminant_v0", DataKernel.PHONE_VIEWER_PREVIEW_KIND)
        assertTrue(DataKernel.PHONE_VIEWER_PREVIEW_KIND !in DataKernel.CAPTURE_KIND_VALUES)
        assertEquals("mediastore_images_external_v0", DataKernel.PHONE_MEDIASTORE_COLLECTION)
        assertEquals("Pictures/FilmFoundry/", DataKernel.PHONE_MEDIASTORE_RELATIVE_PATH)
        assertEquals(listOf("fill_roll", "illuminate_viewer"), DataKernel.PHONE_CAMERA_SESSION_ROLES)
        assertEquals(listOf("image_capture"), DataKernel.PHONE_CAMERAX_STILL_USE_CASES)
        assertEquals(listOf("preview", "image_analysis"), DataKernel.PHONE_CAMERAX_PREVIEW_USE_CASES)
    }

    @Test
    fun observeRecipesShareLightTableAndSplitMode() {
        val session = desk.openViewer(
            result,
            illuminant = ViewerIlluminant.SYNTHETIC_LIGHT_TABLE,
            lightTable = LightTableSettings(transmissionLightEv = 0.5f, transmissionLightTemperatureK = 4000f),
        )
        val viewer = ObservationDesk.viewerObserveRecipe(session)
        assertEquals("viewer_light_table", viewer["observe_mode"])
        assertEquals("synthetic_light_table", viewer["viewer_illuminant"])
        assertEquals(0.5, viewer["transmission_light_ev"])
        assertThrows(WorkflowException::class.java) {
            ObservationDesk.viewerObserveRecipe(
                session.copy(illuminant = ViewerIlluminant.SCENE_CAMERA),
            )
        }
        val scanner = ObservationDesk.scannerObserveRecipe(
            lightTable = session.lightTable,
            interpretation = ScannerInterpretation(scanNormalize = false),
        )
        assertEquals("scanner", scanner["observe_mode"])
        assertEquals(false, scanner["scan_normalize"])
        assertEquals(0.5, scanner["transmission_light_ev"])
        assertEquals(4000.0, scanner["transmission_light_temperature_k"])
    }

    @Test
    fun sceneIlluminantRecipeDoesNotEnterGallery() {
        val session = desk.openViewer(result)
        val illuminant = SceneIlluminant(
            rgb = listOf(0.8, 0.4, 0.2),
            image = FloatNd(intArrayOf(1, 2, 3), floatArrayOf(0.9f, 0.1f, 0.1f, 0.1f, 0.1f, 0.8f)),
        )
        desk.illuminateWithScene("r1", illuminant)
        val recipe = ObservationDesk.viewerObserveRecipe(session)
        assertEquals("scene_camera", recipe["viewer_illuminant"])
        assertEquals(listOf(0.8, 0.4, 0.2), recipe[DataKernel.PHONE_SCENE_ILLUMINANT_RGB_KEY])
        assertEquals(illuminant.image, recipe[DataKernel.PHONE_SCENE_ILLUMINANT_IMAGE_KEY])
        assertEquals("spatial_linear_rgb_v0", recipe["viewer_illuminant_reduction"])
        assertEquals(DataKernel.PHONE_VIEWER_PREVIEW_KIND, illuminant.sourceKind)
        assertThrows(WorkflowException::class.java) {
            desk.publishViewerToGallery("r1")
        }
    }
}
