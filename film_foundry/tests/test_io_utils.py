import cv2
import json
import numpy as np
from pathlib import Path
from PIL import Image, ImageOps
import pytest


import film_foundry.core.electronic_negative as electronic_negative
import film_foundry.core.engine as engine
import film_foundry.core.atomic_io as atomic_io
from film_foundry.core.color import ensure_rgb_float
from film_foundry.core.atomic_io import atomic_output_directory, atomic_output_path, atomic_path_set
from film_foundry.core.engine import (
    apply_optical_observation_snapshot,
    develop_negative,
    process_file,
    save_developed_medium_at_path,
)
from film_foundry.core.io_utils import assert_unique_output_stems, iter_images, load_image, load_image_with_color, probe_image_dimensions, save_image, save_image_bundle
from film_foundry.core.electronic_negative import (
    export_transparent_plate_set,
    load_linear_rgb_tiff,
    medium_transmission_rgb,
    save_linear_rgb_tiff,
    scanner_raw_export_border_width,
    split_scanner_raw_border,
)
from film_foundry.core.sidecar import (
    final_positive_sidecar,
    load_scanner_raw_optical_observation,
    load_scanner_raw_sidecar,
    scanner_raw_border_contract,
    scanner_raw_border_width_from_sidecar,
)
from film_foundry.identity import PROJECT_NAME
from film_foundry.model.config import DarkroomConfig
from film_foundry.tools import run_darkroom_gui as gui


def test_grayscale_array_becomes_rgb():
    gray = np.array([[0, 128], [255, 64]], dtype=np.uint8)

    rgb = ensure_rgb_float(gray)

    assert rgb.shape == (2, 2, 3)
    assert np.allclose(rgb[..., 0], rgb[..., 1])
    assert np.allclose(rgb[..., 1], rgb[..., 2])


def test_probe_image_dimensions_does_not_decode_pixels(tmp_path, monkeypatch):
    path = tmp_path / "尺寸.png"
    Image.new("RGB", (37, 23), (20, 40, 60)).save(path)

    def fail_decode(*_args, **_kwargs):
        raise AssertionError("dimension probing must not call OpenCV pixel decoding")

    monkeypatch.setattr(Image.Image, "load", fail_decode)
    assert probe_image_dimensions(path) == (37, 23)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_load_image_normalizes_every_exif_orientation(tmp_path, orientation):
    path = tmp_path / f"orientation_{orientation}.jpg"
    pixels = np.asarray(
        [
            [[255, 0, 0], [0, 255, 0], [0, 0, 255]],
            [[20, 40, 60], [90, 110, 130], [180, 200, 220]],
        ],
        dtype=np.uint8,
    )
    exif = Image.Exif()
    exif[274] = orientation
    Image.fromarray(pixels, mode="RGB").save(path, quality=100, subsampling=0, exif=exif)

    loaded = load_image(path)
    with Image.open(path) as encoded:
        expected = np.asarray(
            ImageOps.exif_transpose(encoded).convert("RGB"),
            dtype=np.float32,
        ) / np.float32(255.0)

    assert loaded.shape == expected.shape
    assert np.array_equal(loaded, expected)
    assert probe_image_dimensions(path) == (expected.shape[1], expected.shape[0])


def test_tiff_preserve_loader_applies_exif_orientation_without_losing_shape(tmp_path):
    path = tmp_path / "portrait_by_exif.tiff"
    pixels = np.arange(2 * 5 * 3, dtype=np.uint8).reshape(2, 5, 3)
    exif = Image.Exif()
    exif[274] = 6
    Image.fromarray(pixels, mode="RGB").save(path, exif=exif)

    loaded = load_image(path)
    with Image.open(path) as encoded:
        expected = np.asarray(
            ImageOps.exif_transpose(encoded).convert("RGB"),
            dtype=np.float32,
        ) / np.float32(255.0)

    assert loaded.shape == (5, 2, 3)
    assert np.array_equal(loaded, expected)
    assert probe_image_dimensions(path) == (2, 5)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_png_preserve_loader_normalizes_every_exif_orientation(tmp_path, orientation):
    path = tmp_path / f"orientation_{orientation}.png"
    pixels = np.asarray(
        [
            [[250, 10, 20], [30, 240, 40], [50, 60, 230]],
            [[70, 80, 90], [100, 110, 120], [130, 140, 150]],
        ],
        dtype=np.uint8,
    )
    exif = Image.Exif()
    exif[274] = orientation
    Image.fromarray(pixels, mode="RGB").save(path, exif=exif)

    loaded = load_image(path)
    with Image.open(path) as encoded:
        expected = np.asarray(
            ImageOps.exif_transpose(encoded).convert("RGB"),
            dtype=np.float32,
        ) / np.float32(255.0)

    assert np.array_equal(loaded, expected)
    assert probe_image_dimensions(path) == (expected.shape[1], expected.shape[0])


def test_oriented_input_saves_physical_portrait_without_stale_orientation(tmp_path):
    source_path = tmp_path / "camera_portrait.jpg"
    output_path = tmp_path / "processed_portrait.jpg"
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (8, 4), (30, 90, 160)).save(source_path, exif=exif)

    loaded = load_image(source_path)
    save_image(loaded, output_path, DarkroomConfig().output)

    with Image.open(output_path) as output:
        assert output.size == (4, 8)
        assert output.getexif().get(274) is None


def _s15fixed16(value: float) -> bytes:
    return int(round(value * 65536.0)).to_bytes(4, "big", signed=True)


def _u16fixed16(value: float) -> bytes:
    return int(round(value * 65536.0)).to_bytes(4, "big", signed=False)


def _xy_to_xyz(x: float, y: float) -> tuple[float, float, float]:
    return (x / y, 1.0, (1.0 - x - y) / y)


def _bradford_adapt_d65_to_d50(xyz: np.ndarray) -> np.ndarray:
    bradford = np.array(
        (
            (0.8951, 0.2664, -0.1614),
            (-0.7502, 1.7135, 0.0367),
            (0.0389, -0.0685, 1.0296),
        ),
        dtype=np.float64,
    )
    d65 = np.array((0.95047, 1.0, 1.08883), dtype=np.float64)
    d50 = np.array((0.96422, 1.0, 0.82521), dtype=np.float64)
    src = bradford @ d65
    dst = bradford @ d50
    adapted = np.linalg.inv(bradford) @ np.diag(dst / src) @ bradford
    return adapted @ np.asarray(xyz, dtype=np.float64)


def _icc_tag_directory(payload: bytes) -> dict[bytes, tuple[int, int]]:
    count = int.from_bytes(payload[128:132], "big")
    tags: dict[bytes, tuple[int, int]] = {}
    offset = 132
    for _ in range(count):
        signature = bytes(payload[offset : offset + 4])
        loc = int.from_bytes(payload[offset + 4 : offset + 8], "big")
        size = int.from_bytes(payload[offset + 8 : offset + 12], "big")
        tags[signature] = (loc, size)
        offset += 12
    return tags


