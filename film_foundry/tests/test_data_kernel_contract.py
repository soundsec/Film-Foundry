from __future__ import annotations

from pathlib import Path
import ast
import json

import numpy as np

from film_foundry.contracts import data_kernel
from film_foundry.core.engine import scan_medium_output, scan_negative
from film_foundry.core.negative_io import (
    _metadata_kwargs,
    load_developed_medium_for_scan,
)
from film_foundry.core.states import DevelopedNegative, developed_medium_metadata
from film_foundry.model.config import DarkroomConfig


def _imported_modules(source: str) -> list[str]:
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return imported


def test_contracts_package_does_not_import_runtime_or_ui():
    forbidden_prefixes = (
        "film_foundry.core",
        "film_foundry.ui",
        "film_foundry.app",
        "film_foundry.tools",
        "numpy",
        "cv2",
        "PySide6",
    )
    root = Path(data_kernel.__file__).parent
    for path in sorted(root.glob("*.py")):
        imported = _imported_modules(path.read_text(encoding="utf-8"))
        assert not any(
            name == prefix or name.startswith(prefix + ".")
            for name in imported
            for prefix in forbidden_prefixes
        ), (path.name, imported)


def test_workflow_commands_match_headless_api():
    from film_foundry.app.api import WORKFLOW_COMMANDS, WORKFLOW_ERROR_CODES
    from film_foundry.app.control import WorkflowProgress

    assert data_kernel.WORKFLOW_COMMANDS == WORKFLOW_COMMANDS
    assert data_kernel.WORKFLOW_ERROR_CODES == WORKFLOW_ERROR_CODES
    assert tuple(WorkflowProgress.__dataclass_fields__) == data_kernel.WORKFLOW_PROGRESS_FIELDS
    assert "cancelled" in data_kernel.WORKFLOW_ERROR_CODES
    assert "progress_cancel_contract" not in data_kernel.MISSING_FOR_PHONE_LIFECYCLE
    assert "interrupt_resume" not in data_kernel.MISSING_FOR_PHONE_LIFECYCLE
    assert data_kernel.INTERRUPT_RESUME_UNITS == (
        "complete_latent_npz",
        "complete_developed_npz",
        "complete_output_image",
    )
    assert "starting" in data_kernel.WORKFLOW_PROGRESS_STAGES
    assert "completed" in data_kernel.WORKFLOW_PROGRESS_STAGES


def test_medium_suffixes_match_workflow_and_engine():
    from film_foundry.app import workflow
    from film_foundry.core import engine

    assert data_kernel.NEGATIVE_SUFFIX == workflow.NEGATIVE_SUFFIX == engine.NEGATIVE_SUFFIX
    assert data_kernel.POSITIVE_SUFFIX == workflow.POSITIVE_SUFFIX == engine.POSITIVE_SUFFIX


def test_identity_fields_match_npz_metadata_adapter():
    payload = {
        "medium_family": "silver_halide_film",
        "medium_process": "negative",
        "image_polarity": "negative",
        "view_mode": "transmissive",
        "base_type": "orange_mask",
        "color_system": "color_negative_dye",
        "compatible_interpreters": ["negative_scan"],
    }
    identity = _metadata_kwargs(payload, Path("frame.darkroom_negative.npz"))
    assert tuple(sorted(identity)) == tuple(sorted(data_kernel.MEDIUM_IDENTITY_FIELDS))


def test_developed_medium_metadata_emits_identity_and_array_semantics():
    medium = DevelopedNegative(
        linear_input=np.empty((0, 0, 3), dtype=np.float32),
        after_mtf=np.empty((0, 0, 3), dtype=np.float32),
        after_halation=np.empty((0, 0, 3), dtype=np.float32),
        density_cmy=np.zeros((2, 3, 3), dtype=np.float32),
        density_grain=np.zeros((2, 3, 3), dtype=np.float32),
        optical_density_rgb=np.full((2, 3, 3), 0.4, dtype=np.float32),
    )
    payload = developed_medium_metadata(medium)
    assert set(data_kernel.MEDIUM_IDENTITY_FIELDS).issubset(payload)
    assert payload["array_semantics_version"] == 1
    assert payload["authoritative_optical_density_rgb"] is True
    assert payload["array_semantics"]["optical_density_rgb"]["authoritative_for_scan"] is True
    assert payload["array_semantics"]["density_cmy"]["authoritative_for_scan"] is False
    assert "frame_identity" not in payload


