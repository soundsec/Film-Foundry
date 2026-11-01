from __future__ import annotations

from pathlib import Path
import json
import zipfile

import numpy as np

from film_foundry.contracts import data_kernel
from film_foundry.core.engine import scan_negative
from film_foundry.core.latent_io import load_exposed_latent
from film_foundry.core.negative_io import load_developed_medium_for_scan
from film_foundry.model.config import DarkroomConfig

_RESOURCES = Path(__file__).resolve().parent / "fixtures" / "interchange"


def _resource(name: str) -> Path:
    return _RESOURCES / name


def _assert_npy_not_pickle(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert names
        assert all(name.endswith(".npy") for name in names)
        for name in names:
            payload = archive.read(name)
            assert payload.startswith(b"\x93NUMPY")
            header = payload[10:128].decode("latin-1")
            assert "|O" not in header
            assert "pickle" not in header.lower()


def test_phone_optical_fixture_is_desktop_scannable():
    path = _resource("phone_optical_python_sample.npz")
    _assert_npy_not_pickle(path)
    medium = load_developed_medium_for_scan(path)
    before = np.array(medium.optical_density_rgb, copy=True)
    assert medium.image_polarity == "negative"
    assert medium.layer_masters_available is False
    assert medium.metadata["frame_identity"] == {"roll_id": "pocket-roll", "frame_index": 1}
    np.testing.assert_allclose(medium.optical_density_rgb[0, 0, 0], 0.10, atol=1e-6)
    np.testing.assert_allclose(medium.optical_density_rgb[-1, -1, -1], 1.80, atol=1e-6)
    assert "canonical_full_v1" not in json.dumps(medium.metadata)
    with np.load(path, allow_pickle=False) as packed:
        assert "optical_density_rgb" in packed.files
        assert "developed_medium_metadata" in packed.files
        assert "density_cmy" not in packed.files
        assert "density_grain" not in packed.files

    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    scanned = scan_negative(medium, config)
    np.testing.assert_array_equal(medium.optical_density_rgb, before)
    assert scanned.output_srgb.shape == (2, 3, 3)


def test_phone_latent_fixture_is_desktop_loadable():
    path = _resource("phone_latent_python_sample.npz")
    _assert_npy_not_pickle(path)
    state, metadata = load_exposed_latent(path)
    assert metadata["kind"] == data_kernel.LATENT_KIND
    assert metadata["stage"] == data_kernel.LATENT_STAGE
    assert metadata["consumed"] is False
    assert metadata["frame_identity"] == {"roll_id": "pocket-roll", "frame_index": 1}
    np.testing.assert_allclose(state.halide, 0.5, atol=1e-6)
    np.testing.assert_allclose(state.developability, 0.8, atol=1e-6)
    np.testing.assert_array_equal(state.metallic_silver, 0.0)
    with np.load(path, allow_pickle=False) as packed:
        assert set(data_kernel.phone_latent_ingest_npz_keys()) <= set(packed.files)
        assert "density_cmy" not in packed.files


def test_phone_reduced_profile_optical_is_desktop_scannable(tmp_path):
    optical = np.linspace(0.15, 0.9, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
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
                "profile_id": data_kernel.PHONE_REDUCED_PROFILE_ID,
                "lower_order_enabled": True,
            }
        },
    }
    path = tmp_path / f"phone_frame{data_kernel.NEGATIVE_SUFFIX}"
    np.savez_compressed(
        path,
        optical_density_rgb=optical,
        developed_medium_metadata=np.asarray(
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        ),
    )
    medium = load_developed_medium_for_scan(path)
    before = np.array(medium.optical_density_rgb, copy=True)
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    scanned = scan_negative(medium, config)
    assert medium.metadata["film_process_model"]["execution_implementation"]["profile_id"] == (
        data_kernel.PHONE_REDUCED_PROFILE_ID
    )
    assert medium.metadata["film_process_model"]["execution_implementation"]["profile_id"] != (
        "canonical_full_v1"
    )
    np.testing.assert_array_equal(medium.optical_density_rgb, before)
    assert scanned.output_srgb.shape == optical.shape


def test_phone_library_index_sample_is_application_json_not_npz():
    path = _resource("phone_library_index_sample.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == data_kernel.PHONE_LIBRARY_INDEX_KIND
    assert payload["version"] == 1  # Historical catalog remains a supported legacy input.
    assert data_kernel.PHONE_LIBRARY_INDEX_VERSION == 3
    assert payload["rolls"] == []
    assert payload["results"] == []
    assert path.suffix == ".json"
    assert not path.read_bytes().startswith(b"PK")
    assert data_kernel.PHONE_LIBRARY_INDEX_KIND not in data_kernel.NPZ_SCAN_AUTHORITY_ARRAY_KEYS
