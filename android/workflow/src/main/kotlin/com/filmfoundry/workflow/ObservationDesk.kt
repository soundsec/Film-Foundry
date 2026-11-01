package com.filmfoundry.workflow

import com.filmfoundry.interchange.DataKernel
import com.filmfoundry.interchange.FloatNd

enum class ObserveMode(val wireName: String) {
    VIEWER_LIGHT_TABLE("viewer_light_table"),
    VIEWER_PROJECTION("viewer_projection"),
    SCANNER("scanner"),
}

enum class ViewerIlluminant(val wireName: String) {
    SYNTHETIC_LIGHT_TABLE("synthetic_light_table"),
    SCENE_CAMERA("scene_camera"),
}

data class SceneIlluminant(
    val rgb: List<Double>,
    val image: FloatNd? = null,
    val sourceKind: String = DataKernel.PHONE_VIEWER_PREVIEW_KIND,
    val reduction: String = if (image == null) {
        DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION[0]
    } else {
        DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION[1]
    },
) {
    init {
        if (rgb.size != 3 || rgb.any { it < 0.0 }) {
            throw WorkflowException("invalid_request", "scene_illuminant_rgb must be 3 non-negative values")
        }
        if (sourceKind != DataKernel.PHONE_VIEWER_PREVIEW_KIND) {
            throw WorkflowException("invalid_request", "viewer preview kind is not a capture kind")
        }
        if (reduction !in DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION) {
            throw WorkflowException("invalid_request", "unknown viewer illuminant reduction")
        }
        val field = image
        if (field != null && (field.shape.size != 3 || field.shape[2] != 3)) {
            throw WorkflowException("invalid_request", "scene_illuminant_rgb_image must be HxWx3")
        }
    }
}

data class LightTableSettings(
    val transmissionLightEv: Float = 0f,
    val transmissionLightTemperatureK: Float = 5500f,
) {
    fun toRecipe(): Map<String, Any?> {
        return mapOf(
            "transmission_light_ev" to transmissionLightEv.toDouble(),
            "transmission_light_temperature_k" to transmissionLightTemperatureK.toDouble(),
        )
    }
}

data class ScannerInterpretation(
    val removeBaseMask: Boolean = true,
    val invertTransmission: Boolean = true,
    val scanNormalize: Boolean = true,
    val scanNormalizeStrength: Float = 0.45f,
    val scanNormalizeMode: String = "luma",
    val scanBlackPercentile: Float = 0.3f,
    val scanWhitePercentile: Float = 99.7f,
) {
    init {
        if (scanNormalizeMode !in DataKernel.PHONE_SCAN_NORMALIZE_MODES) {
            throw WorkflowException("invalid_request", "scan_normalize_mode must be luma or rgb")
        }
        if (scanBlackPercentile < 0f || scanWhitePercentile > 100f ||
            scanWhitePercentile <= scanBlackPercentile
        ) {
            throw WorkflowException("invalid_request", "black/white percentiles must be ordered in 0..100")
        }
    }

    fun toRecipe(): Map<String, Any?> {
        return mapOf(
            "remove_base_mask" to removeBaseMask,
            "invert_transmission" to invertTransmission,
            "scan_normalize" to scanNormalize,
            "scan_normalize_strength" to scanNormalizeStrength.toDouble(),
            "scan_normalize_mode" to scanNormalizeMode,
            "scan_black_percentile" to scanBlackPercentile.toDouble(),
            "scan_white_percentile" to scanWhitePercentile.toDouble(),
        )
    }
}

data class ViewerSession(
    val resultId: String,
    val opticalPath: String,
    var illuminant: ViewerIlluminant,
    var lightTable: LightTableSettings,
    var sceneIlluminant: SceneIlluminant? = null,
)

data class GalleryItem(
    val displayName: String,
    val mimeType: String,
    val kind: String = DataKernel.PHONE_GALLERY_ITEM_KIND,
    val interpretation: Map<String, Any?> = emptyMap(),
)

/**
 * Observe a developed transparent master. Light table, camera backlight and
 * scan stay in the app. Only an explicit projection save may enter the gallery.
 */
class ObservationDesk {
    private val viewers = linkedMapOf<String, ViewerSession>()
    private val gallery = linkedMapOf<String, GalleryItem>()

    fun openViewer(
        result: DevelopedResult,
        illuminant: ViewerIlluminant = ViewerIlluminant.SYNTHETIC_LIGHT_TABLE,
        lightTable: LightTableSettings = LightTableSettings(),
    ): ViewerSession {
        val session = ViewerSession(
            resultId = result.resultId,
            opticalPath = result.opticalPath,
            illuminant = illuminant,
            lightTable = lightTable,
        )
        viewers[result.resultId] = session
        return session
    }