def test_frame_identity_catalog_matches_core_and_is_not_material_identity():
    from film_foundry.core import frame_identity as core_identity

    assert data_kernel.FRAME_IDENTITY_OBJECT_KEY == core_identity.FRAME_IDENTITY_KEY
    assert data_kernel.FRAME_IDENTITY_FIELDS == core_identity.FRAME_IDENTITY_FIELDS
    assert data_kernel.FRAME_IDENTITY_OBJECT_KEY not in data_kernel.MEDIUM_IDENTITY_FIELDS
    for name in data_kernel.FRAME_IDENTITY_FIELDS:
        assert name not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert "roll_identity" not in data_kernel.MISSING_FOR_PHONE_LIFECYCLE
    assert "frame_identity" not in data_kernel.MISSING_FOR_PHONE_LIFECYCLE


def test_developed_medium_metadata_emits_optional_frame_identity():
    medium = DevelopedNegative(
        linear_input=np.empty((0, 0, 3), dtype=np.float32),
        after_mtf=np.empty((0, 0, 3), dtype=np.float32),
        after_halation=np.empty((0, 0, 3), dtype=np.float32),
        density_cmy=np.zeros((2, 3, 3), dtype=np.float32),
        density_grain=np.zeros((2, 3, 3), dtype=np.float32),
        optical_density_rgb=np.full((2, 3, 3), 0.4, dtype=np.float32),
    )
    medium.metadata["frame_identity"] = {
        "roll_id": "roll-a",
        "frame_id": "A12",
        "frame_index": 12,
    }
    payload = developed_medium_metadata(medium)
    assert payload["frame_identity"] == {
        "roll_id": "roll-a",
        "frame_id": "A12",
        "frame_index": 12,
    }



def test_execution_implementation_keys_match_plan_dict():
    from film_foundry.core.implementation_plan import ImplementationPlan

    plan = ImplementationPlan(
        execution_mode="quality",
        scaling_enabled=False,
        quality_mode="standard",
        profile_id="canonical_full_v1",
        lower_order_enabled=False,
        emulsion_mtf_model="adaptive_dual_psf_v1",
        emulsion_grain_model="multiscale_layer_density_v1",
        halation_source_model="scattered_peak_area_v1",
    )
    assert tuple(plan.as_dict()) == data_kernel.EXECUTION_IMPLEMENTATION_KEYS


def test_phone_optical_master_without_layer_arrays_is_desktop_scannable(tmp_path):
    optical = np.linspace(0.15, 1.2, 5 * 7 * 3, dtype=np.float32).reshape(5, 7, 3)
    metadata = {
        "medium_family": "film",
        "medium_process": "negative",
        "image_polarity": "negative",
        "view_mode": "transmissive",
        "base_type": "orange_mask",
        "color_system": "color_negative_dye",
        "compatible_interpreters": ["negative_scan"],
        "film_process_model": {
            "execution_implementation": {
                "profile_id": "builtin_reduced_fast_v1",
                "lower_order_enabled": True,
            }
        },
    }
    path = tmp_path / f"phone_frame{data_kernel.NEGATIVE_SUFFIX}"
    np.savez_compressed(
        path,
        optical_density_rgb=optical,
        developed_medium_metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )

    medium = load_developed_medium_for_scan(path)
    before = np.array(medium.optical_density_rgb, copy=True)
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    scanned = scan_negative(medium, config)
    output_only = scan_medium_output(medium, config, "auto")

    assert medium.image_polarity == "negative"
    assert medium.layer_masters_available is False
    assert medium.metadata["film_process_model"]["execution_implementation"]["profile_id"] == (
        "builtin_reduced_fast_v1"
    )
    np.testing.assert_array_equal(medium.optical_density_rgb, before)
    assert scanned.output_srgb.shape == optical.shape
    np.testing.assert_array_equal(output_only.output_srgb, scanned.output_srgb)


def test_phone_scan_ingest_key_helper_matches_catalog():
    assert data_kernel.phone_scan_ingest_npz_keys() == (
        "optical_density_rgb",
        "developed_medium_metadata",
    )
    assert data_kernel.phone_latent_ingest_npz_keys() == (
        "halide",
        "developability",
        "metallic_silver",
        "latent_medium_metadata",
    )
    for name in data_kernel.MISSING_FOR_PHONE_LIFECYCLE:
        assert name not in data_kernel.MEDIUM_IDENTITY_FIELDS
        assert name not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS


def test_config_share_catalog_constants_are_exported():
    from film_foundry import contracts

    assert data_kernel.CONFIG_SHARE_KIND == "film_foundry_config_share"
    assert data_kernel.CONFIG_SHARE_VERSION == 2
    assert data_kernel.CONFIG_SHARE_PREFIX == "FFC2:"
    assert data_kernel.CONFIG_SHARE_FIELDS[0:3] == ("film", "develop", "scanner")
    assert "look" in data_kernel.CONFIG_SHARE_FIELDS
    assert contracts.CONFIG_SHARE_PREFIX == data_kernel.CONFIG_SHARE_PREFIX
    assert "share-config" not in data_kernel.WORKFLOW_COMMANDS
    assert "portable_config_share" not in data_kernel.MISSING_FOR_PHONE_LIFECYCLE
    assert data_kernel.PHONE_REDUCED_PROFILE_ID == "phone_reduced_v0"
    assert data_kernel.PHONE_REDUCED_PROFILE_ID != "canonical_full_v1"
    assert contracts.PHONE_REDUCED_PROFILE_ID == data_kernel.PHONE_REDUCED_PROFILE_ID


