import json

import numpy as np
import pytest

from film_foundry.core.engine import develop_negative, scan_medium_output, scan_negative
from film_foundry.core.negative_io import (
    load_developed_medium_for_scan,
    load_developed_negative_npz,
    load_negative_density_arrays,
)
from film_foundry.core.states import developed_medium_metadata
from film_foundry.model.config import DarkroomConfig


def test_numeric_negative_npz_loads_without_pickle(tmp_path):
    path = tmp_path / "negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(path, density_cmy=density, density_grain=density + 0.1)

    density_cmy, density_grain = load_negative_density_arrays(path)

    assert density_cmy.shape == (2, 3, 3)
    assert density_grain.shape == (2, 3, 3)
    assert density_grain.dtype == np.float32


def test_legacy_object_negative_npz_is_rejected_by_default(tmp_path):
    path = tmp_path / "legacy_negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    legacy = {"density_cmy": density, "density_grain": density}
    np.savez_compressed(path, legacy=np.array(legacy, dtype=object))

    with pytest.raises(ValueError, match="does not load pickled"):
        load_negative_density_arrays(path)


def test_legacy_object_negative_npz_can_be_loaded_when_explicitly_trusted(tmp_path):
    path = tmp_path / "legacy_negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    legacy = {"density_cmy": density, "density_grain": density + 0.2}
    np.savez_compressed(path, legacy=np.array(legacy, dtype=object))

    density_cmy, density_grain = load_negative_density_arrays(path, allow_legacy_pickle=True)

    assert density_cmy.shape == (2, 3, 3)
    assert np.allclose(density_grain, 0.2)


def test_developed_medium_metadata_round_trips_from_npz(tmp_path):
    path = tmp_path / "negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    metadata = {
        "medium_family": "film",
        "medium_process": "negative",
        "image_polarity": "negative",
        "view_mode": "transmissive",
        "base_type": "orange_mask",
        "color_system": "color_negative_dye",
        "compatible_interpreters": ["negative_scan"],
    }
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density + 0.1,
        developed_medium_metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )

    negative = load_developed_negative_npz(path)

    assert negative.medium_process == "negative"
    assert negative.image_polarity == "negative"
    assert negative.view_mode == "transmissive"
    assert negative.base_type == "orange_mask"
    assert negative.compatible_interpreters == ("negative_scan",)
    assert negative.metadata["developed_medium"]["color_system"] == "color_negative_dye"


def test_authoritative_optical_master_and_clear_base_round_trip_from_npz(tmp_path):
    path = tmp_path / "optical_master.npz"
    density = np.full((2, 3, 3), 0.4, dtype=np.float32)
    optical = np.full((2, 3, 3), (0.3, 0.7, 1.1), dtype=np.float32)
    clear = np.asarray((0.22, 0.58, 0.90), dtype=np.float32)
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        optical_density_rgb=optical,
        clear_base_optical_density_rgb=clear,
    )

    medium = load_developed_negative_npz(path)

    assert np.array_equal(medium.optical_density_rgb, optical)
    assert np.allclose(medium.clear_base_optical_density_rgb, clear)


def test_npz_full_loader_marks_unavailable_history_without_allocating_full_frames(tmp_path):
    path = tmp_path / "no_history.npz"
    density = np.full((7, 11, 3), 0.4, dtype=np.float32)
    np.savez_compressed(path, density_cmy=density, density_grain=density)

    medium = load_developed_negative_npz(path)

    assert medium.layer_masters_available is True
    assert medium.frame_shape == density.shape
    for history in (medium.linear_input, medium.after_mtf, medium.after_halation):
        assert history.shape == (0, 0, 3)
        assert history.dtype == np.float32
        assert history.nbytes == 0
    assert medium.metadata["stage_storage"]["history"] == "unavailable"


