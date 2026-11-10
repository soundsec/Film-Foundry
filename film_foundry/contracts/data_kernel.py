"""Inventory of the shared data kernel already used by desktop interchange.

Field names are copied from current DevelopedNegative / NPZ / workflow
surfaces. Changing a name here without a matching production change is a
catalog bug, not a format migration.
"""

from __future__ import annotations

# Historical on-disk suffixes. Do not rename to look Android-friendly.
NEGATIVE_SUFFIX = ".darkroom_negative.npz"
POSITIVE_SUFFIX = ".darkroom_positive.npz"

# Headless commands with a desktop load path. New latents bind their material
# parameters; older archives still need caller-supplied material at development.
WORKFLOW_COMMANDS = ("full", "develop", "expose", "develop_latent", "scan", "reexpose")

# Coarse application-layer stages. Not a medium JSON field. fraction is
# optional and may be omitted; this is not a wall-clock or per-tile meter.
WORKFLOW_PROGRESS_STAGES = (
    "starting",
    "loading",
    "exposing",
    "developing",
    "saving",
    "scanning",
    "completed",
)
WORKFLOW_PROGRESS_FIELDS = ("command", "stage", "fraction")
WORKFLOW_ERROR_CODES = (
    "unsupported_command",
    "input_not_found",
    "io_permission",
    "io_error",
    "invalid_request",
    "runtime_failure",
    "cancelled",
)

POLARITY_VALUES = ("negative", "positive")

# Exposed undeveloped latent archive. New file kind; does not rename developed
# NPZ keys. Motivation is mobile shoot-then-develop; the contract is shared.
LATENT_SUFFIX = ".darkroom_latent.npz"
LATENT_KIND = "exposed_undeveloped_latent_v1"
LATENT_STAGE = "exposed_latent"
LATENT_JSON_KEYS = ("latent_medium_metadata",)
LATENT_REQUIRED_ARRAY_KEYS = ("halide", "developability", "metallic_silver")
LATENT_OPTIONAL_ARRAY_KEYS = (
    "coupler",
    "dye",
    "bleached_halide",
    "original_developability",
)
LATENT_SCALAR_FIELDS = ("auxiliary_remaining", "masking_coupler_remaining")
MATERIAL_SNAPSHOT_KEY = "material_snapshot"
MATERIAL_SNAPSHOT_MODEL = "film_material_parameters_v1"
FILM_REBATE_EXPOSURE_KEY = "film_border_snapshot"
FILM_REBATE_EXPOSURE_MODEL = "film_rebate_exposure_v1"

# Optional provenance of medium-to-material optical re-exposure. Copies to the
# final film_process_model; no source pixels or source state are overwritten.
OPTICAL_CASCADE_KEY = 'optical_cascade'
OPTICAL_CASCADE_MODEL = 'optical_cascade_v1'

# Optional final optical strip. Existing RGB / spectral arrays already contain the border.
FORMED_FILM_REBATE_KEY = "formed_film_rebate"
FORMED_FILM_REBATE_MODEL = "formed_rebate_reference_v1"
FORMED_FILM_REBATE_FIELDS = ("model", "image_height", "rebate_rows", "holes_per_row", "label", "order", "spatial_reference")

# Portable identity stored in developed_medium_metadata JSON and restored by
# film_foundry.core.negative_io._metadata_kwargs.
MEDIUM_IDENTITY_FIELDS = (
    "medium_family",
    "medium_process",
    "image_polarity",
    "view_mode",
    "base_type",
    "color_system",
    "compatible_interpreters",
)

# Optional nested object on latent_medium_metadata and
# developed_medium_metadata. Absence remains legal for old files.
FRAME_IDENTITY_OBJECT_KEY = "frame_identity"
FRAME_IDENTITY_FIELDS = ("roll_id", "frame_id", "frame_index")

# Optional recipe seed, carried by JSON/FFC2, not a medium array or roll index.
# Desktop implements these components; the phone reduced kernel currently
# omits the spatial incident operators and must not claim identical defects.
INCIDENT_IDENTITY_RECIPE_FIELD = "develop.incident_identity_seed"
INCIDENT_IDENTITY_MODEL = "roll_batch_incident_identity_v1"
INCIDENT_IDENTITY_COMPONENTS = (
    "light_leak", "gate_occlusion", "process_variation", "uneven_development",
    "chemical_stain", "silver_plating", "reticulation",
)

# ZIP/NPY arrays a current desktop writer may emit. Legacy layer load still
# requires the two density_* keys when optical_density_rgb is absent.
NPZ_LAYER_COMPAT_ARRAY_KEYS = ("density_cmy", "density_grain")
NPZ_SCAN_AUTHORITY_ARRAY_KEYS = ("optical_density_rgb",)
NPZ_JSON_KEYS = ("developed_medium_metadata",)
NPZ_OPTIONAL_ARRAY_KEYS = (
    "clear_base_optical_density_rgb",
    "film_foundry_provenance",
    "spectral_component_amounts",
    "spectral_post_process_density_rgb",
)

