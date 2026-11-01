package com.filmfoundry.interchange

object DataKernel {
    const val NEGATIVE_SUFFIX = ".darkroom_negative.npz"
    const val POSITIVE_SUFFIX = ".darkroom_positive.npz"
    const val LATENT_SUFFIX = ".darkroom_latent.npz"

    const val LATENT_KIND = "exposed_undeveloped_latent_v1"
    const val LATENT_STAGE = "exposed_latent"
    const val LATENT_KIND_KEY = "kind"
    const val LATENT_STAGE_KEY = "stage"
    const val MATERIAL_SNAPSHOT_KEY = "material_snapshot"
    const val MATERIAL_SNAPSHOT_MODEL = "film_material_parameters_v1"

    const val CONFIG_SHARE_KIND = "film_foundry_config_share"
    const val CONFIG_SHARE_VERSION = 2
    const val CONFIG_SHARE_PREFIX = "FFC2:"
    const val MATERIAL_GEOMETRY_KEY = "material_geometry"
    const val MATERIAL_GEOMETRY_MODEL_ID = "reference_frame_physical_v1"
    const val SPECTRAL_EXPOSURE_PROFILE_KEY = "spectral_exposure_profile"
    const val SPECTRAL_EXPOSURE_MODEL_ID = "rgb_basis_spectral_exposure_v1"
    const val SMITS_EXPOSURE_MODEL_ID = "smits7_spectral_exposure_v1"
    val SMITS_BASIS_ORDER = listOf("white", "cyan", "magenta", "yellow", "red", "green", "blue")
    const val SPECTRAL_MATERIAL_PROFILE_KEY = SpectralOptics.MATERIAL_KEY
    const val SPECTRAL_MEDIUM_KEY = SpectralOptics.MEDIUM_KEY
    const val SPECTRAL_COMPONENT_ARRAY_KEY = SpectralOptics.AMOUNTS_KEY
    const val SPECTRAL_POST_PROCESS_ARRAY_KEY = SpectralOptics.POST_PROCESS_KEY
    const val SPECTRAL_OBSERVATION_PROFILE_KEY = SpectralOptics.OBSERVER_KEY
    val SPECTRAL_EXPOSURE_PROFILE_FIELDS = listOf(
        "model", "profile_id", "working_space", "wavelengths_nm",
        "rgb_basis", "layer_sensitivity", "layer_gain",
    )

    val POLARITY_VALUES = listOf("negative", "positive")
    val MEDIUM_IDENTITY_FIELDS = listOf(
        "medium_family",
        "medium_process",
        "image_polarity",
        "view_mode",
        "base_type",
        "color_system",
        "compatible_interpreters",
    )
    const val FRAME_IDENTITY_KEY = "frame_identity"
    val FRAME_IDENTITY_FIELDS = listOf("roll_id", "frame_id", "frame_index")
    // Recipe identity is retained in JSON/FFC2. The phone reduced kernel
    // implements declared phone grain / selected incidents independently; other spatial models remain omitted.
    const val INCIDENT_IDENTITY_RECIPE_FIELD = "develop.incident_identity_seed"
    const val INCIDENT_IDENTITY_MODEL = "roll_batch_incident_identity_v1"

    val CONFIG_SHARE_FIELDS = listOf(
        "film",
        "develop",
        "scanner",
        "look",
        "film_border",
        "mode",
        "medium",
        "enable_mtf",
        "enable_halation",
        "enable_grain",
        "enable_subtractive",
    )