def _display_p3_icc_bytes() -> bytes:
    """Build a LittleCMS-readable Display P3 profile from the built-in sRGB ICC.

    Homemade v2 headers are easy to get wrong; lcms then returns a collapsed
    transform. Start from Pillow's valid sRGB profile, rename it so the loader
    does not treat it as working sRGB, and replace the D50-adapted primaries.
    """
    from PIL import ImageCms

    payload = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    old_name = "sRGB built-in".encode("utf-16-be")
    new_name = "DisplayP3Test".encode("utf-16-be")
    if old_name not in payload or len(old_name) != len(new_name):
        raise RuntimeError("Unexpected built-in sRGB ICC description layout.")
    payload = bytearray(bytes(payload).replace(old_name, new_name, 1))

    red_xy = (0.680, 0.320)
    green_xy = (0.265, 0.690)
    blue_xy = (0.150, 0.060)
    white_xy = _xy_to_xyz(0.3127, 0.3290)
    matrix = np.column_stack(
        [
            np.asarray(_xy_to_xyz(*red_xy)),
            np.asarray(_xy_to_xyz(*green_xy)),
            np.asarray(_xy_to_xyz(*blue_xy)),
        ]
    )
    scale = np.linalg.solve(matrix, np.asarray(white_xy))
    matrix = matrix * scale
    primaries = {
        b"rXYZ": _bradford_adapt_d65_to_d50(matrix[:, 0]),
        b"gXYZ": _bradford_adapt_d65_to_d50(matrix[:, 1]),
        b"bXYZ": _bradford_adapt_d65_to_d50(matrix[:, 2]),
    }
    tags = _icc_tag_directory(payload)
    for signature, xyz in primaries.items():
        loc, size = tags[signature]
        xyz_payload = b"XYZ " + (0).to_bytes(4, "big") + b"".join(_s15fixed16(float(value)) for value in xyz)
        if size < len(xyz_payload):
            raise RuntimeError(f"Built-in sRGB ICC {signature!r} tag is too small to patch.")
        payload[loc : loc + len(xyz_payload)] = xyz_payload
    if b"chrm" in tags:
        loc, size = tags[b"chrm"]
        chrm = (
            b"chrm"
            + (0).to_bytes(4, "big")
            + (3).to_bytes(2, "big")
            + (0).to_bytes(2, "big")
            + b"".join(
                _u16fixed16(float(value))
                for value in (*red_xy, *green_xy, *blue_xy)
            )
        )
        if size < len(chrm):
            raise RuntimeError("Built-in sRGB ICC chrm tag is too small to patch.")
        payload[loc : loc + len(chrm)] = chrm
    return bytes(payload)


def test_icc_float_lut_identity_uses_actual_integer_nodes():
    from PIL import ImageCms
    from film_foundry.core.io_utils import _apply_icc_float_tiled

    rgb = np.repeat(np.linspace(0, 1, 4097, dtype=np.float32)[None, :, None], 3, axis=2)
    converted = _apply_icc_float_tiled(rgb, ImageCms.createProfile("sRGB"))
    np.testing.assert_allclose(converted, rgb, atol=1e-7, rtol=0)


def test_icc_srgb_description_does_not_override_different_primaries():
    from film_foundry.core.io_utils import _open_rgb_icc_profile, _profile_is_srgb

    payload = _display_p3_icc_bytes().replace(
        "DisplayP3Test".encode("utf-16-be"), "sRGB built-in".encode("utf-16-be"),
    )
    profile, _ = _open_rgb_icc_profile(payload)
    assert not _profile_is_srgb(profile)


def test_untagged_png_is_assumed_srgb_working_space(tmp_path):
    path = tmp_path / "plain.png"
    pixels = np.array([[[20, 40, 60], [200, 10, 30]]], dtype=np.uint8)
    Image.fromarray(pixels, mode="RGB").save(path)

    image, record = load_image_with_color(path)

    assert record["source"] == "assumed_srgb"
    assert record["working_space"] == "srgb_iec61966_2_1"
    assert record["policy"] == "apply"
    assert np.allclose(image, pixels.astype(np.float32) / 255.0)


def test_embedded_srgb_icc_does_not_change_pixels(tmp_path):
    from PIL import ImageCms

    path = tmp_path / "srgb.png"
    pixels = np.array([[[20, 40, 60], [200, 10, 30]]], dtype=np.uint8)
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.fromarray(pixels, mode="RGB").save(path, icc_profile=profile)

    image, record = load_image_with_color(path)

    assert record["source"] == "embedded_srgb"
    assert np.allclose(image, pixels.astype(np.float32) / 255.0)


def test_display_p3_icc_converts_into_working_srgb_and_ignore_keeps_encoded_values(tmp_path):
    path = tmp_path / "p3.png"
    pixels = np.zeros((2, 3, 3), dtype=np.uint8)
    pixels[0, 1] = (0, 255, 0)
    pixels[1, 0] = (80, 90, 70)
    Image.fromarray(pixels, mode="RGB").save(path, icc_profile=_display_p3_icc_bytes())

    converted, applied = load_image_with_color(path, input_icc_policy="apply")
    assumed, ignored = load_image_with_color(path, input_icc_policy="ignore")

    assert applied["source"] == "converted_to_srgb"
    assert "p3" in str(applied.get("profile_description", "")).lower()
    assert ignored["source"] == "icc_ignored"
    assert ignored["reason"] == "policy_ignore"
    assert np.allclose(assumed[1, 0], np.array((80, 90, 70), dtype=np.float32) / 255.0)
    assert float(np.max(converted[0, 0])) < 0.05
    assert not np.allclose(converted[1, 0], assumed[1, 0], atol=1e-4)
    assert not np.allclose(converted, assumed)


def test_damaged_icc_is_ignored_and_assumed_srgb(tmp_path):
    path = tmp_path / "broken.png"
    pixels = np.array([[[90, 100, 110]]], dtype=np.uint8)
    Image.fromarray(pixels, mode="RGB").save(path, icc_profile=b"not-an-icc-profile")

    image, record = load_image_with_color(path)

    assert record["source"] == "icc_ignored"
    assert record["reason"] == "unreadable_profile"
    assert np.allclose(image, pixels.astype(np.float32) / 255.0)


def test_16bit_wide_gamut_uses_native_lcms_float_precision(monkeypatch):
    import film_foundry.core.io_utils as io_utils_module
    from film_foundry.core.io_utils import _convert_rgb_to_working_srgb

    def reject_approximation(*_args, **_kwargs):
        raise AssertionError("high-bit-depth profiles must not use the RGB8 3-D LUT")

    monkeypatch.setattr(io_utils_module, "_apply_icc_float_tiled", reject_approximation)

    icc = _display_p3_icc_bytes()
    first = np.zeros((1, 1, 3), dtype=np.uint16)
    second = np.zeros((1, 1, 3), dtype=np.uint16)
    # Near-neutral Display P3 samples stay inside sRGB. Pure P3 green clips,
    # so 16-bit steps would collapse after the destination gamut limit.
    first[0, 0] = (20000, 20500, 19800)
    second[0, 0] = (20050, 20550, 19850)
    converted_a, record_a = _convert_rgb_to_working_srgb(first, icc, policy="apply")
    converted_b, _record_b = _convert_rgb_to_working_srgb(second, icc, policy="apply")

    assert record_a["bit_depth"] == 16
    assert record_a["source"] == "converted_to_srgb"
    assert record_a["transform"] == "lcms_float32"
    assert not np.array_equal(converted_a, converted_b)
    assert 0.0 < float(np.max(np.abs(converted_b - converted_a))) < (1.0 / 255.0)
    quantized = np.round(first.astype(np.float64) * (255.0 / 65535.0)).astype(np.uint8)
    assert np.array_equal(
        quantized,
        np.round(second.astype(np.float64) * (255.0 / 65535.0)).astype(np.uint8),
    )