# Phone kernel stamps this on formed media. Desktop must not treat it as
# canonical_full_v1. Absence remains legal for old desktop files.
PHONE_REDUCED_PROFILE_ID = "phone_reduced_v0"

# Optional nested object on latent_medium_metadata, copied onto
# developed_medium_metadata as ingest provenance. Capture is not a medium
# array, not a second schema, and not scene-referred by default. Desktop may
# ignore it. Absence remains legal.
CAPTURE_OBJECT_KEY = "capture"
CAPTURE_KIND_VALUES = (
    "display_referred_srgb",
    "ultra_hdr_gainmap",
    "sensor_raw_dng",
    "linear_working_rgb",
    "exposure_stack",
)
CAPTURE_RECORD_FIELDS = (
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
CAPTURE_HDR_GAINMAP_VALUES = (
    "absent",
    "present_not_applied",
    "applied",
)
CAPTURE_WORKING_SPACE = "srgb_linear_iec61966_2_1_v0"

# Optional film configuration and frozen exposure provenance. Stored in latent
# metadata and developed film_process_model, never as a new image array.
SPECTRAL_EXPOSURE_PROFILE_KEY = "spectral_exposure_profile"
SPECTRAL_EXPOSURE_MODEL_ID = "rgb_basis_spectral_exposure_v1"
SMITS_EXPOSURE_MODEL_ID = "smits7_spectral_exposure_v1"
SMITS_BASIS_ORDER = ("white", "cyan", "magenta", "yellow", "red", "green", "blue")
SPECTRAL_EXPOSURE_PROFILE_FIELDS = (
    "model", "profile_id", "working_space", "wavelengths_nm",
    "rgb_basis", "layer_sensitivity", "layer_gain",
)
SPECTRAL_MATERIAL_PROFILE_KEY = "spectral_material_profile"
SPECTRAL_MEDIUM_KEY = "spectral_medium"
SPECTRAL_COMPONENT_ARRAY_KEY = "spectral_component_amounts"
SPECTRAL_POST_PROCESS_ARRAY_KEY = "spectral_post_process_density_rgb"
SPECTRAL_OBSERVATION_PROFILE_KEY = "spectral_observation_profile"
# The two definitions and component order live in contracts/spectral_optics.
# A developed capability consists of both JSON definition and HxWx9 amounts.

# Phone application library. Not medium arrays and not a second NPZ schema.
# A roll is a generator of undeveloped latents; a developed result is a
# frozen transmissive optical master and cannot recreate the roll.
PHONE_ROLL_CAPACITY_VALUES = (8, 10, 12, 16, 24, 36, 48, 72)
PHONE_LIBRARY_KINDS = (
    "undeveloped_roll",
    "developed_result",
)
PHONE_ROLL_LIFECYCLE = (
    "loading",
    "shooting",
    "sealed",
    "discarded",
)
# Application-private roll catalog. Not a medium archive and not an
# generic interrupt-resume unit. Capture receipts preserve sources until latent
# formation; formed media still require complete NPZ files. v1 remains readable.
PHONE_LIBRARY_INDEX_KIND = "phone_library_index_v0"
PHONE_LIBRARY_INDEX_VERSION = 3
PHONE_LIBRARY_INDEX_FILENAME = "phone_library_index.json"
# Named film/scanner recipes, independent of a loaded roll. Application-private
# JSON, not a medium archive, not FFC2 in the gallery, and not an
# interrupt-resume unit. Entry.recipe is the FFC2 film or scanner object.
PHONE_RECIPE_CATALOG_KIND = "phone_recipe_catalog_v0"
PHONE_RECIPE_CATALOG_VERSION = 1
PHONE_RECIPE_CATALOG_FILENAME = "phone_recipe_catalog.json"
PHONE_RECIPE_CATALOG_ENTRY_KINDS = ("film", "scanner")
PHONE_LIBRARY_FRAME_STATES = (
    "empty",
    "latent",
    "failed",
    "capturing",
    "captured",
)

# Phone observation of a developed transparent master. Light table and
# camera backlight stay inside the app. Projection throws a picture.
# Scanner interprets and also stays in the app library. Only an explicit
# library save of that projection may enter the system photo gallery.
# These names are not medium array keys.
PHONE_OBSERVE_MODES = (
    "viewer_light_table",
    "viewer_projection",
    "scanner",
)
PHONE_VIEWER_ILLUMINANT = (
    "synthetic_light_table",
    "scene_camera",
)
# Observe-recipe illuminant. Not a capture kind and not a medium array.
# Preview frames may light the viewer; they cannot fill a roll.
PHONE_SCENE_ILLUMINANT_RGB_KEY = "scene_illuminant_rgb"
PHONE_SCENE_ILLUMINANT_IMAGE_KEY = "scene_illuminant_rgb_image"
PHONE_VIEWER_PREVIEW_KIND = "viewer_preview_illuminant_v0"
PHONE_VIEWER_ILLUMINANT_REDUCTION = (
    "mean_linear_rgb_v0",
    "spatial_linear_rgb_v0",
)
PHONE_GALLERY_ITEM_KIND = "projection_observation"
PHONE_LIGHT_TABLE_FIELDS = (
    "transmission_light_ev",
    "transmission_light_temperature_k",
)
PHONE_SCANNER_INTERPRETATION_FIELDS = (
    "remove_base_mask",
    "invert_transmission",
    "color_curve_rgb",
    "color_mixer",
    "tone_curve",
    "scan_normalize",
    "scan_normalize_strength",
    "scan_normalize_mode",
    "scan_black_percentile",
    "scan_white_percentile",
)
PHONE_SCAN_NORMALIZE_MODES = ("luma", "rgb")
PHONE_GALLERY_IMAGE_MIMES = (
    "image/png",
    "image/jpeg",
    "image/webp",
)
# CameraX session roles and MediaStore insert. Application/platform
# wiring, not capture kinds and not medium array keys. Live androidx
# types stay in a future AGP module; these names are the JVM contract.
PHONE_CAMERA_SESSION_ROLES = (
    "fill_roll",
    "illuminate_viewer",
)
PHONE_CAMERAX_STILL_USE_CASES = ("image_capture",)
PHONE_CAMERAX_PREVIEW_USE_CASES = (
    "preview",
    "image_analysis",
)
PHONE_MEDIASTORE_COLLECTION = "mediastore_images_external_v0"
PHONE_MEDIASTORE_RELATIVE_PATH = "Pictures/FilmFoundry/"
PHONE_TRANSMISSION_CAPTURE_ID = "phone_transmission_capture_v0"
PHONE_VIEWER_MODEL_ID = "phone_light_table_viewer_v0"
PHONE_PROJECTION_MODEL_ID = "phone_projection_viewer_v0"
PHONE_SCANNER_MODEL_ID = "phone_reduced_scanner_v0"
PHONE_CROSS_CHEMISTRY_ID = "phone_cross_chemistry_v0"

# Nested object under developed_medium_metadata / medium metadata. Phone-formed
# media should stamp profile_id so desktop does not treat a reduced result as
# canonical_full_v1. Absence remains legal for old desktop files.
EXECUTION_IMPLEMENTATION_KEYS = (
    "execution_mode",
    "scaling_enabled",
    "quality_mode",
    "profile_id",
    "lower_order_enabled",
    "component_models",
    "speed_estimate",
    "speed_estimate_basis",
)

# Present on desktop saves or in-memory objects; not required for a phone
# optical master that desktop only needs to observe.
DESKTOP_ONLY_INTERCHANGE = (
    "runtime_config",
    "linear_input",
    "after_mtf",
    "after_halation",
    "film_foundry_provenance",
)

# Named gaps. These are not field names to invent in this catalog.
# Interrupt/resume is replay from complete artifacts, not a mid-process
# medium JSON object. In-flight formation is not checkpointed.
INTERRUPT_RESUME_UNITS = (
    "complete_latent_npz",
    "complete_developed_npz",
    "complete_output_image",
)
MISSING_FOR_PHONE_LIFECYCLE = (
    "portable_archive_other_than_npz",
)

# Portable recipe identity, not a medium archive and not a preset filename.
# The string carries film/develop/scanner/look parameter values. If both sides
# already have the JSON file, share the file instead.
CONFIG_SHARE_KIND = "film_foundry_config_share"
CONFIG_SHARE_VERSION = 2
MATERIAL_GEOMETRY_OBJECT_KEY = "material_geometry"
MATERIAL_GEOMETRY_MODEL_ID = "reference_frame_physical_v1"
MATERIAL_GEOMETRY_FIELDS = (
    "model", "frame_long_edge_mm", "reference_short_edge_mm", "full_shape",
)
CONFIG_SHARE_PREFIX = "FFC2:"
CONFIG_SHARE_FIELDS = (
    "film",
    "exposure",
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


def phone_scan_ingest_npz_keys() -> tuple[str, ...]:
    """Keys sufficient for desktop scan ingest of a phone-formed medium."""
    return NPZ_SCAN_AUTHORITY_ARRAY_KEYS + NPZ_JSON_KEYS


def phone_latent_ingest_npz_keys() -> tuple[str, ...]:
    """Keys sufficient for desktop develop ingest of a phone-exposed latent."""
    return LATENT_REQUIRED_ARRAY_KEYS + LATENT_JSON_KEYS