def test_capture_catalog_is_ingest_not_medium_identity():
    from film_foundry import contracts

    assert data_kernel.CAPTURE_OBJECT_KEY == "capture"
    assert data_kernel.CAPTURE_OBJECT_KEY not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.CAPTURE_OBJECT_KEY not in data_kernel.LATENT_REQUIRED_ARRAY_KEYS
    assert data_kernel.CAPTURE_OBJECT_KEY not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert data_kernel.CAPTURE_OBJECT_KEY not in data_kernel.NPZ_JSON_KEYS
    assert data_kernel.CAPTURE_KIND_VALUES == (
        "display_referred_srgb",
        "ultra_hdr_gainmap",
        "sensor_raw_dng",
        "linear_working_rgb",
        "exposure_stack",
    )
    assert data_kernel.CAPTURE_HDR_GAINMAP_VALUES == (
        "absent",
        "present_not_applied",
        "applied",
    )
    assert data_kernel.CAPTURE_RECORD_FIELDS[0] == "kind"
    assert data_kernel.CAPTURE_WORKING_SPACE == "srgb_linear_iec61966_2_1_v0"
    assert contracts.CAPTURE_KIND_VALUES == data_kernel.CAPTURE_KIND_VALUES
    assert "preview" not in data_kernel.CAPTURE_KIND_VALUES
    assert "jpeg" not in data_kernel.LATENT_REQUIRED_ARRAY_KEYS


def test_phone_roll_library_catalog_is_application_not_medium():
    from film_foundry import contracts

    assert data_kernel.PHONE_ROLL_CAPACITY_VALUES == (8, 10, 12, 16, 24, 36, 48, 72)
    assert data_kernel.PHONE_LIBRARY_KINDS == (
        "undeveloped_roll",
        "developed_result",
    )
    assert data_kernel.PHONE_ROLL_LIFECYCLE == (
        "loading",
        "shooting",
        "sealed",
        "discarded",
    )
    assert "undeveloped_roll" not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert "developed_result" not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert "capacity" not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert contracts.PHONE_ROLL_CAPACITY_VALUES == data_kernel.PHONE_ROLL_CAPACITY_VALUES
    assert "halide" not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert data_kernel.PHONE_LIBRARY_INDEX_KIND == "phone_library_index_v0"
    assert data_kernel.PHONE_LIBRARY_INDEX_VERSION == 3
    assert data_kernel.PHONE_LIBRARY_INDEX_FILENAME == "phone_library_index.json"
    assert data_kernel.PHONE_LIBRARY_FRAME_STATES == ("empty", "latent", "failed", "capturing", "captured")
    assert data_kernel.PHONE_LIBRARY_INDEX_KIND not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.PHONE_LIBRARY_INDEX_FILENAME.endswith(".json")
    assert not data_kernel.PHONE_LIBRARY_INDEX_FILENAME.endswith(data_kernel.LATENT_SUFFIX)
    assert not data_kernel.PHONE_LIBRARY_INDEX_FILENAME.endswith(data_kernel.NEGATIVE_SUFFIX)
    assert data_kernel.PHONE_LIBRARY_INDEX_KIND not in data_kernel.INTERRUPT_RESUME_UNITS
    assert data_kernel.PHONE_LIBRARY_INDEX_KIND not in data_kernel.WORKFLOW_COMMANDS
    assert contracts.PHONE_LIBRARY_INDEX_FILENAME == data_kernel.PHONE_LIBRARY_INDEX_FILENAME
    assert "consumed" not in data_kernel.PHONE_LIBRARY_FRAME_STATES
    assert data_kernel.PHONE_RECIPE_CATALOG_KIND == "phone_recipe_catalog_v0"
    assert data_kernel.PHONE_RECIPE_CATALOG_FILENAME == "phone_recipe_catalog.json"
    assert data_kernel.PHONE_RECIPE_CATALOG_ENTRY_KINDS == ("film", "scanner")
    assert data_kernel.PHONE_RECIPE_CATALOG_KIND not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.PHONE_RECIPE_CATALOG_KIND not in data_kernel.INTERRUPT_RESUME_UNITS
    assert data_kernel.PHONE_RECIPE_CATALOG_KIND not in data_kernel.WORKFLOW_COMMANDS
    assert data_kernel.PHONE_RECIPE_CATALOG_FILENAME.endswith(".json")
    assert not data_kernel.PHONE_RECIPE_CATALOG_FILENAME.endswith(data_kernel.LATENT_SUFFIX)
    assert contracts.PHONE_RECIPE_CATALOG_FILENAME == data_kernel.PHONE_RECIPE_CATALOG_FILENAME