def test_process_file_records_assumed_srgb_input_color(tmp_path):
    input_path = tmp_path / "plain.png"
    output_path = tmp_path / "out.png"
    Image.fromarray(np.full((6, 8, 3), 140, dtype=np.uint8), mode="RGB").save(input_path)
    config = DarkroomConfig()
    config.random_seed = 2
    config.seed_strategy = "fixed"
    config.enable_grain = False
    config.enable_halation = False

    process_file(input_path, output_path, config)

    negative_path = output_path.parent / "negatives" / f"{input_path.stem}.darkroom_negative.npz"
    with np.load(negative_path) as packed:
        medium_metadata = json.loads(str(packed["developed_medium_metadata"]))
    record = medium_metadata["input_color"]
    assert record["source"] == "assumed_srgb"
    assert record["working_space"] == "srgb_iec61966_2_1"

    develop_sidecar = json.loads(
        (negative_path.with_suffix(negative_path.suffix + ".json")).read_text(encoding="utf-8")
    )
    assert develop_sidecar["developed_medium"]["input_color"]["source"] == "assumed_srgb"


def test_old_session_defaults_to_applying_embedded_icc():
    restored = DarkroomConfig.from_dict({"fast_mode": False})
    ignored = DarkroomConfig.from_dict({"input_icc_policy": "ignore"})

    assert restored.input_icc_policy == "apply"
    assert ignored.input_icc_policy == "ignore"


def test_scanner_raw_sidecar_restores_immutable_material_optics(tmp_path):
    raw_path = tmp_path / "frame.scanner_raw.tiff"
    sidecar_path = raw_path.with_suffix(raw_path.suffix + ".json")
    absorption = [[1.08, 0.18, 0.08], [0.12, 0.98, 0.16], [0.05, 0.22, 0.94]]
    sidecar_path.write_text(json.dumps({
        "developed_medium": {
            "film_process_model": {
                "optical_observation": {
                    "dye_absorption_matrix": absorption,
                    "base_dye_interaction_strength": 0.73,
                    "base_dye_interaction_matrix": [[0.8, 0.15, 0.05], [0.1, 0.8, 0.1], [0.04, 0.16, 0.8]],
                    "base_density_rgb": [0.2, 0.6, 0.9],
                    "density_min": [0.11, 0.12, 0.13],
                    "density_max": [1.8, 1.9, 1.7],
                    "color_process": "color",
                }
            }
        }
    }), encoding="utf-8")
    config = DarkroomConfig()

    observation = load_scanner_raw_optical_observation(raw_path)
    restored = apply_optical_observation_snapshot(config, observation)

    assert restored
    assert np.allclose(config.film.dye_absorption_matrix, absorption)
    assert config.film.base_dye_interaction_strength == 0.73
    assert np.allclose(
        config.film.base_dye_interaction_matrix,
        [[0.8, 0.15, 0.05], [0.1, 0.8, 0.1], [0.04, 0.16, 0.8]],
    )
    assert config.film.film_base_density_rgb == (0.2, 0.6, 0.9)