    fun switchViewerIlluminant(resultId: String, illuminant: ViewerIlluminant): ViewerSession {
        val session = viewers[resultId]
            ?: throw WorkflowException("invalid_request", "no viewer session for $resultId")
        session.illuminant = illuminant
        return session
    }

    fun illuminateWithScene(resultId: String, illuminant: SceneIlluminant): ViewerSession {
        val session = viewers[resultId]
            ?: throw WorkflowException("invalid_request", "no viewer session for $resultId")
        session.illuminant = ViewerIlluminant.SCENE_CAMERA
        session.sceneIlluminant = illuminant
        return session
    }

    fun publishViewerToGallery(resultId: String): Nothing {
        throw WorkflowException(
            "invalid_request",
            "viewer light table and scene-camera views do not enter the system gallery",
        )
    }

    @Suppress("UNUSED_PARAMETER")
    fun commitScannerObservation(
        result: DevelopedResult,
        displayName: String,
        mimeType: String,
        interpretation: ScannerInterpretation = ScannerInterpretation(),
    ): Nothing {
        throw WorkflowException("invalid_request", "scans stay in the app library")
    }

    fun commitProjectionObservation(
        result: DevelopedResult,
        displayName: String,
        mimeType: String,
    ): GalleryItem {
        GalleryExportPolicy.requireScannerCommit(displayName, mimeType)
        val item = GalleryItem(
            displayName = displayName,
            mimeType = mimeType,
            interpretation = mapOf("observe_mode" to ObserveMode.VIEWER_PROJECTION.wireName),
        )
        gallery[result.resultId + "/" + displayName] = item
        return item
    }

    fun galleryItems(): List<GalleryItem> = gallery.values.toList()

    companion object {
        fun viewerObserveRecipe(
            session: ViewerSession,
            sceneIlluminantRgb: List<Double>? = null,
        ): Map<String, Any?> {
            val payload = linkedMapOf<String, Any?>(
                "observe_mode" to ObserveMode.VIEWER_LIGHT_TABLE.wireName,
                "viewer_illuminant" to session.illuminant.wireName,
            )
            payload.putAll(session.lightTable.toRecipe())
            if (session.illuminant == ViewerIlluminant.SCENE_CAMERA) {
                val rgb = sceneIlluminantRgb ?: session.sceneIlluminant?.rgb
                if (rgb == null || rgb.size != 3) {
                    throw WorkflowException(
                        "invalid_request",
                        "scene_camera illuminant requires scene_illuminant_rgb",
                    )
                }
                payload[DataKernel.PHONE_SCENE_ILLUMINANT_RGB_KEY] = rgb
                session.sceneIlluminant?.image?.let { image ->
                    payload[DataKernel.PHONE_SCENE_ILLUMINANT_IMAGE_KEY] = image
                }
                payload["viewer_illuminant_reduction"] = session.sceneIlluminant?.reduction
                    ?: DataKernel.PHONE_VIEWER_ILLUMINANT_REDUCTION[0]
            }
            return payload
        }

        fun scannerObserveRecipe(
            lightTable: LightTableSettings = LightTableSettings(),
            interpretation: ScannerInterpretation = ScannerInterpretation(),
            colorCurveRgb: List<List<List<Double>>>? = null,
        ): Map<String, Any?> {
            val payload = linkedMapOf<String, Any?>(
                "observe_mode" to ObserveMode.SCANNER.wireName,
            )
            payload.putAll(lightTable.toRecipe())
            payload.putAll(interpretation.toRecipe())
            if (colorCurveRgb != null) {
                payload["color_curve_rgb"] = colorCurveRgb
            }
            return payload
        }
    }
}

object GalleryExportPolicy {
    fun requireScannerCommit(displayName: String, mimeType: String) {
        val lower = displayName.lowercase()
        if (
            lower.endsWith(DataKernel.LATENT_SUFFIX) ||
            lower.endsWith(DataKernel.NEGATIVE_SUFFIX) ||
            lower.endsWith(DataKernel.POSITIVE_SUFFIX)
        ) {
            throw WorkflowException("invalid_request", "NPZ archives do not enter the system gallery")
        }
        if (
            displayName.equals(DataKernel.PHONE_LIBRARY_INDEX_FILENAME, ignoreCase = true) ||
            lower.endsWith(".json")
        ) {
            throw WorkflowException("invalid_request", "library index and JSON catalogs do not enter the system gallery")
        }
        if (
            displayName.startsWith(DataKernel.CONFIG_SHARE_PREFIX) ||
            lower.endsWith(".ffc2")
        ) {
            throw WorkflowException("invalid_request", "recipe share codes do not enter the system gallery")
        }
        if (mimeType !in DataKernel.PHONE_GALLERY_IMAGE_MIMES) {
            throw WorkflowException("invalid_request", "system gallery only accepts PNG/JPEG/WebP observations")
        }
    }
}