    val LATENT_REQUIRED_ARRAYS = listOf("halide", "developability", "metallic_silver")
    val LATENT_OPTIONAL_ARRAYS = listOf(
        "coupler",
        "dye",
        "bleached_halide",
        "original_developability",
    )
    const val LATENT_METADATA_KEY = "latent_medium_metadata"
    const val OPTICAL_DENSITY_KEY = "optical_density_rgb"
    const val DEVELOPED_METADATA_KEY = "developed_medium_metadata"
    const val CLEAR_BASE_KEY = "clear_base_optical_density_rgb"
    const val PHONE_REDUCED_PROFILE_ID = "phone_reduced_v0"
    const val CAPTURE_OBJECT_KEY = "capture"
    const val CAPTURE_WORKING_SPACE = "srgb_linear_iec61966_2_1_v0"
    val CAPTURE_KIND_VALUES = listOf(
        "display_referred_srgb",
        "ultra_hdr_gainmap",
        "sensor_raw_dng",
        "linear_working_rgb",
        "exposure_stack",
    )
    val CAPTURE_HDR_GAINMAP_VALUES = listOf(
        "absent",
        "present_not_applied",
        "applied",
    )
    val CAPTURE_RECORD_FIELDS = listOf(
        "kind",
        "isp_processed",
        "working_space",
        "source_encoding",
        "bit_depth",
        "hdr_gainmap",
        "scene_referred_claim",
        "exposure_time_ns",
        "iso",
        "frame_count",
    )
    val PHONE_ROLL_CAPACITY_VALUES = listOf(8, 10, 12, 16, 24, 36, 48, 72)
    val PHONE_LIBRARY_KINDS = listOf("undeveloped_roll", "developed_result")
    val PHONE_ROLL_LIFECYCLE = listOf("loading", "shooting", "sealed", "discarded")
    const val PHONE_LIBRARY_INDEX_KIND = "phone_library_index_v0"
    const val PHONE_LIBRARY_INDEX_VERSION = 3
    const val PHONE_LIBRARY_INDEX_FILENAME = "phone_library_index.json"
    const val PHONE_RECIPE_CATALOG_KIND = "phone_recipe_catalog_v0"
    const val PHONE_RECIPE_CATALOG_VERSION = 1
    const val PHONE_RECIPE_CATALOG_FILENAME = "phone_recipe_catalog.json"
    val PHONE_RECIPE_CATALOG_ENTRY_KINDS = listOf("film", "scanner")
    val PHONE_LIBRARY_FRAME_STATES = listOf("empty", "latent", "failed", "capturing", "captured")
    val PHONE_OBSERVE_MODES = listOf("viewer_light_table", "viewer_projection", "scanner")
    val PHONE_VIEWER_ILLUMINANT = listOf("synthetic_light_table", "scene_camera")
    const val PHONE_SCENE_ILLUMINANT_RGB_KEY = "scene_illuminant_rgb"
    const val PHONE_SCENE_ILLUMINANT_IMAGE_KEY = "scene_illuminant_rgb_image"
    const val PHONE_VIEWER_PREVIEW_KIND = "viewer_preview_illuminant_v0"
    val PHONE_VIEWER_ILLUMINANT_REDUCTION = listOf("mean_linear_rgb_v0", "spatial_linear_rgb_v0")
    const val PHONE_GALLERY_ITEM_KIND = "projection_observation"
    val PHONE_LIGHT_TABLE_FIELDS = listOf(
        "transmission_light_ev",
        "transmission_light_temperature_k",
    )
    val PHONE_SCANNER_INTERPRETATION_FIELDS = listOf(
        "remove_base_mask",
        "invert_transmission",
        "color_curve_rgb",
        "tone_curve",
        "scan_normalize",
        "scan_normalize_strength",
        "scan_normalize_mode",
        "scan_black_percentile",
        "scan_white_percentile",
    )
    val PHONE_SCAN_NORMALIZE_MODES = listOf("luma", "rgb")
    val PHONE_GALLERY_IMAGE_MIMES = listOf("image/png", "image/jpeg", "image/webp")
    val PHONE_CAMERA_SESSION_ROLES = listOf("fill_roll", "illuminate_viewer")
    val PHONE_CAMERAX_STILL_USE_CASES = listOf("image_capture")
    val PHONE_CAMERAX_PREVIEW_USE_CASES = listOf("preview", "image_analysis")
    const val PHONE_MEDIASTORE_COLLECTION = "mediastore_images_external_v0"
    const val PHONE_MEDIASTORE_RELATIVE_PATH = "Pictures/FilmFoundry/"
    const val PHONE_TRANSMISSION_CAPTURE_ID = "phone_transmission_capture_v0"
    const val PHONE_VIEWER_MODEL_ID = "phone_light_table_viewer_v0"
    const val PHONE_PROJECTION_MODEL_ID = "phone_projection_viewer_v0"
    const val PHONE_SCANNER_MODEL_ID = "phone_reduced_scanner_v0"
    const val PHONE_CROSS_CHEMISTRY_ID = "phone_cross_chemistry_v0"

    val WORKFLOW_COMMANDS = listOf("expose", "develop_latent", "scan")
    val WORKFLOW_PROGRESS_STAGES = listOf(
        "starting",
        "loading",
        "exposing",
        "developing",
        "saving",
        "scanning",
        "completed",
    )
    val WORKFLOW_ERROR_CODES = listOf(
        "unsupported_command",
        "input_not_found",
        "io_permission",
        "io_error",
        "invalid_request",
        "runtime_failure",
        "cancelled",
    )
}