def test_present_but_corrupt_scanner_raw_sidecar_fails_explicitly(tmp_path):
    raw_path = tmp_path / "frame.scanner_raw.tiff"
    raw_path.with_suffix(raw_path.suffix + ".json").write_text(
        '{"kind":"ScannerRawNegative","kind":"duplicate"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid scanner raw sidecar"):
        load_scanner_raw_sidecar(raw_path)


def test_final_scan_sidecar_records_effective_runtime_not_unapplied_request(tmp_path):
    develop_config = DarkroomConfig()
    develop_config.film.film_base_density_rgb = (0.22, 0.48, 0.81)
    medium = develop_negative(np.full((4, 5, 3), 0.4, dtype=np.float32), develop_config)
    requested_scan = DarkroomConfig()
    requested_scan.film.film_base_density_rgb = (2.0, 2.0, 2.0)
    requested_scan.scanner.scan_saturation = 1.23
    scanned = engine.scan_negative(medium, requested_scan)

    payload = final_positive_sidecar(
        output_path=tmp_path / "scan.png",
        negative_path=tmp_path / "master.darkroom_negative.npz",
        config=requested_scan,
        scanned=scanned,
    )

    runtime = scanned.metadata["runtime_config"]
    assert payload["config"]["film"]["film_base_density_rgb"] == runtime.film.film_base_density_rgb
    assert payload["config"]["film"]["film_base_density_rgb"] != (2.0, 2.0, 2.0)
    assert payload["config"]["scanner"]["scan_saturation"] == 1.23


def test_scanner_raw_sidecar_border_contract_overrides_current_gui_default(tmp_path):
    raw_path = tmp_path / "frame.scanner_raw.tiff"
    # Legacy sidecars stored 10% relative to the original 100x120 image.  The
    # exported outer raw is therefore 120x140 and must round-trip exactly.
    raw = np.full((120, 140, 3), 0.4, dtype=np.float32)
    save_linear_rgb_tiff(raw, raw_path)
    raw_path.with_suffix(raw_path.suffix + ".json").write_text(json.dumps({
        "kind": "ScannerRawNegative",
        "border": {"percent": 0.10, "min_px": 1},
        "developed_medium": {"film_process_model": {"optical_observation": {
            "dye_absorption_matrix": [[0.9, 0.2, 0.1], [0.1, 1.1, 0.2], [0.0, 0.1, 1.2]],
        }}},
    }), encoding="utf-8")
    config = DarkroomConfig()
    original_matrix = config.film.dye_absorption_matrix
    config.scanner.interpretation_mode = "negative"
    config.output.scanner_raw_border_percent = 0.04
    config.output.scanner_raw_border_min_px = 1

    scanned, source, _preview = gui.scan_from_file(raw_path, config)
    sidecar = load_scanner_raw_sidecar(raw_path)

    assert scanner_raw_border_contract(sidecar) == (0.10, 1)
    assert scanner_raw_border_width_from_sidecar(sidecar, raw.shape) == 10
    assert source == "scanner_raw_tiff"
    assert scanned.metadata["scan_source"] == "scanner_raw_tiff"
    assert scanned.scanner_raw.shape[:2] == (100, 120)
    assert config.film.dye_absorption_matrix == original_matrix

    config.scanner.interpretation_mode = "positive"
    positive_interpretation, positive_source, _preview = gui.scan_from_file(raw_path, config)
    assert positive_source == "scanner_raw_tiff"
    assert positive_interpretation.metadata["scan_source"] == "scanner_raw_tiff"
    assert positive_interpretation.scanner_raw.shape[:2] == (100, 120)


def test_gui_recognizes_light_table_raw_without_inventing_negative_border(tmp_path):
    raw_path = tmp_path / "frame.light_table_raw.tiff"
    raw = np.linspace(0.1, 0.9, 100 * 120 * 3, dtype=np.float32).reshape(100, 120, 3)
    save_linear_rgb_tiff(raw, raw_path)
    config = DarkroomConfig()
    config.scanner.interpretation_mode = "negative"
    config.output.scanner_raw_border_percent = 0.10
    config.output.scanner_raw_border_min_px = 1

    scanned, source, _preview = gui.scan_from_file(raw_path, config)

    assert gui.is_scanner_raw_tiff(raw_path)
    assert gui.iter_developed_medium_files(raw_path) == [raw_path]
    assert source == "light_table_raw_tiff"
    assert scanned.metadata["scan_source"] == "light_table_raw_tiff"
    assert scanned.metadata["source_path"] == str(raw_path)
    assert scanned.scanner_raw.shape[:2] == raw.shape[:2]


def test_tiny_negative_export_honors_minimum_border_when_percent_is_enabled():
    assert scanner_raw_export_border_width((1, 1, 3), 0.04, 32) == 32
    assert scanner_raw_export_border_width((1, 1, 3), 0.0, 32) == 0


def test_alpha_array_is_composited_over_white():
    rgba = np.zeros((1, 2, 4), dtype=np.uint8)
    rgba[0, 0] = (0, 0, 0, 0)
    rgba[0, 1] = (0, 0, 0, 255)

    rgb = ensure_rgb_float(rgba)

    assert np.allclose(rgb[0, 0], 1.0)
    assert np.allclose(rgb[0, 1], 0.0)


def test_hyperspectral_like_array_is_rejected():
    image = np.zeros((2, 2, 8), dtype=np.float32)

    with pytest.raises(ValueError, match="Unsupported channel count"):
        ensure_rgb_float(image)


def test_image_and_raw_conversion_reject_non_finite_arrays_at_boundary():
    image = np.zeros((2, 3, 3), dtype=np.float32)
    image[0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN or infinite"):
        ensure_rgb_float(image)
    with pytest.raises(ValueError, match="NaN or infinite"):
        split_scanner_raw_border(image)


def test_scanner_border_helpers_reject_non_finite_geometry():
    with pytest.raises(ValueError, match="finite"):
        scanner_raw_export_border_width((5, 6, 3), float("nan"), 1)
    with pytest.raises(ValueError, match="finite"):
        split_scanner_raw_border(np.zeros((5, 6, 3), dtype=np.float32), border_percent=float("inf"))


def test_save_image_rejects_non_finite_output_and_invalid_bit_depth(tmp_path):
    image = np.full((4, 5, 3), 0.5, dtype=np.float32)
    invalid = image.copy()
    invalid[0, 0, 0] = np.nan
    output = DarkroomConfig().output

    with pytest.raises(ValueError, match="NaN or infinite"):
        save_image(invalid, tmp_path / "nan.png", output)

    output.bit_depth = 32
    with pytest.raises(ValueError, match="bit depth"):
        save_image(image, tmp_path / "invalid_depth.png", output)


def test_image_sidecar_bundle_restores_previous_generation_on_sidecar_failure(tmp_path, monkeypatch):
    image_path = tmp_path / "scan.png"
    sidecar_path = tmp_path / "scan.png.json"
    image_path.write_bytes(b"old-image")
    sidecar_path.write_text("old-sidecar", encoding="utf-8")
    output = DarkroomConfig().output

    def fail_sidecar(*_args, **_kwargs):
        raise RuntimeError("simulated sidecar failure")

    import film_foundry.core.io_utils as io_utils

    monkeypatch.setattr(io_utils, "atomic_write_json", fail_sidecar)
    with pytest.raises(RuntimeError, match="simulated sidecar failure"):
        save_image_bundle(
            np.full((4, 5, 3), 0.5, dtype=np.float32),
            image_path,
            output,
            {"kind": "ScannedPositive"},
        )

    assert image_path.read_bytes() == b"old-image"
    assert sidecar_path.read_text(encoding="utf-8") == "old-sidecar"


def test_image_bundle_refuses_to_overwrite_scanned_source(tmp_path):
    source = tmp_path / "scanner_raw.tiff"
    source.write_bytes(b"source-raw")

    with pytest.raises(ValueError, match="must not overwrite"):
        save_image_bundle(
            np.full((4, 5, 3), 0.5, dtype=np.float32),
            source,
            DarkroomConfig().output,
            protected_paths=(source,),
        )

    assert source.read_bytes() == b"source-raw"


def test_batch_output_stems_reject_case_insensitive_source_collisions():
    with pytest.raises(ValueError, match="same output stem"):
        assert_unique_output_stems(
            [Path("frame.jpg"), Path("FRAME.png")],
            "Full workflow",
        )

    assert_unique_output_stems(
        [Path("frame.darkroom_negative.npz"), Path("frame.darkroom_positive.npz")],
        "Scan workflow",
    )


def test_atomic_output_preserves_existing_file_when_writer_fails(tmp_path):
    path = tmp_path / "result.json"
    path.write_text("old-valid-content", encoding="utf-8")

    with pytest.raises(RuntimeError, match="simulated"):
        with atomic_output_path(path) as temporary:
            temporary.write_text("partial-new-content", encoding="utf-8")
            raise RuntimeError("simulated failure")

    assert path.read_text(encoding="utf-8") == "old-valid-content"
    assert not list(tmp_path.glob(".ff-*"))


def test_atomic_directory_preserves_previous_bundle_when_build_fails(tmp_path):
    target = tmp_path / "layer_pack"
    target.mkdir()
    (target / "sidecar.json").write_text("old-valid-manifest", encoding="utf-8")

    with pytest.raises(RuntimeError, match="simulated"):
        with atomic_output_directory(target) as staging:
            (staging / "sidecar.json").write_text("incomplete-new-manifest", encoding="utf-8")
            raise RuntimeError("simulated directory failure")

    assert (target / "sidecar.json").read_text(encoding="utf-8") == "old-valid-manifest"
    assert not list(tmp_path.glob(".ff-stage-*"))
    assert not list(tmp_path.glob(".ff-backup-*"))


def test_atomic_directory_replaces_previous_bundle_only_after_success(tmp_path):
    target = tmp_path / "layer_pack"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")

    with atomic_output_directory(target) as staging:
        (staging / "new.txt").write_text("new", encoding="utf-8")
        assert (target / "old.txt").exists()

    assert not (target / "old.txt").exists()
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob(".ff-stage-*"))
    assert not list(tmp_path.glob(".ff-backup-*"))


def test_atomic_directory_restores_previous_bundle_if_commit_move_fails(tmp_path, monkeypatch):
    target = tmp_path / "layer_pack"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    real_replace = atomic_io.os.replace

    def fail_staging_commit(source, destination):
        source = Path(source)
        destination = Path(destination)
        if source.name.startswith(".ff-stage-") and destination == target:
            raise OSError("simulated commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_io.os, "replace", fail_staging_commit)
    with pytest.raises(OSError, match="simulated commit"):
        with atomic_output_directory(target) as staging:
            (staging / "new.txt").write_text("new", encoding="utf-8")

    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (target / "new.txt").exists()
    assert not list(tmp_path.glob(".ff-stage-*"))
    assert not list(tmp_path.glob(".ff-backup-*"))


def test_atomic_path_set_restores_all_previous_outputs_after_failure(tmp_path):
    master = tmp_path / "frame.npz"
    attachments = tmp_path / "frame_layers"
    master.write_bytes(b"old-master")
    attachments.mkdir()
    (attachments / "manifest.json").write_text("old-manifest", encoding="utf-8")
    new_sidecar = tmp_path / "frame.npz.json"

    with pytest.raises(RuntimeError, match="simulated set failure"):
        with atomic_path_set((master, attachments, new_sidecar)):
            master.write_bytes(b"new-master")
            attachments.mkdir()
            (attachments / "manifest.json").write_text("new-manifest", encoding="utf-8")
            new_sidecar.write_text("incomplete", encoding="utf-8")
            raise RuntimeError("simulated set failure")

    assert master.read_bytes() == b"old-master"
    assert (attachments / "manifest.json").read_text(encoding="utf-8") == "old-manifest"
    assert not new_sidecar.exists()
    assert not list(tmp_path.glob(".ff-backup-*"))


def test_atomic_path_set_rejects_duplicates_and_nested_targets(tmp_path):
    target = tmp_path / "bundle"
    with pytest.raises(ValueError, match="duplicate"):
        with atomic_path_set((target, target)):
            pass
    with pytest.raises(ValueError, match="nested"):
        with atomic_path_set((target, target / "manifest.json")):
            pass


def test_non_finite_scanner_raw_export_preserves_existing_file(tmp_path):
    path = tmp_path / "raw.tiff"
    path.write_bytes(b"previous-valid-file")
    invalid = np.zeros((3, 4, 3), dtype=np.float32)
    invalid[0, 0, 0] = np.nan

    with pytest.raises(ValueError, match="NaN or infinite"):
        save_linear_rgb_tiff(invalid, path)

    assert path.read_bytes() == b"previous-valid-file"


def test_invalid_developed_medium_is_rejected_before_any_master_is_written(tmp_path):
    config = DarkroomConfig()
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)
    medium.after_halation = medium.after_halation[:-1]
    output = tmp_path / "invalid.darkroom_negative.npz"

    with pytest.raises(ValueError, match="share one"):
        save_developed_medium_at_path(tmp_path / "input.png", output, medium, config)

    assert not output.exists()


def test_invalid_output_config_is_rejected_before_any_master_is_written(tmp_path):
    config = DarkroomConfig()
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)
    config.output.quality = float("nan")
    output = tmp_path / "invalid.darkroom_negative.npz"

    with pytest.raises(ValueError, match="Non-finite output"):
        save_developed_medium_at_path(tmp_path / "input.png", output, medium, config)

    assert not output.exists()