def test_scan_loader_keeps_only_authoritative_optical_master_and_matches_full_scan(tmp_path):
    path = tmp_path / "optical_scan.darkroom_negative.npz"
    density = np.linspace(0.1, 0.9, 9 * 13 * 3, dtype=np.float32).reshape(9, 13, 3)
    optical = np.linspace(0.2, 1.4, 9 * 13 * 3, dtype=np.float32).reshape(9, 13, 3)
    metadata = {
        "medium_family": "film",
        "medium_process": "negative",
        "image_polarity": "negative",
        "view_mode": "transmissive",
        "base_type": "orange_mask",
        "color_system": "color_negative_dye",
        "compatible_interpreters": ["negative_scan"],
    }
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density + 0.02,
        optical_density_rgb=optical,
        developed_medium_metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )

    full = load_developed_negative_npz(path)
    scan_only = load_developed_medium_for_scan(path)
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    expected = scan_negative(full, config).output_srgb
    actual = scan_negative(scan_only, config).output_srgb
    output_only = scan_medium_output(scan_only, config, "negative")

    assert scan_only.layer_masters_available is False
    assert scan_only.density_cmy.shape == (0, 0, 3)
    assert scan_only.density_grain.shape == (0, 0, 3)
    assert scan_only.density_cmy.nbytes == 0
    assert scan_only.density_grain.nbytes == 0
    assert scan_only.frame_shape == optical.shape
    assert scan_only.metadata["stage_storage"]["profile"] == "scan_optical_only_v1"
    assert np.array_equal(actual, expected)
    assert np.array_equal(output_only.output_srgb, expected)
    assert output_only.metadata["execution_profile"] == "output_only_exact_v1"


def test_scan_loader_falls_back_to_portable_layers_for_legacy_npz(tmp_path):
    path = tmp_path / "legacy_layers_only.npz"
    density = np.linspace(0.1, 0.8, 5 * 7 * 3, dtype=np.float32).reshape(5, 7, 3)
    np.savez_compressed(path, density_cmy=density, density_grain=density + 0.01)

    medium = load_developed_medium_for_scan(path)

    assert medium.layer_masters_available is True
    assert medium.optical_density_rgb is None
    assert np.array_equal(medium.density_cmy, density)
    assert np.array_equal(medium.density_grain, density + 0.01)


def test_positive_transparency_metadata_selects_scan_interpreter_without_sidecar(tmp_path):
    path = tmp_path / "slide.npz"
    density = np.linspace(0.05, 1.2, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    metadata = {
        "medium_family": "film",
        "medium_process": "slide",
        "image_polarity": "positive",
        "view_mode": "transmissive",
        "base_type": "clear_base",
        "color_system": "positive_dye",
        "compatible_interpreters": ["positive_transparency_scan"],
    }
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )

    slide = load_developed_negative_npz(path)
    scanned = scan_negative(slide)

    assert scanned.interpreter_key == "positive_transparency_scan"
    assert scanned.input_polarity == "positive"
    assert scanned.output_polarity == "positive"
    assert scanned.output_srgb.shape == density.shape