def test_phone_observe_gallery_catalog_is_scanner_commit_only():
    from film_foundry import contracts
    from film_foundry.model.config import ScannerConfig

    scanner_fields = ScannerConfig.__dataclass_fields__
    assert data_kernel.PHONE_OBSERVE_MODES == (
        "viewer_light_table",
        "viewer_projection",
        "scanner",
    )
    assert data_kernel.PHONE_VIEWER_ILLUMINANT == (
        "synthetic_light_table",
        "scene_camera",
    )
    assert data_kernel.PHONE_SCENE_ILLUMINANT_RGB_KEY == "scene_illuminant_rgb"
    assert data_kernel.PHONE_SCENE_ILLUMINANT_IMAGE_KEY == "scene_illuminant_rgb_image"
    assert data_kernel.PHONE_VIEWER_PREVIEW_KIND == "viewer_preview_illuminant_v0"
    assert data_kernel.PHONE_VIEWER_ILLUMINANT_REDUCTION == (
        "mean_linear_rgb_v0",
        "spatial_linear_rgb_v0",
    )
    assert data_kernel.PHONE_VIEWER_PREVIEW_KIND not in data_kernel.CAPTURE_KIND_VALUES
    assert data_kernel.PHONE_SCENE_ILLUMINANT_RGB_KEY not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.PHONE_SCENE_ILLUMINANT_IMAGE_KEY not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert contracts.PHONE_VIEWER_PREVIEW_KIND == data_kernel.PHONE_VIEWER_PREVIEW_KIND
    assert data_kernel.PHONE_GALLERY_ITEM_KIND == "projection_observation"
    assert data_kernel.PHONE_LIGHT_TABLE_FIELDS == (
        "transmission_light_ev",
        "transmission_light_temperature_k",
    )
    for name in data_kernel.PHONE_LIGHT_TABLE_FIELDS:
        assert name in scanner_fields
    for name in data_kernel.PHONE_SCANNER_INTERPRETATION_FIELDS:
        assert name in scanner_fields
    assert data_kernel.PHONE_SCAN_NORMALIZE_MODES == ("luma", "rgb")
    assert "image/png" in data_kernel.PHONE_GALLERY_IMAGE_MIMES
    assert data_kernel.PHONE_GALLERY_ITEM_KIND not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.PHONE_GALLERY_ITEM_KIND not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert contracts.PHONE_GALLERY_ITEM_KIND == data_kernel.PHONE_GALLERY_ITEM_KIND
    assert "viewer_light_table" not in data_kernel.WORKFLOW_COMMANDS
    assert data_kernel.PHONE_TRANSMISSION_CAPTURE_ID == "phone_transmission_capture_v0"
    assert data_kernel.PHONE_VIEWER_MODEL_ID == "phone_light_table_viewer_v0"
    assert data_kernel.PHONE_PROJECTION_MODEL_ID == "phone_projection_viewer_v0"
    assert data_kernel.PHONE_CROSS_CHEMISTRY_ID == "phone_cross_chemistry_v0"
    assert data_kernel.PHONE_SCANNER_MODEL_ID == "phone_reduced_scanner_v0"
    assert contracts.PHONE_VIEWER_MODEL_ID == data_kernel.PHONE_VIEWER_MODEL_ID
    assert data_kernel.PHONE_TRANSMISSION_CAPTURE_ID not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert data_kernel.PHONE_VIEWER_MODEL_ID not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.PHONE_CAMERA_SESSION_ROLES == (
        "fill_roll",
        "illuminate_viewer",
    )
    assert data_kernel.PHONE_CAMERAX_STILL_USE_CASES == ("image_capture",)
    assert data_kernel.PHONE_CAMERAX_PREVIEW_USE_CASES == (
        "preview",
        "image_analysis",
    )
    assert data_kernel.PHONE_MEDIASTORE_COLLECTION == "mediastore_images_external_v0"
    assert data_kernel.PHONE_MEDIASTORE_RELATIVE_PATH == "Pictures/FilmFoundry/"
    assert data_kernel.PHONE_MEDIASTORE_COLLECTION not in data_kernel.MEDIUM_IDENTITY_FIELDS
    assert data_kernel.PHONE_MEDIASTORE_RELATIVE_PATH not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
    assert data_kernel.PHONE_CAMERAX_STILL_USE_CASES[0] not in data_kernel.CAPTURE_KIND_VALUES
    assert contracts.PHONE_MEDIASTORE_COLLECTION == data_kernel.PHONE_MEDIASTORE_COLLECTION
    assert "image_capture" not in data_kernel.CAPTURE_KIND_VALUES