def test_non_finite_medium_metadata_is_rejected_before_npz_write(tmp_path):
    config = DarkroomConfig()
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)
    medium.metadata["film_process_model"]["corrupt_value"] = float("nan")
    output = tmp_path / "invalid_metadata.darkroom_negative.npz"

    with pytest.raises(ValueError, match="Out of range"):
        save_developed_medium_at_path(tmp_path / "input.png", output, medium, config)

    assert not output.exists()


def test_npz_only_negative_export_does_not_depend_on_scanner_validity(tmp_path):
    config = DarkroomConfig()
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    config.output.watermark_negative_material = False
    config.save_sidecar = False
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)
    config.scanner.scan_saturation = float("nan")
    output = tmp_path / "valid.darkroom_negative.npz"

    paths = save_developed_medium_at_path(tmp_path / "input.png", output, medium, config)

    assert output.exists()
    assert paths["negative_path"] == str(output)


def test_transparent_exports_share_one_full_frame_dye_transform(tmp_path, monkeypatch):
    config = DarkroomConfig()
    density = np.linspace(0.1, 1.0, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    expected = medium_transmission_rgb(density, config.film)
    original = electronic_negative._dye_density_rgb
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(electronic_negative, "_dye_density_rgb", counted)
    paths = export_transparent_plate_set(density, config.film, tmp_path / "transparent")
    exported = load_linear_rgb_tiff(paths["negative_transmission_16bit_tiff"])

    assert calls == 1
    assert np.allclose(exported, expected, atol=1.0 / 65535.0)


def test_transmission_export_uses_authoritative_rgb_optical_master(tmp_path):
    config = DarkroomConfig()
    density = np.zeros((3, 4, 3), dtype=np.float32)
    optical = np.full((3, 4, 3), (0.2, 0.5, 0.9), dtype=np.float32)
    paths = export_transparent_plate_set(
        density,
        config.film,
        tmp_path / "authoritative_transparent",
        optical_density_rgb=optical,
        clear_base_optical_density_rgb=(0.1, 0.2, 0.3),
    )

    exported = load_linear_rgb_tiff(paths["negative_transmission_16bit_tiff"])

    assert np.allclose(exported, np.power(10.0, -optical), atol=1.0 / 65535.0)


def test_positive_preview_and_light_table_raw_share_one_capture(tmp_path, monkeypatch):
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.output.save_scanner_raw = True
    config.output.export_transparent_plate = False
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)
    original = engine.capture_optical_density
    calls = 0
    shapes = []

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        shapes.append(np.asarray(args[0]).shape)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "capture_optical_density", counted)
    save_developed_medium_at_path(
        tmp_path / "input.png",
        tmp_path / "positive.darkroom_positive.npz",
        medium,
        config,
    )

    # The image capture is reused for preview and raw export. The second call is
    # only the 1x1 clear-base reference used to construct the observation border.
    assert calls == 2
    assert shapes.count((5, 6, 3)) == 1
    assert shapes.count((1, 1, 3)) == 1


def test_developed_medium_bundle_rolls_back_as_one_generation(tmp_path, monkeypatch):
    config = DarkroomConfig()
    config.output.export_transparent_plate = True
    config.output.export_plate_set = False
    config.output.export_layer_pack = False
    config.output.save_scanner_raw = True
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)
    output = tmp_path / "frame.darkroom_negative.npz"
    preview = output.with_suffix(".negative_visual.png")
    transparent = tmp_path / "frame.darkroom_negative_transparent_plate"
    output.write_bytes(b"old-master")
    preview.write_bytes(b"old-preview")
    transparent.mkdir()
    (transparent / "old.txt").write_text("old-materials", encoding="utf-8")

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("simulated material export failure")

    monkeypatch.setattr(engine, "export_transparent_plate_set", fail_export)
    with pytest.raises(RuntimeError, match="simulated material export failure"):
        save_developed_medium_at_path(tmp_path / "input.png", output, medium, config)

    assert output.read_bytes() == b"old-master"
    assert preview.read_bytes() == b"old-preview"
    assert (transparent / "old.txt").read_text(encoding="utf-8") == "old-materials"
    assert not output.with_suffix(".scanner_raw.tiff").exists()
    assert not output.with_suffix(output.suffix + ".json").exists()
    assert not list(tmp_path.glob(".ff-backup-*"))


def test_new_medium_generation_removes_disabled_stale_products(tmp_path):
    image = np.full((5, 6, 3), 0.4, dtype=np.float32)
    enabled = DarkroomConfig()
    enabled.output.save_scanner_raw = True
    enabled.output.export_transparent_plate = True
    enabled.output.export_plate_set = True
    enabled.save_sidecar = True
    medium = develop_negative(image, enabled)
    output = tmp_path / "frame.darkroom_negative.npz"
    save_developed_medium_at_path(tmp_path / "input.png", output, medium, enabled)
    raw = output.with_suffix(".scanner_raw.tiff")
    transparent = tmp_path / "frame.darkroom_negative_transparent_plate"
    plates = tmp_path / "frame.darkroom_negative_plate_set"
    assert raw.exists() and transparent.exists() and plates.exists()

    compact = DarkroomConfig()
    compact.output.save_scanner_raw = False
    compact.output.export_transparent_plate = False
    compact.output.export_plate_set = False
    compact.save_sidecar = False
    compact_medium = develop_negative(image, compact)
    save_developed_medium_at_path(tmp_path / "input.png", output, compact_medium, compact)

    assert output.exists()
    assert output.with_suffix(".negative_visual.png").exists()
    assert not raw.exists()
    assert not raw.with_suffix(raw.suffix + ".json").exists()
    assert not output.with_suffix(output.suffix + ".json").exists()
    assert not transparent.exists()
    assert not plates.exists()


def test_medium_export_uses_immutable_material_optics_not_caller_film(tmp_path):
    image = np.linspace(0.05, 0.95, 5 * 7 * 3, dtype=np.float32).reshape(5, 7, 3)
    develop_config = DarkroomConfig()
    develop_config.output.save_scanner_raw = False
    develop_config.output.export_transparent_plate = True
    medium = develop_negative(image, develop_config)

    neutral = DarkroomConfig()
    neutral.output.save_scanner_raw = False
    neutral.output.export_transparent_plate = True
    hostile = DarkroomConfig()
    hostile.output.save_scanner_raw = False
    hostile.output.export_transparent_plate = True
    hostile.film.film_base_density_rgb = (2.0, 0.0, 0.0)
    hostile.film.dye_absorption_matrix = (
        (0.0, 0.0, 2.5),
        (2.5, 0.0, 0.0),
        (0.0, 2.5, 0.0),
    )

    first = tmp_path / "first.darkroom_negative.npz"
    second = tmp_path / "second.darkroom_negative.npz"
    first_paths = save_developed_medium_at_path(tmp_path / "input.png", first, medium, neutral)
    second_paths = save_developed_medium_at_path(tmp_path / "input.png", second, medium, hostile)
    first_transmission = load_linear_rgb_tiff(
        first_paths["material:negative_transmission_16bit_tiff"]
    )
    second_transmission = load_linear_rgb_tiff(
        second_paths["material:negative_transmission_16bit_tiff"]
    )

    assert np.array_equal(first_transmission, second_transmission)