def test_new_npz_optical_snapshot_is_self_contained_without_sidecar(tmp_path):
    path = tmp_path / "self_contained.darkroom_negative.npz"
    image = np.linspace(0.1, 0.9, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    medium = develop_negative(image, DarkroomConfig())
    metadata = developed_medium_metadata(medium)
    np.savez_compressed(
        path,
        density_cmy=medium.density_cmy,
        density_grain=medium.density_grain,
        developed_medium_metadata=np.asarray(json.dumps(metadata, ensure_ascii=False)),
    )

    loaded = load_developed_negative_npz(path)
    assert "runtime_config" not in loaded.metadata
    observation = loaded.metadata["film_process_model"]["optical_observation"]
    assert observation["dye_absorption_matrix"] is not None
    assert observation["base_density_rgb"]

    scan_a = DarkroomConfig()
    scan_a.scanner.scan_normalize = False
    scan_b = DarkroomConfig()
    scan_b.scanner.scan_normalize = False
    scan_b.film.film_base_density_rgb = (2.0, 2.0, 0.0)
    scan_b.film.dye_absorption_matrix = (
        (2.0, 0.0, 0.0),
        (0.0, 0.0, 2.0),
        (0.0, 2.0, 0.0),
    )

    rendered_a = scan_negative(loaded, scan_a)
    rendered_b = scan_negative(loaded, scan_b)
    assert np.allclose(rendered_a.output_srgb, rendered_b.output_srgb)
    assert np.array_equal(loaded.density_cmy, medium.density_cmy)
    assert np.array_equal(loaded.density_grain, medium.density_grain)


@pytest.mark.parametrize("bad_value", (np.nan, np.inf, -np.inf))
def test_npz_reader_rejects_non_finite_density_at_load_boundary(tmp_path, bad_value):
    path = tmp_path / "nonfinite.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    density[0, 0, 0] = bad_value
    np.savez_compressed(path, density_cmy=density, density_grain=np.zeros_like(density))

    with pytest.raises(ValueError, match="NaN|Infinity"):
        load_negative_density_arrays(path)


def test_npz_reader_rejects_empty_mismatched_and_truncated_media(tmp_path):
    empty_path = tmp_path / "empty_arrays.npz"
    np.savez_compressed(
        empty_path,
        density_cmy=np.zeros((0, 3, 3), dtype=np.float32),
        density_grain=np.zeros((0, 3, 3), dtype=np.float32),
    )
    with pytest.raises(ValueError, match="HxWx3"):
        load_negative_density_arrays(empty_path)

    mismatch_path = tmp_path / "mismatch.npz"
    np.savez_compressed(
        mismatch_path,
        density_cmy=np.zeros((2, 3, 3), dtype=np.float32),
        density_grain=np.zeros((2, 4, 3), dtype=np.float32),
    )
    with pytest.raises(ValueError, match="形状不一致"):
        load_negative_density_arrays(mismatch_path)

    truncated_path = tmp_path / "truncated.npz"
    truncated_path.write_bytes(b"PK\x03\x04incomplete")
    with pytest.raises(ValueError, match="truncated|valid ZIP"):
        load_negative_density_arrays(truncated_path)


def test_npz_reader_rejects_malformed_or_contradictory_embedded_metadata(tmp_path):
    density = np.zeros((2, 3, 3), dtype=np.float32)
    malformed = tmp_path / "malformed_metadata.npz"
    np.savez_compressed(
        malformed,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=np.asarray('{"image_polarity": NaN}'),
    )
    with pytest.raises(ValueError, match="metadata|Non-standard"):
        load_developed_negative_npz(malformed)

    contradictory = tmp_path / "contradictory_metadata.npz"
    np.savez_compressed(
        contradictory,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=np.asarray(json.dumps({
            "image_polarity": "positive",
            "compatible_interpreters": ["negative_scan"],
        })),
    )
    with pytest.raises(ValueError, match="正片 metadata"):
        load_developed_negative_npz(contradictory)


def test_npz_reader_rejects_string_instead_of_interpreter_list(tmp_path):
    path = tmp_path / "bad_interpreters.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=np.asarray(json.dumps({
            "image_polarity": "negative",
            "compatible_interpreters": "negative_scan",
        })),
    )

    with pytest.raises(ValueError, match="字符串列表"):
        load_developed_negative_npz(path)


def test_positive_filename_is_last_resort_identity_when_metadata_and_sidecar_are_absent(tmp_path):
    path = tmp_path / "legacy.darkroom_positive.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(path, density_cmy=density, density_grain=density)

    medium = load_developed_negative_npz(path)

    assert medium.image_polarity == "positive"
    assert medium.medium_process == "slide"
    assert medium.compatible_interpreters == ("positive_transparency_scan",)
    assert medium.metadata["medium_identity_source"] == "filename_fallback"


def test_npz_reader_rejects_present_but_corrupt_sidecar(tmp_path):
    path = tmp_path / "legacy.darkroom_positive.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(path, density_cmy=density, density_grain=density)
    path.with_suffix(path.suffix + ".json").write_text(
        '{"kind":"DevelopedPositiveTransparency","config":{"bad":NaN}}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid developed-medium sidecar"):
        load_developed_negative_npz(path)


@pytest.mark.parametrize(
    "payload",
    (
        '[]',
        '{"config": []}',
        '{"developed_medium": []}',
        '{"kind": "first", "kind": "second"}',
    ),
)
def test_npz_reader_rejects_malformed_sidecar_structure(tmp_path, payload):
    path = tmp_path / "medium.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(path, density_cmy=density, density_grain=density)
    path.with_suffix(path.suffix + ".json").write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid developed-medium sidecar"):
        load_developed_negative_npz(path)


def test_partial_positive_metadata_receives_consistent_positive_defaults(tmp_path):
    path = tmp_path / "partial_positive.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=np.asarray(json.dumps({"image_polarity": "positive"})),
    )

    medium = load_developed_negative_npz(path)

    assert medium.medium_process == "slide"
    assert medium.base_type == "clear_base"
    assert medium.compatible_interpreters == ("positive_transparency_scan",)
