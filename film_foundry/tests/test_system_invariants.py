import copy

import numpy as np
import pytest
from PIL import Image

from film_foundry.core.engine import (
    _material_pool_tile_rows,
    _scan_output_tile_rows,
    develop_negative,
    process_array,
    process_file,
    save_developed_medium_at_path,
    scan_negative,
)
from film_foundry.core import engine
from film_foundry.core.execution_topology import (
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.core.io_utils import load_image
from film_foundry.core.negative_io import load_developed_negative_npz
from film_foundry.model.config import DarkroomConfig


def _program_config(
    program_key: str,
    *,
    material_color: str = "color",
    material_process: str = "negative",
) -> DarkroomConfig:
    config = DarkroomConfig()
    config.chemistry.program_key = program_key
    config.film.color_process = material_color
    config.film.medium_process = material_process
    config.film.image_polarity = "positive" if material_process == "slide" else "negative"
    config.mode = "bw_negative" if program_key.startswith("bw_") else "color_negative"
    config.seed_strategy = "fixed"
    config.random_seed = 73
    config.enable_halation = False
    config.scanner.scan_normalize = False
    if program_key == "color_negative_bleach_bypass":
        config.chemistry.silver_bleach_completion = 0.35
    return config


@pytest.mark.parametrize(
    ("program_key", "material_color", "material_process", "expected_polarity", "interpreter"),
    (
        ("bw_negative", "monochrome", "negative", "negative", "negative_scan"),
        ("color_negative", "color", "negative", "negative", "negative_scan"),
        ("color_negative_bleach_bypass", "color", "negative", "negative", "negative_scan"),
        ("bw_reversal", "monochrome", "negative", "positive", "positive_transparency_scan"),
        ("color_reversal", "color", "negative", "positive", "positive_transparency_scan"),
        # Cross-process directions: color material in a silver-only program,
        # native slide material in a negative-forming program, and monochrome
        # stock in a colour coupler program.
        ("bw_negative", "color", "negative", "negative", "negative_scan"),
        ("color_negative", "color", "slide", "negative", "negative_scan"),
        ("color_negative", "monochrome", "negative", "negative", "negative_scan"),
        ("color_negative_bleach_bypass", "monochrome", "negative", "negative", "negative_scan"),
        ("color_reversal", "monochrome", "negative", "positive", "positive_transparency_scan"),
    ),
)
def test_full_and_split_pipeline_are_equivalent_for_supported_program_matrix(
    program_key,
    material_color,
    material_process,
    expected_polarity,
    interpreter,
):
    image = np.linspace(0.03, 0.97, 11 * 15 * 3, dtype=np.float32).reshape(11, 15, 3)
    config = _program_config(
        program_key,
        material_color=material_color,
        material_process=material_process,
    )

    direct = process_array(image, copy.deepcopy(config))
    medium = develop_negative(image, copy.deepcopy(config))
    master_before = medium.density_grain.copy()
    split = scan_negative(medium)

    assert medium.image_polarity == expected_polarity
    assert split.interpreter_key == interpreter
    assert np.all(np.isfinite(direct))
    assert np.allclose(direct, split.output_srgb, atol=1e-7)
    assert np.array_equal(medium.density_grain, master_before)


@pytest.mark.parametrize("enable_grain", (False, True))
@pytest.mark.parametrize(
    ("program_key", "material_color", "material_process"),
    (
        ("bw_negative", "monochrome", "negative"),
        ("color_negative", "color", "negative"),
        ("color_negative_bleach_bypass", "color", "negative"),
        ("bw_reversal", "monochrome", "negative"),
        ("color_reversal", "color", "negative"),
        ("bw_negative", "color", "negative"),
        ("color_negative", "color", "slide"),
        ("color_negative", "monochrome", "negative"),
        ("color_reversal", "monochrome", "negative"),
    ),
)
def test_developed_master_buffers_have_isolated_public_ownership(
    program_key,
    material_color,
    material_process,
    enable_grain,
):
    image = np.linspace(0.04, 0.96, 12 * 18 * 3, dtype=np.float32).reshape(12, 18, 3)
    config = _program_config(
        program_key,
        material_color=material_color,
        material_process=material_process,
    )
    config.enable_grain = enable_grain
    medium = develop_negative(image, config)

    assert medium.density_cmy.dtype == np.float32
    assert medium.density_grain.dtype == np.float32
    assert medium.density_cmy.flags.writeable is True
    assert medium.density_grain.flags.writeable is True
    assert not np.shares_memory(medium.density_cmy, medium.density_grain)
    if medium.optical_density_rgb is not None:
        assert medium.optical_density_rgb.dtype == np.float32
        assert medium.optical_density_rgb.flags.writeable is False
        assert not np.shares_memory(medium.optical_density_rgb, medium.density_cmy)
        assert not np.shares_memory(medium.optical_density_rgb, medium.density_grain)


@pytest.mark.parametrize("cold_history", (False, True))
@pytest.mark.parametrize("enable_mtf", (False, True))
@pytest.mark.parametrize("enable_halation", (False, True))
def test_developed_history_buffers_have_isolated_public_ownership(
    cold_history,
    enable_mtf,
    enable_halation,
):
    image = np.linspace(0.02, 0.98, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    config = _program_config("color_negative")
    config.enable_mtf = enable_mtf
    config.enable_halation = enable_halation
    config.processing.history_storage_policy = "cold_fp16" if cold_history else "full"
    medium = develop_negative(image, config)
    expected_dtype = np.float16 if cold_history else np.float32
    histories = (medium.linear_input, medium.after_mtf, medium.after_halation)

    assert all(history.dtype == expected_dtype for history in histories)
    assert all(history.flags.writeable is True for history in histories)
    assert all(float(history.min()) >= 0.0 for history in histories)
    assert all(float(history.max()) <= 1.0 for history in histories)
    assert not np.shares_memory(histories[0], histories[1])
    assert not np.shares_memory(histories[0], histories[2])
    assert not np.shares_memory(histories[1], histories[2])


def test_default_output_scan_tiling_starts_at_the_8mp_exact_execution_boundary():
    config = DarkroomConfig()
    below_boundary = np.empty((1999, 4000, 0), dtype=np.float32)
    at_boundary = np.empty((2000, 4000, 0), dtype=np.float32)

    assert config.processing.scan_tile_threshold_megapixels == 8.0
    assert _scan_output_tile_rows(config, below_boundary) is None
    assert _scan_output_tile_rows(config, at_boundary) == 512


def test_memory_budget_advances_only_the_reversible_exact_material_schedule():
    config = DarkroomConfig()
    below_static_boundary = np.empty((1000, 1000, 0), dtype=np.float32)

    assert _material_pool_tile_rows(config, below_static_boundary) is None
    config.processing.memory_budget_mb = 64.0
    assert _material_pool_tile_rows(config, below_static_boundary) == 256
    config.processing.material_tile_rows = 0
    assert _material_pool_tile_rows(config, below_static_boundary) is None


@pytest.mark.parametrize(
    ("program_key", "material_color", "material_process"),
    (
        ("bw_negative", "monochrome", "negative"),
        ("color_negative", "color", "negative"),
        ("bw_reversal", "monochrome", "negative"),
        ("color_reversal", "color", "negative"),
        ("bw_negative", "color", "negative"),
        ("color_negative", "monochrome", "negative"),
        ("color_reversal", "monochrome", "negative"),
    ),
)
def test_exact_material_row_tiling_preserves_formed_density(
    program_key,
    material_color,
    material_process,
):
    image = np.linspace(0.01, 0.99, 19 * 23 * 3, dtype=np.float32).reshape(19, 23, 3)
    untiled = _program_config(
        program_key,
        material_color=material_color,
        material_process=material_process,
    )
    untiled.processing.material_tile_rows = 0
    tiled = copy.deepcopy(untiled)
    tiled.processing.material_tile_rows = 4
    tiled.processing.material_tile_threshold_megapixels = 1e-9

    untiled_medium = develop_negative(image, untiled)
    tiled_medium = develop_negative(image, tiled)

    assert np.array_equal(tiled_medium.density_cmy, untiled_medium.density_cmy)
    assert np.array_equal(tiled_medium.density_grain, untiled_medium.density_grain)
    if untiled_medium.optical_density_rgb is None:
        assert tiled_medium.optical_density_rgb is None
    else:
        assert np.array_equal(
            tiled_medium.optical_density_rgb,
            untiled_medium.optical_density_rgb,
        )
        assert np.array_equal(
            tiled_medium.clear_base_optical_density_rgb,
            untiled_medium.clear_base_optical_density_rgb,
        )
    tiled_model = tiled_medium.metadata["film_process_model"]
    untiled_model = untiled_medium.metadata["film_process_model"]
    assert tiled_model["representation"] == untiled_model["representation"]
    assert tiled_model["components"] == untiled_model["components"]
    assert [step["action"] for step in tiled_model["process_trace"]] == [
        step["action"] for step in untiled_model["process_trace"]
    ]
    assert tiled_model["execution"]["material_pool_tiling"] == "exact_row_tiles_v1"


@pytest.mark.parametrize(
    ("program_key", "mode"),
    (
        ("color_negative", "auto"),
        ("color_negative", "positive"),
        ("color_reversal", "auto"),
        ("color_reversal", "negative"),
        ("bw_negative", "auto"),
        ("bw_reversal", "auto"),
    ),
)
def test_output_only_scan_matches_full_diagnostic_scan(program_key, mode):
    image = np.linspace(0.02, 0.98, 13 * 17 * 3, dtype=np.float32).reshape(13, 17, 3)
    material_color = "monochrome" if program_key.startswith("bw_") else "color"
    config = _program_config(program_key, material_color=material_color)
    medium = develop_negative(image, config)
    runtime = medium.metadata["runtime_config"]

    if mode == "auto":
        expected = scan_negative(medium).output_srgb
    else:
        expected = engine.scan_medium_direct(medium, runtime, mode).output_srgb
    actual, _ = engine._scan_medium_output_only(medium, runtime, mode)

    assert np.array_equal(actual, expected)


@pytest.mark.parametrize(
    ("scan_method", "enable_subtractive"),
    (
        ("legacy_density_mapping", True),
        ("negative_inversion", False),
    ),
)
def test_output_only_negative_scan_preserves_legacy_and_non_subtractive_routes(
    scan_method,
    enable_subtractive,
):
    image = np.linspace(0.04, 0.96, 11 * 19 * 3, dtype=np.float32).reshape(11, 19, 3)
    config = _program_config("color_negative")
    config.scanner.scan_method = scan_method
    config.enable_subtractive = enable_subtractive
    medium = develop_negative(image, config)
    runtime = medium.metadata["runtime_config"]

    expected = scan_negative(medium).output_srgb
    actual, _ = engine._scan_medium_output_only(medium, runtime, "auto")

    assert np.array_equal(actual, expected)


@pytest.mark.parametrize(
    ("program_key", "normalize_mode"),
    (
        ("color_negative", "luma"),
        ("color_negative", "rgb"),
        ("color_reversal", "luma"),
        ("color_reversal", "rgb"),
    ),
)
def test_output_only_scan_row_tiling_preserves_global_interpretation(
    program_key,
    normalize_mode,
):
    image = np.linspace(0.02, 0.98, 21 * 29 * 3, dtype=np.float32).reshape(21, 29, 3)
    config = _program_config(program_key)
    config.scanner.scan_normalize = True
    config.scanner.scan_normalize_mode = normalize_mode
    medium = develop_negative(image, config)
    untiled = copy.deepcopy(medium.metadata["runtime_config"])
    untiled.processing.scan_tile_rows = 0
    tiled = copy.deepcopy(untiled)
    tiled.processing.scan_tile_rows = 4
    tiled.processing.scan_tile_threshold_megapixels = 1e-9

    if medium.image_polarity == "positive":
        expected = engine._scan_positive_output_only(medium, untiled)
        actual = engine._scan_positive_output_only(medium, tiled)
    else:
        expected = engine._scan_negative_output_only(medium, untiled)
        actual = engine._scan_negative_output_only(medium, tiled)

    assert np.allclose(actual, expected, atol=1e-7)


def test_tiled_negative_uses_one_global_calibration_for_adversarial_bands(monkeypatch):
    """Tiles with incompatible local ranges must still share one calibration."""
    image = np.empty((24, 31, 3), dtype=np.float32)
    image[0:6] = (0.015, 0.04, 0.85)
    image[6:12] = (0.90, 0.03, 0.02)
    image[12:18] = (0.04, 0.88, 0.08)
    image[18:24] = (0.92, 0.78, 0.18)
    image *= np.linspace(0.72, 1.0, image.shape[1], dtype=np.float32)[None, :, None]

    config = _program_config("color_negative")
    config.scanner.scan_normalize = True
    config.scanner.scan_normalize_mode = "rgb"
    medium = develop_negative(image, config)
    untiled = copy.deepcopy(medium.metadata["runtime_config"])
    untiled.processing.scan_tile_rows = 0
    expected = engine._scan_negative_output_only(medium, untiled)

    calls = {"base": 0, "normalization": 0}
    original_base = engine._clear_base_scanner_sample
    original_range = engine.scan_normalization_range

    def counted_base(*args, **kwargs):
        calls["base"] += 1
        return original_base(*args, **kwargs)

    def counted_range(*args, **kwargs):
        calls["normalization"] += 1
        return original_range(*args, **kwargs)

    monkeypatch.setattr(engine, "_clear_base_scanner_sample", counted_base)
    monkeypatch.setattr(engine, "scan_normalization_range", counted_range)
    tiled = copy.deepcopy(untiled)
    tiled.processing.scan_tile_rows = 6
    tiled.processing.scan_tile_threshold_megapixels = 1e-9
    actual = engine._scan_negative_output_only(medium, tiled)

    assert calls == {"base": 1, "normalization": 1}
    assert np.allclose(actual, expected, atol=1e-7)


def test_tiled_positive_output_is_exactly_reference_allocation_topology():
    image = np.linspace(0.02, 0.98, 37 * 53 * 3, dtype=np.float32).reshape(
        37,
        53,
        3,
    )
    config = _program_config("color_reversal", material_process="slide")
    medium = develop_negative(image, config)
    runtime = copy.deepcopy(medium.metadata["runtime_config"])
    runtime.processing.scan_tile_rows = 7
    runtime.processing.scan_tile_threshold_megapixels = 1e-9

    optimized = engine._scan_positive_output_only(medium, copy.deepcopy(runtime))
    with execution_topology(REFERENCE_TOPOLOGY):
        reference = engine._scan_positive_output_only(medium, copy.deepcopy(runtime))

    assert np.array_equal(optimized, reference)


def test_zero_strength_tiled_normalization_skips_global_statistics(monkeypatch):
    image = np.linspace(0.04, 0.96, 31 * 47 * 3, dtype=np.float32).reshape(
        31,
        47,
        3,
    )
    config = _program_config("color_negative")
    medium = develop_negative(image, config)
    runtime = copy.deepcopy(medium.metadata["runtime_config"])
    runtime.processing.scan_tile_rows = 5
    runtime.processing.scan_tile_threshold_megapixels = 1e-9
    runtime.scanner.scan_normalize = True
    runtime.scanner.scan_normalize_strength = 0.0

    def unexpected_statistics(*_args, **_kwargs):
        raise AssertionError("zero-strength normalization must not measure percentiles")

    monkeypatch.setattr(engine, "scan_normalization_range", unexpected_statistics)
    output = engine._scan_negative_output_only(medium, runtime)

    assert np.isfinite(output).all()


def test_process_array_production_path_does_not_build_diagnostic_scan(monkeypatch):
    image = np.linspace(0.06, 0.94, 9 * 15 * 3, dtype=np.float32).reshape(9, 15, 3)
    config = _program_config("color_negative")

    def diagnostic_scan_was_called(*_args, **_kwargs):
        raise AssertionError("diagnostic scan path must not run")

    monkeypatch.setattr(engine, "scan_negative", diagnostic_scan_was_called)
    monkeypatch.setattr(engine, "scan_medium_direct", diagnostic_scan_was_called)

    output = process_array(image, config)
    assert output.shape == image.shape
    assert np.all(np.isfinite(output))


def test_production_mixed_precision_only_compacts_consumed_history():
    image = np.linspace(0.03, 0.97, 10 * 16 * 3, dtype=np.float32).reshape(10, 16, 3)
    config = _program_config("color_negative")

    output, _runtime, medium = engine._process_output_with_runtime(image, config)

    assert output.dtype == np.float32
    assert medium.linear_input.dtype == np.float16
    assert medium.after_mtf.dtype == np.float16
    assert medium.after_halation.dtype == np.float16
    assert medium.density_cmy.dtype == np.float32
    assert medium.density_grain.dtype == np.float32
    assert medium.metadata["stage_storage"]["authoritative_density_dtype"] == "float32"


def test_output_only_runtime_discards_unobserved_history_without_changing_output():
    image = np.linspace(0.03, 0.97, 10 * 16 * 3, dtype=np.float32).reshape(10, 16, 3)
    config = _program_config("color_negative")

    expected, _runtime, cold_medium = engine._process_output_with_runtime(
        image,
        copy.deepcopy(config),
    )
    actual, _runtime, output_only_medium = engine._process_output_with_runtime(
        image,
        copy.deepcopy(config),
        retain_cold_history=False,
    )

    assert np.array_equal(actual, expected)
    histories = (
        output_only_medium.linear_input,
        output_only_medium.after_mtf,
        output_only_medium.after_halation,
    )
    assert all(history.shape == (0, 0, 3) for history in histories)
    assert all(history.dtype == np.float32 for history in histories)
    assert all(history.nbytes == 0 for history in histories)
    assert histories[0] is not histories[1]
    assert histories[0] is not histories[2]
    assert histories[1] is not histories[2]
    assert not np.shares_memory(histories[0], histories[1])
    assert not np.shares_memory(histories[0], histories[2])
    assert not np.shares_memory(histories[1], histories[2])
    assert output_only_medium.metadata["stage_storage"] == {
        "policy": "output_only_discarded_history_v1",
        "cold_history_dtype": None,
        "authoritative_density_dtype": "float32",
        "discarded_histories": ["linear_input", "after_mtf", "after_halation"],
    }
    assert cold_medium.linear_input.size == image.size


def test_immediate_array_output_releases_compatibility_layers_after_optical_compose():
    image = np.linspace(0.03, 0.97, 10 * 16 * 3, dtype=np.float32).reshape(10, 16, 3)
    config = _program_config("color_negative")

    expected, _runtime, retained = engine._process_output_with_runtime(
        image,
        copy.deepcopy(config),
        retain_cold_history=False,
        retain_layer_masters=True,
    )
    actual, _runtime, released = engine._process_output_with_runtime(
        image,
        copy.deepcopy(config),
        retain_cold_history=False,
        retain_layer_masters=False,
    )

    assert np.array_equal(actual, expected)
    assert retained.layer_masters_available is True
    assert released.layer_masters_available is False
    assert released.density_cmy.nbytes == 0
    assert released.density_grain.nbytes == 0
    assert released.optical_density_rgb is not None
    assert released.metadata["stage_storage"]["profile"] == "scan_optical_only_v1"
    assert (
        released.metadata["stage_storage"]["layer_masters"]
        == "discarded_during_optical_compose"
    )


def test_public_develop_api_rejects_internal_discard_history_policy():
    image = np.full((5, 7, 3), 0.4, dtype=np.float32)
    config = _program_config("color_negative")
    config.processing.history_storage_policy = "discard"

    with pytest.raises(ValueError, match="history_storage_policy"):
        develop_negative(image, config)


def test_plate_capable_production_path_retains_full_precision_history():
    image = np.linspace(0.03, 0.97, 10 * 16 * 3, dtype=np.float32).reshape(10, 16, 3)
    config = _program_config("color_negative")

    _output, _runtime, medium = engine._process_output_with_runtime(
        image,
        config,
        retain_development_stages=True,
    )

    assert medium.linear_input.dtype == np.float32
    assert medium.after_mtf.dtype == np.float32
    assert medium.after_halation.dtype == np.float32
    assert "stage_storage" not in medium.metadata


@pytest.mark.parametrize("program_key", ("color_negative", "bw_negative", "color_reversal", "bw_reversal"))
def test_saved_medium_roundtrip_preserves_master_and_default_observation(tmp_path, program_key):
    image = np.linspace(0.04, 0.96, 9 * 13 * 3, dtype=np.float32).reshape(9, 13, 3)
    material_color = "monochrome" if program_key.startswith("bw_") else "color"
    config = _program_config(program_key, material_color=material_color)
    config.look.saturation_multiplier = 1.17
    config.look.print_exposure_ev = 0.28
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    medium = develop_negative(image, config)
    expected = scan_negative(medium)
    suffix = ".darkroom_positive.npz" if medium.image_polarity == "positive" else ".darkroom_negative.npz"
    path = tmp_path / f"master{suffix}"

    save_developed_medium_at_path(tmp_path / "source.png", path, medium, config)
    loaded = load_developed_negative_npz(path)
    actual = scan_negative(loaded)

    assert np.array_equal(loaded.density_cmy, medium.density_cmy)
    assert np.array_equal(loaded.density_grain, medium.density_grain)
    assert loaded.image_polarity == medium.image_polarity
    assert loaded.compatible_interpreters == medium.compatible_interpreters
    assert np.allclose(actual.output_srgb, expected.output_srgb, atol=1e-7)


def test_file_entry_matches_array_entry_and_exported_master(tmp_path):
    input_path = tmp_path / "source.png"
    output_path = tmp_path / "result.png"
    pixels = np.arange(10 * 14 * 3, dtype=np.uint8).reshape(10, 14, 3)
    Image.fromarray(pixels, mode="RGB").save(input_path)
    config = _program_config("color_negative")
    config.output.bit_depth = 16
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    source = load_image(input_path)
    expected = process_array(source, copy.deepcopy(config))

    process_file(input_path, output_path, config)
    saved = load_image(output_path)
    medium_path = tmp_path / "negatives" / "source.darkroom_negative.npz"
    loaded = load_developed_negative_npz(medium_path)
    rescanned = scan_negative(loaded)

    assert np.allclose(saved, expected, atol=1.0 / 65535.0 + 1e-7)
    assert np.allclose(rescanned.output_srgb, expected, atol=1e-7)