def test_developed_sidecar_records_actual_varied_process_runtime(tmp_path):
    image = np.full((5, 7, 3), 0.4, dtype=np.float32)
    requested = DarkroomConfig()
    requested.seed_strategy = "fixed"
    requested.random_seed = 19
    requested.chemistry.process_variation = 1.0
    requested.output.save_scanner_raw = False
    requested.output.export_transparent_plate = False
    medium = develop_negative(image, requested)
    actual_runtime = medium.metadata["runtime_config"]
    output = tmp_path / "varied.darkroom_negative.npz"

    save_developed_medium_at_path(tmp_path / "input.png", output, medium, requested)
    sidecar = atomic_io.strict_json_load(output.with_suffix(output.suffix + ".json"))

    assert sidecar["config"]["chemistry"]["time_min"] == actual_runtime.chemistry.time_min
    assert sidecar["config"]["chemistry"]["time_min"] != requested.chemistry.time_min


def test_full_file_process_rolls_back_final_and_material_outputs_together(tmp_path, monkeypatch):
    input_path = tmp_path / "input.png"
    output_path = tmp_path / "result.png"
    Image.fromarray(np.full((8, 10, 3), 128, dtype=np.uint8)).save(input_path)
    output_path.write_bytes(b"old-final")
    negative_path = tmp_path / "negatives" / "input.darkroom_negative.npz"
    negative_path.parent.mkdir()
    negative_path.write_bytes(b"old-master")
    config = DarkroomConfig()
    config.output.export_transparent_plate = True

    def fail_export(*_args, **_kwargs):
        raise RuntimeError("simulated full export failure")

    monkeypatch.setattr(engine, "export_transparent_plate_set", fail_export)
    with pytest.raises(RuntimeError, match="simulated full export failure"):
        process_file(input_path, output_path, config)

    assert output_path.read_bytes() == b"old-final"
    assert negative_path.read_bytes() == b"old-master"
    assert not list(tmp_path.rglob(".ff-backup-*"))


def test_load_16bit_tiff_preserves_range(tmp_path):
    path = tmp_path / "input.tiff"
    rgb = np.zeros((2, 2, 3), dtype=np.uint16)
    rgb[..., 0] = 65535
    rgb[..., 1] = 32768
    bgr = rgb[..., ::-1]
    assert cv2.imwrite(str(path), bgr)

    image = load_image(path)

    assert image.shape == (2, 2, 3)
    assert np.isclose(image[..., 0].max(), 1.0)
    assert np.isclose(image[..., 1].max(), 32768 / 65535, atol=1e-4)


def test_raw_dng_input_is_rejected_with_clear_message(tmp_path):
    path = tmp_path / "input.dng"
    path.write_bytes(b"not really a dng")

    with pytest.raises(ValueError, match="DNG only"):
        load_image(path)


def test_iter_images_ignores_unsupported_single_file(tmp_path):
    path = tmp_path / "input.dng"
    path.write_bytes(b"not really a dng")

    assert iter_images(path) == []


def test_iter_images_returns_empty_for_missing_input_directory(tmp_path):
    assert iter_images(tmp_path / "does_not_exist") == []


def test_full_process_refuses_to_overwrite_source_image(tmp_path):
    path = tmp_path / "source.png"
    Image.fromarray(np.full((4, 5, 3), 128, dtype=np.uint8), mode="RGB").save(path)
    original = path.read_bytes()

    with pytest.raises(ValueError, match="must not overwrite"):
        process_file(path, path, DarkroomConfig())

    assert path.read_bytes() == original


def test_developed_medium_export_refuses_to_overwrite_source_path(tmp_path):
    source = tmp_path / "source.npz"
    source.write_bytes(b"original-source")
    config = DarkroomConfig()
    medium = develop_negative(np.full((4, 5, 3), 0.4, dtype=np.float32), config)

    with pytest.raises(ValueError, match="must not overwrite"):
        save_developed_medium_at_path(source, source, medium, config)

    assert source.read_bytes() == b"original-source"


def test_process_file_with_chinese_paths_exports_negative_materials(tmp_path):
    input_path = tmp_path / "中文输入.png"
    output_path = tmp_path / "中文输出.png"
    image = np.full((12, 12, 3), 160, dtype=np.uint8)
    image[3:7, 4:9] = (235, 230, 220)
    Image.fromarray(image, mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.random_seed = 1
    config.seed_strategy = "fixed"
    config.output.format = "png"
    config.output.save_scanner_raw = True
    config.output.scanner_raw_border_percent = 0.10
    config.output.export_transparent_plate = True
    config.output.export_plate_set = True

    result_path = process_file(input_path, output_path, config)

    negative_dir = output_path.parent / "negatives"
    negative_path = negative_dir / f"{input_path.stem}.darkroom_negative.npz"
    material_base = negative_path.with_suffix("")
    transparent_dir = material_base.parent / f"{material_base.name}_transparent_plate"
    plate_dir = material_base.parent / f"{material_base.name}_plate_set"
    assert result_path.exists()
    assert Image.open(result_path).info["Software"] == PROJECT_NAME
    assert negative_path.exists()
    with np.load(negative_path) as negative_data:
        assert "film_foundry_provenance" in negative_data.files
        assert "developed_medium_metadata" in negative_data.files
        provenance = json.loads(str(negative_data["film_foundry_provenance"]))
        medium_metadata = json.loads(str(negative_data["developed_medium_metadata"]))
    assert provenance["kind"] == "FilmFoundryProvenance"
    assert provenance["project"] == PROJECT_NAME
    from film_foundry import __version__
    assert provenance["project_version"] == __version__
    assert medium_metadata["medium_process"] == "negative"
    assert medium_metadata["image_polarity"] == "negative"
    assert medium_metadata["compatible_interpreters"] == ["negative_scan"]
    assert negative_path.with_suffix(".negative_visual.png").exists()
    assert negative_path.with_suffix(".scanner_raw.tiff").exists()
    scanner_raw_path = negative_path.with_suffix(".scanner_raw.tiff")
    raw_sidecar = load_scanner_raw_sidecar(scanner_raw_path)
    assert raw_sidecar["border"]["width_px"] == 32
    rescanned, source_kind, _preview = gui.scan_from_file(scanner_raw_path, config)
    assert source_kind == "scanner_raw_tiff"
    assert rescanned.scanner_raw.shape[:2] == (12, 12)
    with negative_path.with_suffix(negative_path.suffix + ".json").open("r", encoding="utf-8") as handle:
        sidecar = json.load(handle)
    assert sidecar["provenance"]["id"] == provenance["id"]
    assert sidecar["developed_medium"]["view_mode"] == "transmissive"
    assert transparent_dir.joinpath("negative_transparent_16bit.tiff").exists()
    assert plate_dir.joinpath("halation_layer_linear.png").exists()


def test_developed_medium_bundle_supports_deep_unicode_output_path(tmp_path):
    output_dir = (
        tmp_path
        / ("深层中文目录" * 3)
        / ("介质输出" * 4)
        / ("事务路径" * 4)
    )
    output = output_dir / "测试胶片.darkroom_negative.npz"
    config = DarkroomConfig()
    config.random_seed = 23
    config.seed_strategy = "fixed"
    config.output.save_scanner_raw = True
    config.output.scanner_raw_border_min_px = 1
    config.output.export_transparent_plate = True
    config.output.export_plate_set = True
    medium = develop_negative(np.full((6, 8, 3), 0.4, dtype=np.float32), config)

    paths = save_developed_medium_at_path(tmp_path / "来源.png", output, medium, config)

    assert output.exists()
    assert Path(paths["scanner_raw_path"]).exists()
    assert Path(paths["material:negative_transmission_16bit_tiff"]).exists()
    assert Path(paths["material:halation_layer_linear"]).exists()
    assert not list(output_dir.glob(".ff-*"))


def test_process_file_exports_positive_transparency_materials(tmp_path):
    input_path = tmp_path / "slide_input.png"
    output_path = tmp_path / "slide_output.png"
    gradient = np.linspace(48, 230, 14 * 16 * 3, dtype=np.uint8).reshape(14, 16, 3)
    Image.fromarray(gradient, mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.random_seed = 3
    config.seed_strategy = "fixed"
    config.film.medium_process = "slide"
    config.film.image_polarity = "positive"
    config.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    config.chemistry.medium_process = "slide"
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.target_medium_process = "slide"
    config.scanner.input_polarity = "positive"
    config.scanner.output_polarity = "positive"
    config.scanner.scan_method = "positive_transparency"
    config.output.save_scanner_raw = True
    config.output.watermark_scanner_raw_border = False

    process_file(input_path, output_path, config)

    positive_path = output_path.parent / "positives" / f"{input_path.stem}.darkroom_positive.npz"
    transparent_dir = positive_path.with_suffix("").parent / f"{positive_path.with_suffix('').name}_transparent_plate"
    assert output_path.exists()
    assert positive_path.exists()
    assert positive_path.with_suffix(".positive_visual.png").exists()
    assert positive_path.with_suffix(".light_table_raw.tiff").exists()
    assert not (output_path.parent / "negatives" / f"{input_path.stem}.darkroom_negative.npz").exists()
    assert transparent_dir.joinpath("positive_transparent_16bit.tiff").exists()
    assert transparent_dir.joinpath("positive_transmission_16bit.tiff").exists()
    assert transparent_dir.joinpath("density_alpha.png").exists()
    assert not (positive_path.with_suffix("").parent / f"{positive_path.with_suffix('').name}_plate_set").exists()

    with np.load(positive_path) as positive_data:
        medium_metadata = json.loads(str(positive_data["developed_medium_metadata"]))
    assert medium_metadata["medium_process"] == "slide"
    assert medium_metadata["image_polarity"] == "positive"
    assert medium_metadata["compatible_interpreters"] == ["positive_transparency_scan"]

    with positive_path.with_suffix(positive_path.suffix + ".json").open("r", encoding="utf-8") as handle:
        sidecar = json.load(handle)
    assert sidecar["kind"] == "DevelopedPositiveTransparency"
    assert sidecar["positive_path"] == str(positive_path)
    assert sidecar["paths"]["positive_path"] == str(positive_path)
    assert sidecar["paths"]["light_table_raw_path"] == str(positive_path.with_suffix(".light_table_raw.tiff"))

    with positive_path.with_suffix(".light_table_raw.tiff.json").open("r", encoding="utf-8") as handle:
        raw_sidecar = json.load(handle)
    assert raw_sidecar["kind"] == "LightTableRawPositive"
    assert raw_sidecar["developed_medium"]["image_polarity"] == "positive"
    assert raw_sidecar["border"] == {
        "present": True,
        "meaning": "derived clear-film-base transmission reference; observation only",
        "percent": 0.04,
        "min_px": 32,
        "width_px": 32,
    }


def test_gui_positive_develop_export_uses_positive_raw_transparency_and_plate_set(tmp_path):
    input_path = tmp_path / "positive_input.png"
    Image.fromarray(np.full((10, 12, 3), 155, dtype=np.uint8), mode="RGB").save(input_path)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.input_polarity = "positive"
    config.scanner.target_medium_process = "positive"
    config.output.export_plate_set = True
    config.output.save_scanner_raw = True
    medium = develop_negative(load_image(input_path), config, rng=np.random.default_rng(2))
    positive_path = tmp_path / "positives" / "positive_input.darkroom_positive.npz"

    gui.save_developed_medium(medium, positive_path, input_path, config, save_sidecar=True)

    material_base = positive_path.with_suffix("")
    transparent_dir = material_base.parent / f"{material_base.name}_transparent_plate"
    plate_dir = material_base.parent / f"{material_base.name}_plate_set"
    assert positive_path.with_suffix(".light_table_raw.tiff").exists()
    assert not positive_path.with_suffix(".scanner_raw.tiff").exists()
    assert transparent_dir.joinpath("positive_transparent_16bit.tiff").exists()
    assert transparent_dir.joinpath("positive_transmission_16bit.tiff").exists()
    assert not transparent_dir.joinpath("negative_transparent_16bit.tiff").exists()
    assert plate_dir.joinpath("cyan_plate.png").exists()


def test_positive_layer_pack_is_complete_bundle_with_polarity_aware_names(tmp_path):
    input_path = tmp_path / "pack_input.png"
    Image.fromarray(np.full((8, 9, 3), 145, dtype=np.uint8), mode="RGB").save(input_path)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.input_polarity = "positive"
    config.output.export_layer_pack = True
    config.output.export_transparent_plate = False
    config.output.export_plate_set = False
    medium = develop_negative(load_image(input_path), config, rng=np.random.default_rng(3))
    positive_path = tmp_path / "positives" / "pack_input.darkroom_positive.npz"

    gui.save_developed_medium(medium, positive_path, input_path, config, save_sidecar=True)

    pack_dir = positive_path.with_suffix("").parent / f"{positive_path.with_suffix('').name}_layer_pack"
    assert pack_dir.joinpath("electronic_positive.npz").exists()
    assert pack_dir.joinpath("positive_transparent_16bit.tiff").exists()
    assert pack_dir.joinpath("positive_transmission_16bit.tiff").exists()
    assert pack_dir.joinpath("cyan_plate.png").exists()
    assert pack_dir.joinpath("grain_layer.png").exists()
    with np.load(pack_dir / "electronic_positive.npz") as packed_medium:
        assert "film_foundry_provenance" in packed_medium.files
        assert "developed_medium_metadata" in packed_medium.files
    with pack_dir.joinpath("sidecar.json").open("r", encoding="utf-8") as handle:
        sidecar = json.load(handle)
    assert sidecar["kind"] == "ElectronicPositiveLayerPack"
    assert sidecar["image_polarity"] == "positive"


def test_process_file_reversal_program_auto_aligns_output_interpreter(tmp_path):
    input_path = tmp_path / "cross_process_input.png"
    output_path = tmp_path / "cross_process_output.png"
    Image.fromarray(np.full((9, 11, 3), 145, dtype=np.uint8), mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    # Keep the default negative scanner identity to verify full-process auto routing.
    assert config.scanner.interpreter_key == "negative_scan"

    process_file(input_path, output_path, config)

    with output_path.with_suffix(output_path.suffix + ".json").open("r", encoding="utf-8") as handle:
        sidecar = json.load(handle)
    assert sidecar["interpreter"]["interpreter_key"] == "positive_transparency_scan"
    assert sidecar["interpreter"]["input_polarity"] == "positive"
    positive_path = output_path.parent / "positives" / f"{input_path.stem}.darkroom_positive.npz"
    assert positive_path.exists()


def test_default_output_skips_plate_set_but_keeps_transparent_material(tmp_path):
    input_path = tmp_path / "input.png"
    output_path = tmp_path / "output.png"
    Image.fromarray(np.full((8, 10, 3), 150, dtype=np.uint8), mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.random_seed = 5
    config.seed_strategy = "fixed"

    process_file(input_path, output_path, config)

    negative_path = output_path.parent / "negatives" / "input.darkroom_negative.npz"
    material_base = negative_path.with_suffix("")
    assert negative_path.exists()
    assert (material_base.parent / f"{material_base.name}_transparent_plate" / "negative_transparent_16bit.tiff").exists()
    assert not (material_base.parent / f"{material_base.name}_plate_set").exists()


def test_gui_develop_save_writes_unified_medium_sidecars(tmp_path):
    input_path = tmp_path / "input.png"
    Image.fromarray(np.full((10, 12, 3), 160, dtype=np.uint8), mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.random_seed = 7
    config.seed_strategy = "fixed"
    config.output.save_scanner_raw = True
    config.output.scanner_raw_border_min_px = 1
    config.output.export_layer_pack = True
    config.output.watermark_scanner_raw_border = False

    image = load_image(input_path)
    negative = develop_negative(image, config, rng=np.random.default_rng(7))
    negative_path = tmp_path / "negatives" / "input.darkroom_negative.npz"

    gui.save_negative(negative, negative_path, input_path, config, save_sidecar=True)

    with np.load(negative_path) as negative_data:
        assert "developed_medium_metadata" in negative_data.files
        medium_metadata = json.loads(str(negative_data["developed_medium_metadata"]))
    assert medium_metadata["medium_process"] == "negative"
    assert medium_metadata["compatible_interpreters"] == ["negative_scan"]

    develop_sidecar_path = negative_path.with_suffix(negative_path.suffix + ".json")
    with develop_sidecar_path.open("r", encoding="utf-8") as handle:
        develop_sidecar = json.load(handle)
    assert develop_sidecar["kind"] == "DevelopedNegative"
    assert develop_sidecar["negative_path"] == str(negative_path)
    assert develop_sidecar["paths"]["negative_path"] == str(negative_path)
    assert develop_sidecar["paths"]["scanner_raw_path"] == str(negative_path.with_suffix(".scanner_raw.tiff"))
    assert develop_sidecar["developed_medium"]["view_mode"] == "transmissive"
    assert develop_sidecar["interpreter"]["interpreter_key"] == "negative_scan"

    scanner_raw_sidecar_path = negative_path.with_suffix(".scanner_raw.tiff.json")
    with scanner_raw_sidecar_path.open("r", encoding="utf-8") as handle:
        scanner_raw_sidecar = json.load(handle)
    assert scanner_raw_sidecar["kind"] == "ScannerRawNegative"
    assert scanner_raw_sidecar["paths"]["negative_path"] == str(negative_path)
    assert scanner_raw_sidecar["developed_medium"]["image_polarity"] == "negative"
    assert scanner_raw_sidecar["interpreter"]["input_polarity"] == "negative"

    layer_pack_sidecar = next((negative_path.with_suffix("").parent / f"{negative_path.with_suffix('').name}_layer_pack").glob("sidecar.json"))
    with layer_pack_sidecar.open("r", encoding="utf-8") as handle:
        layer_pack = json.load(handle)
    assert layer_pack["kind"] == "ElectronicNegativeLayerPack"
    assert layer_pack["developed_medium"]["medium_process"] == "negative"
    assert layer_pack["interpreter"]["interpreter_key"] == "negative_scan"
    assert layer_pack["metadata"]["paths"]["negative_path"] == str(negative_path)


def test_process_file_can_disable_negative_material_watermark(tmp_path):
    input_path = tmp_path / "input.png"
    output_path = tmp_path / "output.png"
    Image.fromarray(np.full((8, 8, 3), 128, dtype=np.uint8), mode="RGB").save(input_path)

    config = DarkroomConfig()
    config.random_seed = 1
    config.seed_strategy = "fixed"
    config.output.watermark_negative_material = False
    config.output.watermark_metadata = False
    config.output.watermark_scanner_raw_border = False
    config.output.export_transparent_plate = False
    config.output.export_plate_set = False

    process_file(input_path, output_path, config)

    assert "Software" not in Image.open(output_path).info
    negative_path = output_path.parent / "negatives" / f"{input_path.stem}.darkroom_negative.npz"
    with np.load(negative_path) as negative_data:
        assert "film_foundry_provenance" not in negative_data.files
    with negative_path.with_suffix(negative_path.suffix + ".json").open("r", encoding="utf-8") as handle:
        sidecar = json.load(handle)
    assert sidecar["provenance"] is None


def test_anti_banding_dither_changes_8bit_output_but_not_16bit(tmp_path):
    ramp = np.linspace(0.2, 0.8, 48, dtype=np.float32)
    image = np.repeat(ramp.reshape(1, 48, 1), 48, axis=0)
    image = np.repeat(image, 3, axis=-1)

    clean = DarkroomConfig().output
    clean.anti_banding_strength = 0.0
    clean.watermark_metadata = False
    dithered = DarkroomConfig().output
    dithered.anti_banding_strength = 1.0
    dithered.watermark_metadata = False

    clean_path = tmp_path / "clean.png"
    dithered_path = tmp_path / "dithered.png"
    save_image(image, clean_path, clean)
    save_image(image, dithered_path, dithered)

    clean_pixels = np.asarray(Image.open(clean_path), dtype=np.uint8)
    dithered_pixels = np.asarray(Image.open(dithered_path), dtype=np.uint8)
    assert not np.array_equal(clean_pixels, dithered_pixels)

    clean.bit_depth = 16
    dithered.bit_depth = 16
    clean16_path = tmp_path / "clean16.png"
    dithered16_path = tmp_path / "dithered16.png"
    save_image(image, clean16_path, clean)
    save_image(image, dithered16_path, dithered)
    assert np.array_equal(cv2.imread(str(clean16_path), cv2.IMREAD_UNCHANGED), cv2.imread(str(dithered16_path), cv2.IMREAD_UNCHANGED))


def test_tiled_output_quantization_matches_previous_full_frame_formula():
    from film_foundry.core.antibanding import apply_output_antibanding
    from film_foundry.core.io_utils import _quantize_rgb_rows

    image = np.random.default_rng(44).random((73, 91, 3), dtype=np.float32)
    expected = np.round(apply_output_antibanding(image, 0.73) * 255.0).astype(np.uint8)
    actual = _quantize_rgb_rows(
        image,
        bit_depth=8,
        tile_rows=11,
        anti_banding_strength=0.73,
    )
    assert np.array_equal(actual, expected)


@pytest.mark.parametrize("bit_depth,dtype,scale", [(8, np.uint8, 255.0), (16, np.uint16, 65535.0)])
def test_generic_tiled_attachment_quantization_is_exact(bit_depth, dtype, scale):
    from film_foundry.core.io_utils import quantize_unit_float_rows

    rgba = np.random.default_rng(45).uniform(-0.2, 1.2, (29, 31, 4)).astype(np.float32)
    expected = np.round(np.clip(rgba, 0.0, 1.0) * scale).astype(dtype)
    actual = quantize_unit_float_rows(
        rgba,
        bit_depth=bit_depth,
        tile_rows=7,
        label="test RGBA",
    )
    assert np.array_equal(actual, expected)
