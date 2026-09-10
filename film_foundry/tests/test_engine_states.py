import copy

import numpy as np
import pytest

from film_foundry.core.engine import (
    _rng_for_input,
    _emulsion_spread_work_long_edge,
    _processing_work_long_edge,
    develop_negative,
    process_array,
    process_array_with_stages,
    scan_medium_direct,
    scan_medium_output,
    scan_negative,
    scan_scanner_raw_direct,
)
from film_foundry.core.electronic_negative import scanner_raw_with_clear_border
from film_foundry.core.execution_topology import (
    OPTIMIZED_TOPOLOGY,
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.core.scanner import (
    capture_optical_density,
    negative_backlight_illuminant_rgb,
    negative_total_density_rgb,
)
from film_foundry.core.states import DevelopedNegative, ScannedPositive
from film_foundry.model.config import DarkroomConfig


def test_manual_transmission_controls_do_not_modify_final_medium():
    image = np.linspace(0.05, 0.95, 18 * 24 * 3, dtype=np.float32).reshape(
        18,
        24,
        3,
    )
    develop_config = DarkroomConfig()
    develop_config.enable_grain = False
    medium = develop_negative(image, develop_config)
    optical_before = medium.optical_density_rgb.copy()

    interpreted = copy.deepcopy(develop_config)
    interpreted.scanner.interpretation_mode = "manual"
    interpreted.scanner.remove_base_mask = True
    interpreted.scanner.invert_transmission = True
    inverted = scan_medium_output(medium, interpreted, "manual")

    direct = copy.deepcopy(interpreted)
    direct.scanner.remove_base_mask = False
    direct.scanner.invert_transmission = False
    photographed_negative = scan_medium_output(medium, direct, "manual")

    assert inverted.metadata["resolved_interpretation"] == "negative"
    assert photographed_negative.metadata["resolved_interpretation"] == "direct"
    assert photographed_negative.metadata["remove_base_mask"] is False
    assert photographed_negative.metadata["invert_transmission"] is False
    assert not np.allclose(inverted.output_srgb, photographed_negative.output_srgb)
    assert np.array_equal(medium.optical_density_rgb, optical_before)


def test_clear_base_reference_border_is_observation_only_and_tiles_cleanly():
    image = np.linspace(0.08, 0.92, 20 * 28 * 3, dtype=np.float32).reshape(
        20,
        28,
        3,
    )
    config = DarkroomConfig()
    config.enable_grain = False
    medium = develop_negative(image, config)
    optical_before = medium.optical_density_rgb.copy()

    config.scanner.interpretation_mode = "manual"
    config.scanner.remove_base_mask = False
    config.scanner.invert_transmission = False
    config.scanner.include_clear_base_border = True
    config.output.scanner_raw_border_percent = 0.10
    config.output.scanner_raw_border_min_px = 1
    untiled = copy.deepcopy(config)
    untiled.processing.scan_tile_rows = 0
    config.processing.scan_tile_rows = 4
    config.processing.scan_tile_threshold_megapixels = 0.0

    expected = scan_medium_output(medium, untiled, "manual")
    output = scan_medium_output(medium, config, "manual")

    assert output.output_srgb.shape == (24, 32, 3)
    assert np.array_equal(output.output_srgb, expected.output_srgb)
    assert np.isfinite(output.output_srgb).all()
    assert np.array_equal(medium.optical_density_rgb, optical_before)


def test_random_strategy_returns_the_concrete_replay_seed():
    config = DarkroomConfig()
    config.seed_strategy = "random"

    rng, resolved_seed = _rng_for_input("audit-input.tif", config)

    assert isinstance(resolved_seed, int)
    assert 0 <= resolved_seed < 2**32
    expected = np.random.default_rng(resolved_seed)
    assert np.array_equal(rng.integers(0, 2**32, size=16), expected.integers(0, 2**32, size=16))


def test_develop_and_scan_are_separate_states():
    image = np.full((12, 16, 3), 0.45, dtype=np.float32)
    config = DarkroomConfig()
    config.enable_grain = False
    config.scanner.scan_normalize = False

    negative = develop_negative(image, config)
    scanned = scan_negative(negative)

    assert isinstance(negative, DevelopedNegative)
    assert isinstance(scanned, ScannedPositive)
    assert negative.density_grain.shape == image.shape
    assert negative.medium_family == "film"
    assert negative.medium_process == "negative"
    assert negative.image_polarity == "negative"
    assert negative.view_mode == "transmissive"
    assert negative.base_type == "orange_mask"
    assert negative.compatible_interpreters == ("negative_scan",)
    assert scanned.negative_total_density.shape == image.shape
    assert scanned.scanner_raw.shape == image.shape
    assert scanned.negative_channel_reconstructed.shape == image.shape
    assert scanned.interpreter_key == "negative_scan"
    assert scanned.input_polarity == "negative"
    assert scanned.output_polarity == "positive"
    assert scanned.output_srgb.shape == image.shape
    assert scanned.positive_linear.min() >= 0.0
    assert scanned.positive_linear.max() <= 1.0


def test_unified_color_process_scans_authoritative_rgb_optical_master():
    image = np.linspace(0.08, 0.92, 9 * 13 * 3, dtype=np.float32).reshape(9, 13, 3)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_negative_bleach_bypass"
    config.chemistry.silver_bleach_completion = 0.0
    config.enable_grain = False
    config.scanner.scan_normalize = False

    medium = develop_negative(image, config)
    authoritative = medium.optical_density_rgb.copy()
    # The compatibility layer master is intentionally not the scanner's
    # authority once a pool-derived RGB observation is present.
    medium.density_grain.fill(0.0)
    scanned = scan_negative(medium, config)

    assert medium.optical_density_rgb is not None
    assert medium.clear_base_optical_density_rgb is not None
    assert np.array_equal(scanned.negative_total_density, authoritative)
    compatibility_optics = negative_total_density_rgb(medium.density_grain, config.film)
    assert not np.allclose(authoritative, compatibility_optics)


def test_derived_rgb_optical_master_is_read_only():
    config = DarkroomConfig()
    config.enable_grain = False
    medium = develop_negative(np.full((5, 7, 3), 0.45, dtype=np.float32), config)

    assert medium.optical_density_rgb is not None
    assert medium.optical_density_rgb.flags.writeable is False
    with pytest.raises(ValueError):
        medium.optical_density_rgb[0, 0, 0] = 0.0


def test_emulsion_grain_preserves_formed_layers_and_enters_rgb_optical_master():
    image = np.linspace(0.05, 0.95, 64 * 96 * 3, dtype=np.float32).reshape(64, 96, 3)
    clean = DarkroomConfig()
    clean.enable_halation = False
    clean.enable_mtf = False
    clean.enable_grain = False
    clean.scanner.scan_normalize = False
    grain = copy.deepcopy(clean)
    grain.enable_grain = True

    clean_medium = develop_negative(image, clean, rng=np.random.default_rng(7))
    grain_medium = develop_negative(image, grain, rng=np.random.default_rng(7))

    assert np.array_equal(grain_medium.density_cmy, clean_medium.density_cmy)
    assert not np.array_equal(grain_medium.density_grain, grain_medium.density_cmy)
    assert not np.shares_memory(grain_medium.density_grain, grain_medium.density_cmy)
    assert not np.array_equal(
        grain_medium.optical_density_rgb,
        clean_medium.optical_density_rgb,
    )
    assert not np.array_equal(
        scan_negative(grain_medium, grain).output_srgb,
        scan_negative(clean_medium, clean).output_srgb,
    )


def test_legacy_monochrome_emulsion_grain_is_neutral_in_rgb_optical_master():
    image = np.linspace(0.08, 0.92, 48 * 72 * 3, dtype=np.float32).reshape(48, 72, 3)
    clean = DarkroomConfig()
    clean.mode = "bw_negative"
    clean.film.color_process = "monochrome"
    clean.chemistry.program_key = "bw_negative"
    clean.enable_halation = False
    clean.enable_mtf = False
    clean.enable_grain = False
    grain = copy.deepcopy(clean)
    grain.enable_grain = True

    clean_medium = develop_negative(image, clean, rng=np.random.default_rng(13))
    grain_medium = develop_negative(image, grain, rng=np.random.default_rng(13))
    optical_delta = grain_medium.optical_density_rgb - clean_medium.optical_density_rgb

    assert np.array_equal(grain_medium.density_cmy, clean_medium.density_cmy)
    assert not np.array_equal(grain_medium.density_grain, grain_medium.density_cmy)
    assert np.allclose(optical_delta[..., 0], optical_delta[..., 1], atol=1e-7)
    assert np.allclose(optical_delta[..., 1], optical_delta[..., 2], atol=1e-7)


def test_monochrome_grain_keeps_deferred_stain_and_plating_in_portable_master():
    image = np.full((64, 96, 3), 0.46, dtype=np.float32)
    clean = DarkroomConfig()
    clean.mode = "bw_negative"
    clean.film.color_process = "monochrome"
    clean.chemistry.program_key = "bw_negative"
    clean.enable_halation = False
    clean.enable_mtf = False
    clean.enable_grain = True
    accidents = copy.deepcopy(clean)
    accidents.chemistry.chemical_stain = 0.85
    accidents.chemistry.silver_plating = 0.75

    clean_medium = develop_negative(image, clean, rng=np.random.default_rng(29))
    accident_medium = develop_negative(image, accidents, rng=np.random.default_rng(29))

    assert set(accident_medium.metadata["accident_maps"]) >= {
        "chemical_stain",
        "silver_plating",
    }
    assert float(accident_medium.density_grain.mean()) > (
        float(clean_medium.density_grain.mean()) + 0.03
    )
    assert np.array_equal(
        accident_medium.density_grain[..., 0],
        accident_medium.density_grain[..., 1],
    )
    assert np.array_equal(
        accident_medium.density_grain[..., 1],
        accident_medium.density_grain[..., 2],
    )


def test_physical_raw_preserves_base_while_oracle_interpretation_is_read_only():
    image = np.linspace(0.06, 0.94, 7 * 11 * 3, dtype=np.float32).reshape(7, 11, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.scanner.scan_normalize = False
    medium = develop_negative(image, config)
    master_before = medium.optical_density_rgb.copy()

    physical_raw = scanner_raw_with_clear_border(
        medium.density_grain,
        config.film,
        config.scanner,
        border_percent=0.0,
        border_min_px=0,
        optical_density_rgb=medium.optical_density_rgb,
        clear_base_optical_density_rgb=medium.clear_base_optical_density_rgb,
    )
    expected_raw = capture_optical_density(
        medium.optical_density_rgb,
        config.scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(config.scanner),
    )
    interpreted = scan_negative(medium, config)

    assert np.array_equal(physical_raw, expected_raw)
    assert not np.allclose(physical_raw, interpreted.negative_base_balanced)
    assert interpreted.metadata["base_balance_source"] == "generated_clear_base"
    assert np.array_equal(medium.optical_density_rgb, master_before)
    assert medium.optical_density_rgb.flags.writeable is False


def test_surface_silver_enters_rgb_master_as_neutral_broadband_density():
    image = np.linspace(0.1, 0.9, 8 * 10 * 3, dtype=np.float32).reshape(8, 10, 3)
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.film.dye_absorption_matrix = (
        (1.0, 0.18, 0.04),
        (0.07, 1.0, 0.16),
        (0.03, 0.22, 1.0),
    )
    plated = copy.deepcopy(clean)
    plated.chemistry.silver_plating = 0.8

    clean_medium = develop_negative(image, clean, rng=np.random.default_rng(19))
    plated_medium = develop_negative(image, plated, rng=np.random.default_rng(19))
    delta = plated_medium.optical_density_rgb - clean_medium.optical_density_rgb
    means = delta.mean(axis=(0, 1))

    assert np.all(means > 0.0)
    assert float(np.max(means) - np.min(means)) < 1e-5
    assert plated_medium.metadata["optical_master_components"]["surface_silver"].startswith(
        "neutral broadband"
    )


def test_generated_negative_scan_uses_known_clear_base_not_image_percentile():
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    config.enable_grain = False
    density = np.full((8, 10, 3), 0.55, dtype=np.float32)
    empty = np.zeros_like(density)
    negative = DevelopedNegative(
        linear_input=empty,
        after_mtf=empty,
        after_halation=empty,
        density_cmy=density,
        density_grain=density,
        metadata={"runtime_config": config},
    )

    scanned = scan_negative(negative, config)

    assert scanned.metadata["base_balance_source"] == "generated_clear_base"
    assert float(scanned.negative_base_balanced.max()) < 1.0
    assert not np.allclose(scanned.negative_base_balanced, 1.0)


def test_external_scanner_raw_records_the_estimated_base_anchor_when_unknown():
    config = DarkroomConfig()
    config.scanner.scan_normalize = False
    config.scanner.scan_base_percentile = 97.5
    raw = np.linspace(0.04, 0.92, 11 * 13 * 3, dtype=np.float32).reshape(11, 13, 3)
    expected = np.percentile(raw, 97.5, axis=(0, 1)).astype(np.float32)

    scanned = scan_scanner_raw_direct(raw, config, "negative")

    assert scanned.metadata["base_balance_source"] == "image_percentile_fallback"
    assert np.array_equal(
        np.asarray(scanned.metadata["base_anchor_rgb"], dtype=np.float32),
        expected,
    )
    assert scanned.metadata["base_estimation_percentile"] == 97.5
    assert np.array_equal(
        scanned.negative_base_balanced,
        np.clip(raw / expected.reshape(1, 1, 3), 1e-6, 1.0).astype(np.float32),
    )


def test_slide_positive_transparency_prototype_develops_and_scans_without_inversion():
    image = np.linspace(0.08, 0.92, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    config = DarkroomConfig()
    config.film.name = "Test Slide"
    config.film.medium_process = "slide"
    config.film.image_polarity = "positive"
    config.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    config.chemistry.medium_process = "slide"
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.target_medium_process = "slide"
    config.scanner.input_polarity = "positive"
    config.scanner.output_polarity = "positive"
    config.scanner.scan_method = "positive_transparency"
    config.scanner.scan_normalize = False
    config.enable_grain = False

    slide = develop_negative(image, config)
    scanned = scan_negative(slide)

    assert slide.medium_process == "slide"
    assert slide.image_polarity == "positive"
    assert slide.view_mode == "transmissive"
    assert slide.base_type == "clear_base"
    assert slide.compatible_interpreters == ("positive_transparency_scan",)
    assert slide.metadata["stage"] == "developed_positive_transparency"
    assert scanned.interpreter_key == "positive_transparency_scan"
    assert scanned.input_polarity == "positive"
    assert scanned.output_polarity == "positive"
    assert scanned.metadata["scan_source"] == "density_positive_transparency"
    assert scanned.output_srgb.shape == image.shape
    assert float(scanned.positive_linear.max()) > float(scanned.positive_linear.min())


def test_light_table_controls_affect_positive_transparency_scan():
    image = np.linspace(0.08, 0.92, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    config = DarkroomConfig()
    config.film.medium_process = "slide"
    config.film.image_polarity = "positive"
    config.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    config.scanner.interpreter_key = "positive_transparency_scan"
    config.scanner.target_medium_process = "slide"
    config.scanner.input_polarity = "positive"
    config.scanner.output_polarity = "positive"
    config.scanner.scan_method = "positive_transparency"
    config.scanner.scan_normalize = False
    config.scanner.positive_scan_color_control_strength = 0.0
    config.look.print_exposure_ev = 0.0
    config.enable_grain = False

    slide = develop_negative(image, config)
    neutral_scan = scan_negative(slide, config)

    bright = copy.deepcopy(config)
    bright.scanner.light_table_ev = 1.25
    bright_scan = scan_negative(slide, bright)

    warm = copy.deepcopy(config)
    warm.scanner.light_table_temperature_k = 3200.0
    warm_scan = scan_negative(slide, warm)

    scanner_ev = copy.deepcopy(config)
    scanner_ev.look.print_exposure_ev = 1.25
    scanner_ev_scan = scan_negative(slide, scanner_ev)

    assert not np.allclose(neutral_scan.scanner_raw, bright_scan.scanner_raw)
    assert float(bright_scan.scanner_raw.mean()) > float(neutral_scan.scanner_raw.mean())
    assert not np.allclose(neutral_scan.scanner_raw, warm_scan.scanner_raw)
    assert np.allclose(neutral_scan.scanner_raw, scanner_ev_scan.scanner_raw)
    assert not np.allclose(neutral_scan.positive_linear, scanner_ev_scan.positive_linear)
    assert bright_scan.metadata["light_table_ev"] == 1.25
    assert warm_scan.metadata["light_table_temperature_k"] == 3200.0

    # Luma normalization may remap display brightness, but must not neutralize
    # the chromaticity introduced by a warm light table.
    normalized = copy.deepcopy(config)
    normalized.scanner.scan_normalize = True
    normalized.scanner.scan_normalize_strength = 0.55
    normalized_scan = scan_negative(slide, normalized)
    warm_normalized = copy.deepcopy(normalized)
    warm_normalized.scanner.light_table_temperature_k = 3200.0
    warm_normalized_scan = scan_negative(slide, warm_normalized)
    neutral_red_blue = float(
        np.mean(normalized_scan.positive_linear[..., 0])
        / max(float(np.mean(normalized_scan.positive_linear[..., 2])), 1e-8)
    )
    warm_red_blue = float(
        np.mean(warm_normalized_scan.positive_linear[..., 0])
        / max(float(np.mean(warm_normalized_scan.positive_linear[..., 2])), 1e-8)
    )
    assert warm_red_blue > neutral_red_blue


def test_negative_channel_reconstruction_changes_scan_only_not_developed_medium():
    image = np.linspace(0.08, 0.92, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.scanner.scan_normalize = False
    medium = develop_negative(image, config)
    density_master = medium.density_grain.copy()

    altered = copy.deepcopy(config)
    altered.scanner.negative_channel_compensation_enabled = True
    altered.scanner.negative_channel_gamma = (1.0, 0.94, 0.88)
    altered.scanner.negative_channel_matrix = (
        (1.0, 0.0, 0.0),
        (0.04, 0.96, 0.0),
        (0.06, -0.02, 0.96),
    )
    neutral_scan = scan_negative(medium, config)
    altered_scan = scan_negative(medium, altered)

    assert np.array_equal(medium.density_grain, density_master)
    assert np.allclose(neutral_scan.scanner_raw, altered_scan.scanner_raw)
    assert not np.allclose(
        neutral_scan.negative_channel_reconstructed,
        altered_scan.negative_channel_reconstructed,
    )
    assert not np.allclose(neutral_scan.output_srgb, altered_scan.output_srgb)
    assert altered_scan.metadata["negative_channel_compensation_enabled"] is True
    assert len(altered_scan.metadata["negative_material_compensation_matrix"]) == 3
    assert 0.0 <= altered_scan.metadata["scanner_saturation_fraction"] <= 1.0
    assert 0.0 <= altered_scan.metadata["scanner_floor_fraction"] <= 1.0


def test_positive_transparency_scan_ignores_negative_channel_reconstruction():
    image = np.linspace(0.08, 0.92, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.enable_grain = False
    config.scanner.scan_normalize = False
    medium = develop_negative(image, config)

    altered = copy.deepcopy(config)
    altered.scanner.negative_channel_gamma = (0.7, 1.2, 1.4)
    altered.scanner.negative_channel_matrix = (
        (0.8, 0.2, 0.0),
        (0.1, 0.8, 0.1),
        (0.0, 0.2, 0.8),
    )
    altered.scanner.negative_channel_compensation_enabled = True

    neutral_scan = scan_medium_direct(medium, config, "positive")
    altered_scan = scan_medium_direct(medium, altered, "positive")
    assert np.allclose(neutral_scan.output_srgb, altered_scan.output_srgb)


def test_slide_material_specific_controls_affect_positive_density():
    image = np.linspace(0.10, 0.90, 12 * 12 * 3, dtype=np.float32).reshape(12, 12, 3)
    base = DarkroomConfig()
    base.film.medium_process = "slide"
    base.film.image_polarity = "positive"
    base.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    base.scanner.interpreter_key = "positive_transparency_scan"
    base.scanner.target_medium_process = "slide"
    base.scanner.input_polarity = "positive"
    base.scanner.output_polarity = "positive"
    base.enable_grain = False

    altered = copy.deepcopy(base)
    altered.film.positive_density_contrast = 1.45
    altered.film.positive_density_bias = 0.04
    altered.film.positive_latitude_compression = 0.55
    altered.film.positive_dye_saturation = 1.35

    base_slide = develop_negative(image, base)
    altered_slide = develop_negative(image, altered)

    assert not np.allclose(base_slide.density_cmy, altered_slide.density_cmy)
    assert altered_slide.metadata["positive_density_contrast"] == 1.45
    assert altered_slide.metadata["positive_latitude_compression"] == 0.55
    assert altered_slide.metadata["positive_dye_saturation"] == 1.35


def test_slide_highlight_shoulder_lifts_thin_positive_density():
    image = np.full((10, 12, 3), 0.98, dtype=np.float32)
    base = DarkroomConfig()
    base.film.medium_process = "slide"
    base.film.image_polarity = "positive"
    base.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    base.scanner.interpreter_key = "positive_transparency_scan"
    base.scanner.target_medium_process = "slide"
    base.scanner.input_polarity = "positive"
    base.scanner.output_polarity = "positive"
    base.enable_grain = False

    shouldered = copy.deepcopy(base)
    shouldered.film.positive_highlight_shoulder = 0.70
    shouldered.film.positive_highlight_shoulder_width = 0.28

    base_slide = develop_negative(image, base)
    shouldered_slide = develop_negative(image, shouldered)

    assert float(shouldered_slide.density_cmy.mean()) > float(base_slide.density_cmy.mean())
    assert shouldered_slide.metadata["positive_highlight_shoulder"] == 0.70


def test_slide_midtone_density_compacts_positive_midtones():
    image = np.full((10, 12, 3), 0.50, dtype=np.float32)
    base = DarkroomConfig()
    base.film.medium_process = "slide"
    base.film.image_polarity = "positive"
    base.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    base.scanner.interpreter_key = "positive_transparency_scan"
    base.scanner.target_medium_process = "slide"
    base.scanner.input_polarity = "positive"
    base.scanner.output_polarity = "positive"
    base.enable_grain = False

    compact = copy.deepcopy(base)
    compact.film.positive_midtone_density = 0.50

    base_slide = develop_negative(image, base)
    compact_slide = develop_negative(image, compact)

    assert float(compact_slide.density_cmy.mean()) > float(base_slide.density_cmy.mean())
    assert compact_slide.metadata["positive_midtone_density"] == 0.50


def test_slide_shadow_toe_softens_dense_positive_shadows():
    image = np.full((10, 12, 3), 0.02, dtype=np.float32)
    base = DarkroomConfig()
    base.film.medium_process = "slide"
    base.film.image_polarity = "positive"
    base.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    base.scanner.interpreter_key = "positive_transparency_scan"
    base.scanner.target_medium_process = "slide"
    base.scanner.input_polarity = "positive"
    base.scanner.output_polarity = "positive"
    base.enable_grain = False

    softened = copy.deepcopy(base)
    softened.film.positive_shadow_toe = 0.70
    softened.film.positive_shadow_toe_width = 0.32

    base_slide = develop_negative(image, base)
    softened_slide = develop_negative(image, softened)

    assert float(softened_slide.density_cmy.mean()) < float(base_slide.density_cmy.mean())
    assert softened_slide.metadata["positive_shadow_toe"] == 0.70


def test_positive_projection_white_rolloff_softens_display_not_light_table_raw():
    image = np.linspace(0.80, 1.0, 10 * 12 * 3, dtype=np.float32).reshape(10, 12, 3)
    base = DarkroomConfig()
    base.film.medium_process = "slide"
    base.film.image_polarity = "positive"
    base.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    base.scanner.interpreter_key = "positive_transparency_scan"
    base.scanner.target_medium_process = "slide"
    base.scanner.input_polarity = "positive"
    base.scanner.output_polarity = "positive"
    base.scanner.scan_method = "positive_transparency"
    base.scanner.scan_normalize = False
    base.scanner.light_table_ev = 3.0
    base.scanner.projection_white_softness = 0.0
    base.enable_grain = False

    soft = copy.deepcopy(base)
    soft.scanner.projection_white_softness = 0.35

    slide = develop_negative(image, base)
    hard_scan = scan_negative(slide, base)
    soft_scan = scan_negative(slide, soft)

    assert np.allclose(hard_scan.scanner_raw, soft_scan.scanner_raw)
    assert float(soft_scan.positive_linear.max()) <= float(hard_scan.positive_linear.max())
    assert not np.allclose(hard_scan.positive_linear, soft_scan.positive_linear)


def test_positive_projection_black_adaptation_lifts_display_not_light_table_raw():
    image = np.linspace(0.0, 0.20, 10 * 12 * 3, dtype=np.float32).reshape(10, 12, 3)
    base = DarkroomConfig()
    base.film.medium_process = "slide"
    base.film.image_polarity = "positive"
    base.film.film_base_density_rgb = (0.03, 0.03, 0.035)
    base.scanner.interpreter_key = "positive_transparency_scan"
    base.scanner.target_medium_process = "slide"
    base.scanner.input_polarity = "positive"
    base.scanner.output_polarity = "positive"
    base.scanner.scan_method = "positive_transparency"
    base.scanner.scan_normalize = False
    base.scanner.projection_black_adaptation = 0.0
    base.enable_grain = False

    adapted = copy.deepcopy(base)
    adapted.scanner.projection_black_adaptation = 0.50

    slide = develop_negative(image, base)
    base_scan = scan_negative(slide, base)
    adapted_scan = scan_negative(slide, adapted)

    assert np.allclose(base_scan.scanner_raw, adapted_scan.scanner_raw)
    assert float(adapted_scan.positive_linear.mean()) > float(base_scan.positive_linear.mean())


def test_positive_only_controls_do_not_change_negative_pipeline():
    image = np.linspace(0.1, 0.9, 10 * 12 * 3, dtype=np.float32).reshape(10, 12, 3)
    base = DarkroomConfig()
    base.enable_grain = False
    base.scanner.scan_normalize = False

    altered = copy.deepcopy(base)
    altered.film.positive_midtone_density = 1.0
    altered.film.positive_shadow_toe = 1.0
    altered.film.positive_shadow_toe_width = 0.8
    altered.film.positive_highlight_shoulder = 1.0
    altered.scanner.projection_black_adaptation = 0.9
    altered.scanner.projection_white_softness = 0.9

    base_negative = develop_negative(image, base)
    altered_negative = develop_negative(image, altered)
    base_scan = scan_negative(base_negative, base)
    altered_scan = scan_negative(altered_negative, altered)

    assert np.allclose(base_negative.density_cmy, altered_negative.density_cmy)
    assert np.allclose(base_scan.output_srgb, altered_scan.output_srgb)


def test_process_array_matches_develop_then_scan_without_grain():
    image = np.linspace(0.1, 0.9, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.scanner.scan_normalize = False

    direct = process_array(image, config)
    negative = develop_negative(image, config)
    scanned = scan_negative(negative)

    assert np.allclose(direct, scanned.output_srgb)


def test_full_array_process_uses_reversal_program_output_contract():
    image = np.linspace(0.1, 0.9, 8 * 10 * 3, dtype=np.float32).reshape(8, 10, 3)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.enable_grain = False

    output, stages = process_array_with_stages(image, config)
    medium = develop_negative(image, config)
    scanned = scan_negative(medium)

    assert output.shape == image.shape
    assert np.all(np.isfinite(output))
    assert medium.image_polarity == "positive"
    assert scanned.interpreter_key == "positive_transparency_scan"
    assert np.allclose(output, scanned.output_srgb)
    assert "11_output_srgb" in stages


def test_direct_scan_mode_ignores_recorded_polarity_without_mutating_medium():
    image = np.linspace(0.1, 0.9, 8 * 10 * 3, dtype=np.float32).reshape(8, 10, 3)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.enable_grain = False
    medium = develop_negative(image, config)
    master = medium.density_grain.copy()

    forced_negative = scan_medium_direct(medium, DarkroomConfig(), "negative")
    forced_positive = scan_medium_direct(medium, DarkroomConfig(), "positive")

    assert medium.image_polarity == "positive"
    assert forced_negative.interpreter_key == "negative_scan"
    assert forced_positive.interpreter_key == "positive_transparency_scan"
    assert forced_negative.metadata["recorded_medium_polarity"] == "positive"
    assert forced_negative.metadata["interpretation_selection"] == "manual_direct"
    assert not np.allclose(forced_negative.output_srgb, forced_positive.output_srgb)
    assert np.array_equal(medium.density_grain, master)


def test_direct_scanner_raw_positive_mode_does_not_apply_negative_inversion():
    raw = np.linspace(0.08, 0.92, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    config = DarkroomConfig()
    config.scanner.scan_normalize = False

    negative = scan_scanner_raw_direct(raw, config, "negative")
    positive = scan_scanner_raw_direct(raw, config, "positive")

    assert negative.interpreter_key == "negative_scan"
    assert positive.interpreter_key == "positive_transparency_scan"
    assert positive.metadata["requested_interpretation"] == "positive"
    assert not np.allclose(negative.output_srgb, positive.output_srgb)


def test_default_negative_scan_establishes_display_range_without_mutating_raw():
    base = np.asarray((0.72, 0.48, 0.27), dtype=np.float32)
    scene_density = np.linspace(0.0, 1.4, 1001, dtype=np.float32).reshape(1, -1, 1)
    raw = base.reshape(1, 1, 3) * np.power(10.0, -scene_density)
    original = raw.copy()

    scanned = scan_scanner_raw_direct(
        raw,
        DarkroomConfig(),
        "negative",
        known_base_transmittance_rgb=base,
    )
    luma = (
        scanned.output_srgb[..., 0] * 0.2126
        + scanned.output_srgb[..., 1] * 0.7152
        + scanned.output_srgb[..., 2] * 0.0722
    )

    assert float(np.percentile(luma, 1.0)) < 0.16
    assert float(np.percentile(luma, 99.0)) > 0.83
    assert np.array_equal(raw, original)


def test_direct_scanner_raw_auto_follows_physical_raw_kind():
    raw = np.linspace(0.08, 0.92, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    config = DarkroomConfig()
    config.scanner.scan_normalize = False

    automatic_positive = scan_scanner_raw_direct(
        raw,
        config,
        "auto",
        raw_source_kind="light_table_raw_tiff",
    )
    explicit_positive = scan_scanner_raw_direct(raw, config, "positive")
    automatic_negative = scan_scanner_raw_direct(
        raw,
        config,
        "auto",
        raw_source_kind="scanner_raw_tiff",
    )
    explicit_negative = scan_scanner_raw_direct(raw, config, "negative")

    assert automatic_positive.metadata["requested_interpretation"] == "auto"
    assert automatic_positive.metadata["resolved_interpretation"] == "positive"
    assert automatic_negative.metadata["resolved_interpretation"] == "negative"
    assert np.allclose(automatic_positive.output_srgb, explicit_positive.output_srgb)
    assert np.allclose(automatic_negative.output_srgb, explicit_negative.output_srgb)


@pytest.mark.parametrize(
    ("section", "field"),
    (
        ("look", "print_exposure_ev"),
        ("scanner", "scan_saturation"),
        ("scanner", "negative_backlight_ev"),
        ("scanner", "negative_backlight_temperature_k"),
    ),
)
def test_scan_rejects_non_finite_runtime_settings(section, field):
    raw = np.linspace(0.08, 0.92, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    config = DarkroomConfig()
    setattr(getattr(config, section), field, float("nan"))

    with pytest.raises(ValueError, match="Non-finite"):
        scan_scanner_raw_direct(raw, config, "negative")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("scan_method", "negative-ish"),
        ("print_mapping_mode", "filmic"),
        ("scan_normalize_mode", "automatic"),
    ),
)
def test_scan_rejects_unknown_semantic_modes(field, value):
    raw = np.linspace(0.08, 0.92, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    config = DarkroomConfig()
    setattr(config.scanner, field, value)

    with pytest.raises(ValueError, match=field):
        scan_scanner_raw_direct(raw, config, "negative")


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("scan_base_percentile", -0.1),
        ("scan_base_percentile", 100.1),
        ("negative_backlight_temperature_k", 0.0),
        ("light_table_temperature_k", -1.0),
        ("print_gamma", 0.0),
    ),
)
def test_scan_rejects_physically_invalid_scalar_settings(field, value):
    raw = np.linspace(0.08, 0.92, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    config = DarkroomConfig()
    setattr(config.scanner, field, value)

    with pytest.raises(ValueError, match=field):
        scan_scanner_raw_direct(raw, config, "negative")


def test_scan_rejects_reversed_black_and_white_percentiles():
    raw = np.linspace(0.08, 0.92, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    config = DarkroomConfig()
    config.scanner.scan_black_percentile = 99.0
    config.scanner.scan_white_percentile = 1.0

    with pytest.raises(ValueError, match="scan_black_percentile"):
        scan_scanner_raw_direct(raw, config, "negative")


@pytest.mark.parametrize("shape", ((0, 0, 3), (0, 5, 3), (5, 0, 3), (5, 5), (5, 5, 4)))
def test_development_rejects_empty_or_non_rgb_arrays_clearly(shape):
    with pytest.raises(ValueError, match="shape|non-zero"):
        develop_negative(np.zeros(shape, dtype=np.float32), DarkroomConfig())


def test_development_rejects_non_finite_input_pixels():
    image = np.zeros((5, 6, 3), dtype=np.float32)
    image[2, 3, 1] = np.nan

    with pytest.raises(ValueError, match="finite"):
        develop_negative(image, DarkroomConfig())


@pytest.mark.parametrize(
    ("section", "field"),
    (
        ("film", "hd_toe_width"),
        ("chemistry", "time_min"),
        ("look", "exposure_ev"),
        ("processing", "grain_work_long_edge"),
    ),
)
def test_development_rejects_non_finite_runtime_settings(section, field):
    config = DarkroomConfig()
    setattr(getattr(config, section), field, float("nan"))

    with pytest.raises(ValueError, match="Non-finite"):
        develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)


def test_development_rejects_malformed_material_vectors_and_density_order():
    config = DarkroomConfig()
    config.film.layer_sensitivity_matrix = ((1.0, 0.0), (0.0, 1.0))
    with pytest.raises(ValueError, match="shape"):
        develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)

    config = DarkroomConfig()
    config.film.density_max = config.film.density_min
    with pytest.raises(ValueError, match="greater"):
        develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("random_seed", 1.5, "random_seed"),
        ("seed_strategy", "unknown", "seed_strategy"),
    ),
)
def test_development_rejects_invalid_reproducibility_contract(field, value, message):
    config = DarkroomConfig()
    setattr(config, field, value)

    with pytest.raises(ValueError, match=message):
        develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), config)


def test_invalid_scanner_settings_do_not_block_or_change_development():
    image = np.full((5, 6, 3), 0.4, dtype=np.float32)
    baseline = DarkroomConfig()
    baseline.enable_grain = False
    invalid_scan = copy.deepcopy(baseline)
    invalid_scan.scanner.scan_saturation = float("nan")

    expected = develop_negative(image, baseline, rng=np.random.default_rng(7))
    actual = develop_negative(image, invalid_scan, rng=np.random.default_rng(7))

    assert np.array_equal(actual.density_cmy, expected.density_cmy)
    assert np.array_equal(actual.density_grain, expected.density_grain)


def test_scan_rejects_non_finite_or_misaligned_developed_medium_arrays():
    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), DarkroomConfig())
    medium.density_grain[1, 2, 0] = np.inf
    with pytest.raises(ValueError, match="finite"):
        scan_medium_direct(medium, DarkroomConfig(), "negative")

    medium = develop_negative(np.full((5, 6, 3), 0.4, dtype=np.float32), DarkroomConfig())
    medium.density_cmy = medium.density_cmy[:-1]
    with pytest.raises(ValueError, match="same shape"):
        scan_medium_direct(medium, DarkroomConfig(), "positive")


def test_full_workflow_can_manually_override_scan_interpretation_only():
    image = np.linspace(0.1, 0.9, 8 * 10 * 3, dtype=np.float32).reshape(8, 10, 3)
    automatic = DarkroomConfig()
    automatic.enable_grain = False
    manual = copy.deepcopy(automatic)
    manual.scanner.interpretation_mode = "positive"

    auto_output = process_array(image, automatic)
    manual_output = process_array(image, manual)

    assert not np.allclose(auto_output, manual_output)


def test_bw_negative_scan_ignores_rgb_color_bias_controls():
    image = np.linspace(0.1, 0.9, 12 * 16, dtype=np.float32)
    image = np.repeat(image.reshape(12, 16, 1), 3, axis=-1)
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.enable_grain = False
    config.scanner.scan_normalize = False
    config.scanner.print_color_shift = (0.12, -0.08, 0.06)
    config.scanner.highlight_color_bias = (0.8, 1.2, 0.7)

    negative = develop_negative(image, config)
    scanned = scan_negative(negative, config)

    assert np.allclose(scanned.positive_linear[..., 0], scanned.positive_linear[..., 1])
    assert np.allclose(scanned.positive_linear[..., 0], scanned.positive_linear[..., 2])


def test_bw_development_does_not_take_ownership_of_scanner_settings():
    image = np.linspace(0.1, 0.9, 8 * 10, dtype=np.float32)
    image = np.repeat(image.reshape(8, 10, 1), 3, axis=-1)
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.film.color_process = "monochrome"
    config.scanner.print_color_shift = (0.12, -0.08, 0.06)
    config.scanner.scanner_light_color = (0.85, 1.0, 1.15)

    medium = develop_negative(image, config)
    develop_runtime = medium.metadata["runtime_config"]
    scanned = scan_negative(medium)

    assert develop_runtime.scanner.print_color_shift == config.scanner.print_color_shift
    assert develop_runtime.scanner.scanner_light_color == config.scanner.scanner_light_color
    assert scanned.metadata["runtime_config"].scanner.print_color_shift == (0.0, 0.0, 0.0)


def test_develop_recipe_changes_negative_density_state():
    image = np.linspace(0.08, 0.92, 14 * 18 * 3, dtype=np.float32).reshape(14, 18, 3)
    base = DarkroomConfig()
    base.enable_grain = False
    base.scanner.scan_normalize = False

    altered = copy.deepcopy(base)
    altered.chemistry.developer_type = "push"
    altered.chemistry.time_min = 13.5
    altered.chemistry.temperature_c = 24.0
    altered.chemistry.concentration = 1.35

    negative_a = develop_negative(image, base)
    negative_b = develop_negative(image, altered)
    scanned_a = scan_negative(negative_a, base)
    scanned_b = scan_negative(negative_b, altered)

    assert not np.allclose(negative_a.density_cmy, negative_b.density_cmy)
    assert not np.allclose(negative_a.density_grain, negative_b.density_grain)
    assert not np.allclose(scanned_a.output_srgb, scanned_b.output_srgb)


def test_scan_interpretation_does_not_mutate_negative_state():
    image = np.linspace(0.06, 0.94, 13 * 17 * 3, dtype=np.float32).reshape(13, 17, 3)
    develop_config = DarkroomConfig()
    develop_config.enable_grain = False
    develop_config.scanner.scan_normalize = False
    negative = develop_negative(image, develop_config)

    density_cmy_before = negative.density_cmy.copy()
    density_grain_before = negative.density_grain.copy()
    stored_config = negative.metadata["runtime_config"]
    total_density_before = negative_total_density_rgb(density_grain_before, stored_config.film)

    scan_a = DarkroomConfig()
    scan_a.scanner.scan_normalize = False
    scan_a.look.print_contrast = 0.85
    scan_a.look.print_exposure_ev = -0.25

    scan_b = DarkroomConfig()
    scan_b.scanner.scan_normalize = False
    scan_b.look.print_contrast = 1.55
    scan_b.look.print_exposure_ev = 0.45
    scan_b.scanner.print_color_shift = (0.08, -0.04, -0.08)

    scanned_a = scan_negative(negative, scan_a)
    scanned_b = scan_negative(negative, scan_b)
    total_density_after = negative_total_density_rgb(negative.density_grain, stored_config.film)

    assert np.allclose(negative.density_cmy, density_cmy_before)
    assert np.allclose(negative.density_grain, density_grain_before)
    assert np.allclose(total_density_after, total_density_before)
    assert not np.allclose(scanned_a.output_srgb, scanned_b.output_srgb)


def test_rescan_with_stored_runtime_does_not_apply_film_look_twice():
    image = np.linspace(0.05, 0.95, 12 * 15 * 3, dtype=np.float32).reshape(12, 15, 3)
    develop_config = DarkroomConfig()
    develop_config.enable_grain = False
    develop_config.scanner.scan_normalize = False
    develop_config.look.look_strength = 1.45
    develop_config.look.negative_contrast = 1.35
    develop_config.look.saturation_multiplier = 1.25

    negative = develop_negative(image, develop_config)
    stored_runtime = negative.metadata["runtime_config"]
    stored_gamma = np.asarray(stored_runtime.film.hd_gamma, dtype=np.float32)
    stored_dye = np.asarray(stored_runtime.film.dye_absorption_matrix, dtype=np.float32)

    scan_config = DarkroomConfig()
    scan_config.scanner.scan_normalize = False
    scan_config.look.look_strength = 2.0
    scan_config.look.negative_contrast = 2.0
    scan_config.look.saturation_multiplier = 1.8
    scan_config.look.print_contrast = 1.4
    scan_config.look.print_exposure_ev = 0.2

    scanned = scan_negative(negative, scan_config)
    scan_runtime = scanned.metadata["runtime_config"]

    assert np.allclose(scan_runtime.film.hd_gamma, stored_gamma)
    assert np.allclose(scan_runtime.film.dye_absorption_matrix, stored_dye)


def test_light_leak_enters_negative_formation_before_scan():
    image = np.zeros((18, 24, 3), dtype=np.float32) + 0.12
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.scanner.scan_normalize = False

    leaked = copy.deepcopy(clean)
    leaked.chemistry.light_leak_strength = 0.85

    negative_clean = develop_negative(image, clean, rng=np.random.default_rng(9))
    negative_leaked = develop_negative(image, leaked, rng=np.random.default_rng(9))

    assert negative_leaked.metadata["has_light_leak_map"] is True
    assert negative_leaked.metadata["light_leak_template"] == "auto"
    assert negative_leaked.metadata["light_leak_pattern"]["template_key"] == "local_edge"
    assert not np.allclose(negative_clean.after_halation, negative_leaked.after_halation)
    assert not np.allclose(negative_clean.density_cmy, negative_leaked.density_cmy)
    assert negative_leaked.density_cmy.mean() > negative_clean.density_cmy.mean()


def test_exposure_occlusion_enters_after_inverse_gamma_before_develop_and_scan_is_read_only():
    image = np.full((36, 48, 3), 0.46, dtype=np.float32)
    original = image.copy()
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.enable_mtf = False
    clean.scanner.scan_normalize = False
    dirty = copy.deepcopy(clean)
    dirty.chemistry.exposure_occlusion_dust = 0.9
    dirty.chemistry.exposure_occlusion_hair = 0.8

    medium_clean = develop_negative(image, clean, rng=np.random.default_rng(13))
    medium_dirty = develop_negative(image, dirty, rng=np.random.default_rng(13))
    optical_before_scan = medium_dirty.optical_density_rgb.copy()

    assert np.array_equal(image, original)
    assert medium_dirty.metadata["has_exposure_occlusion_map"] is True
    assert medium_dirty.metadata["accident_stages"]["exposure_occlusion"] == "pre_latent_exposure"
    assert float(np.mean(medium_dirty.linear_input)) < float(np.mean(medium_clean.linear_input))
    assert float(np.mean(medium_dirty.density_cmy)) < float(np.mean(medium_clean.density_cmy))
    scan_negative(medium_dirty)
    assert np.array_equal(medium_dirty.optical_density_rgb, optical_before_scan)


def test_zero_exposure_occlusion_keeps_historical_linear_path():
    image = np.linspace(0.08, 0.7, 20 * 28 * 3, dtype=np.float32).reshape(20, 28, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    a = develop_negative(image, config, rng=np.random.default_rng(4))
    b = develop_negative(image, copy.deepcopy(config), rng=np.random.default_rng(4))
    assert a.metadata["has_exposure_occlusion_map"] is False
    assert np.array_equal(a.linear_input, b.linear_input)
    assert np.array_equal(a.density_cmy, b.density_cmy)


def test_explicit_light_leak_template_is_recorded_and_scan_remains_read_only():
    image = np.full((37, 53, 3), 0.14, dtype=np.float32)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    config.scanner.scan_normalize = False
    config.chemistry.light_leak_strength = 0.75
    config.chemistry.light_leak_template = "cone"

    medium = develop_negative(image, config, rng=np.random.default_rng(91))
    pattern = medium.metadata["light_leak_pattern"]
    optical_before_scan = medium.optical_density_rgb.copy()

    assert medium.metadata["light_leak_template"] == "cone"
    assert pattern["template_key"] == "cone"
    assert pattern["texture_seed"] >= 0
    assert len(pattern["center_xy"]) == 2
    scan_negative(medium)
    assert np.array_equal(medium.optical_density_rgb, optical_before_scan)


def test_unknown_light_leak_template_is_rejected_before_formation():
    config = DarkroomConfig()
    config.chemistry.light_leak_template = "scanner_glow"

    with pytest.raises(ValueError, match="develop.light_leak_template"):
        develop_negative(
            np.full((7, 9, 3), 0.5, dtype=np.float32),
            config,
            rng=np.random.default_rng(93),
        )


def test_invalid_exposure_occlusion_amount_is_rejected_before_formation():
    config = DarkroomConfig()
    config.chemistry.exposure_occlusion_hair = 1.4
    with pytest.raises(ValueError, match="exposure_occlusion_hair"):
        develop_negative(
            np.full((7, 9, 3), 0.5, dtype=np.float32),
            config,
            rng=np.random.default_rng(93),
        )


def test_light_leak_composition_metadata_and_manual_placement_stay_pre_latent():
    image = np.full((33, 49, 3), 0.12, dtype=np.float32)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    config.scanner.scan_normalize = False
    config.chemistry.light_leak_strength = 0.8
    config.chemistry.light_leak_template = "cone"
    config.chemistry.light_leak_secondary_template = "perimeter"
    config.chemistry.light_leak_mix = "max"
    config.chemistry.light_leak_placement = "manual"
    config.chemistry.light_leak_entry_side = "left"
    config.chemistry.light_leak_identity_seed = 77

    medium = develop_negative(image, config, rng=np.random.default_rng(3))
    pattern = medium.metadata["light_leak_pattern"]
    secondary = medium.metadata["light_leak_secondary_pattern"]
    optical_before_scan = medium.optical_density_rgb.copy()

    assert medium.metadata["light_leak_secondary_template"] == "perimeter"
    assert medium.metadata["light_leak_mix"] == "max"
    assert medium.metadata["light_leak_placement"] == "manual"
    assert pattern["template_key"] == "cone"
    assert pattern["side_weights"][0] == 1.0
    assert secondary["template_key"] == "perimeter"
    scan_negative(medium)
    assert np.array_equal(medium.optical_density_rgb, optical_before_scan)


def test_replace_mix_without_mask_path_is_rejected_before_formation():
    config = DarkroomConfig()
    config.chemistry.light_leak_strength = 0.4
    config.chemistry.light_leak_mix = "replace"

    with pytest.raises(ValueError, match="light_leak_mix"):
        develop_negative(
            np.full((7, 9, 3), 0.5, dtype=np.float32),
            config,
            rng=np.random.default_rng(4),
        )


def test_halation_sensitivity_temporarily_changes_effective_threshold():
    image = np.zeros((18, 24, 3), dtype=np.float32) + 0.18
    image[9, 12] = 0.86
    config = DarkroomConfig()
    config.enable_grain = False
    config.film.halation_threshold = 0.82
    config.look.halation_sensitivity = 1.0
    original_threshold = config.film.halation_threshold

    negative = develop_negative(image, config, rng=np.random.default_rng(13))
    runtime = negative.metadata["runtime_config"]

    assert config.film.halation_threshold == original_threshold
    assert runtime.film.halation_threshold < original_threshold
    assert negative.after_halation.mean() > negative.after_mtf.mean()


def test_layer_selective_halation_targets_material_layers_not_rgb_history():
    image = np.full((65, 97, 3), 0.08, dtype=np.float32)
    image[31:34, 47:50] = 1.0
    base = DarkroomConfig()
    base.enable_mtf = False
    base.enable_grain = False
    base.film.halation_strength = 0.50
    base.film.halation_threshold = 0.50
    base.film.halation_area_suppression = 0.0
    base.film.halation_peak_threshold = 0.01
    base.film.halation_return_model = "layer_selective"

    clean = copy.deepcopy(base)
    clean.enable_halation = False
    red_layer = copy.deepcopy(base)
    red_layer.film.halation_layer_return_weights = (1.0, 0.0, 0.0)
    blue_layer = copy.deepcopy(base)
    blue_layer.film.halation_layer_return_weights = (0.0, 0.0, 1.0)

    clean_medium = develop_negative(image, clean)
    red_medium = develop_negative(image, red_layer)
    blue_medium = develop_negative(image, blue_layer)
    red_delta = red_medium.density_cmy - clean_medium.density_cmy
    blue_delta = blue_medium.density_cmy - clean_medium.density_cmy

    assert float(red_delta[..., 0].max()) > 0.0
    assert np.count_nonzero(red_delta[..., 1:]) == 0
    assert float(blue_delta[..., 2].max()) > 0.0
    assert np.count_nonzero(blue_delta[..., :2]) == 0
    assert np.array_equal(red_medium.after_halation, red_medium.after_mtf)
    assert np.array_equal(blue_medium.after_halation, blue_medium.after_mtf)
    contract = red_medium.metadata["film_process_model"]["halation_formation"]
    assert contract["return_model"] == "layer_selective"
    assert contract["stage"] == "pre_latent_exposure"


def test_layer_selective_halation_is_exact_across_material_tiling_and_scan_read_only():
    image = np.full((67, 101, 3), 0.07, dtype=np.float32)
    image[30:34, 48:52] = (1.0, 0.92, 0.80)
    untiled = DarkroomConfig()
    untiled.enable_mtf = False
    untiled.enable_grain = False
    untiled.film.halation_return_model = "layer_selective"
    untiled.film.halation_layer_return_weights = (1.0, 0.35, 0.08)
    untiled.film.halation_spread_scale_weights = (0.2, 0.6, 0.2)
    untiled.film.halation_outer_radius = 0.05
    untiled.film.halation_threshold = 0.48
    untiled.film.halation_area_suppression = 0.0
    untiled.film.halation_peak_threshold = 0.01
    untiled.processing.material_tile_rows = 0

    tiled = copy.deepcopy(untiled)
    tiled.processing.material_tile_rows = 7
    tiled.processing.material_tile_threshold_megapixels = 0.0001

    full_medium = develop_negative(image, untiled)
    tiled_medium = develop_negative(image, tiled)

    assert np.array_equal(tiled_medium.density_cmy, full_medium.density_cmy)
    assert np.array_equal(
        tiled_medium.optical_density_rgb,
        full_medium.optical_density_rgb,
    )
    before = tiled_medium.optical_density_rgb.copy()
    scan_negative(tiled_medium)
    assert np.array_equal(tiled_medium.optical_density_rgb, before)


def test_invalid_layer_selective_halation_configuration_fails_closed():
    image = np.full((8, 11, 3), 0.2, dtype=np.float32)
    invalid_mode = DarkroomConfig()
    invalid_mode.film.halation_return_model = "rgb_guess"
    with pytest.raises(ValueError, match="halation_return_model"):
        develop_negative(image, invalid_mode)

    invalid_weight = DarkroomConfig()
    invalid_weight.film.halation_return_model = "layer_selective"
    invalid_weight.film.halation_layer_return_weights = (1.0, -0.1, 0.0)
    with pytest.raises(ValueError, match="layer_return_weights"):
        develop_negative(image, invalid_weight)

    zero_weight = DarkroomConfig()
    zero_weight.enable_mtf = False
    zero_weight.enable_grain = False
    zero_weight.film.halation_return_model = "layer_selective"
    zero_weight.film.halation_layer_return_weights = (0.0, 0.0, 0.0)
    disabled = copy.deepcopy(zero_weight)
    disabled.enable_halation = False
    zero_medium = develop_negative(image, zero_weight)
    disabled_medium = develop_negative(image, disabled)
    assert np.array_equal(zero_medium.density_cmy, disabled_medium.density_cmy)
    assert zero_medium.metadata["film_process_model"]["halation_formation"][
        "applied"
    ] is False

    invalid_scale = DarkroomConfig()
    invalid_scale.film.halation_return_model = "layer_selective"
    invalid_scale.film.halation_spread_scale_weights = (0.2, -0.1, 0.9)
    with pytest.raises(ValueError, match="spread_scale_weights"):
        develop_negative(image, invalid_scale)

    narrow_wide = DarkroomConfig()
    narrow_wide.film.halation_return_model = "layer_selective"
    narrow_wide.film.halation_spread_scale_weights = (0.0, 0.5, 0.5)
    narrow_wide.film.halation_outer_radius = 0.01
    with pytest.raises(ValueError, match="wide halation"):
        develop_negative(image, narrow_wide)


def test_monochrome_layer_selective_halation_remains_neutral():
    image = np.full((41, 59, 3), 0.08, dtype=np.float32)
    image[19:22, 28:31] = 1.0
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.film.color_process = "monochrome"
    config.chemistry.program_key = "bw_negative"
    config.enable_mtf = False
    config.enable_grain = False
    config.film.halation_return_model = "layer_selective"
    config.film.halation_layer_return_weights = (1.0, 0.35, 0.08)
    config.film.halation_threshold = 0.48
    config.film.halation_area_suppression = 0.0
    config.film.halation_peak_threshold = 0.01

    medium = develop_negative(image, config)

    assert np.array_equal(medium.density_cmy[..., 0], medium.density_cmy[..., 1])
    assert np.array_equal(medium.density_cmy[..., 1], medium.density_cmy[..., 2])
    assert medium.metadata["film_process_model"]["halation_formation"][
        "return_model"
    ] == "layer_selective"


def test_chemical_stain_and_uneven_development_modify_density_master():
    image = np.linspace(0.08, 0.92, 18 * 24 * 3, dtype=np.float32).reshape(18, 24, 3)
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.scanner.scan_normalize = False

    ruined = copy.deepcopy(clean)
    ruined.chemistry.chemical_stain = 0.80
    ruined.chemistry.uneven_development = 0.70

    negative_clean = develop_negative(image, clean, rng=np.random.default_rng(11))
    negative_ruined = develop_negative(image, ruined, rng=np.random.default_rng(11))

    assert "chemical_stain" in negative_ruined.metadata["accident_maps"]
    assert "uneven_development" in negative_ruined.metadata["accident_maps"]
    assert not np.allclose(negative_clean.density_grain, negative_ruined.density_grain)
    assert np.isfinite(negative_ruined.density_grain).all()
    assert negative_ruined.density_grain.min() >= 0.0


def test_uneven_development_changes_pool_formation_but_stain_does_not():
    image = np.linspace(0.08, 0.92, 18 * 24 * 3, dtype=np.float32).reshape(18, 24, 3)
    clean = DarkroomConfig()
    clean.chemistry.program_key = "color_negative"
    clean.enable_grain = False
    clean.enable_halation = False
    uneven = copy.deepcopy(clean)
    uneven.chemistry.uneven_development = 0.90
    stain = copy.deepcopy(clean)
    stain.chemistry.chemical_stain = 0.90

    clean_medium = develop_negative(image, clean, rng=np.random.default_rng(17))
    uneven_medium = develop_negative(image, uneven, rng=np.random.default_rng(17))
    stain_medium = develop_negative(image, stain, rng=np.random.default_rng(17))
    clean_model = clean_medium.metadata["film_process_model"]
    uneven_model = uneven_medium.metadata["film_process_model"]
    stain_model = stain_medium.metadata["film_process_model"]

    assert uneven_model["process_trace"][0]["reacted_amount"] != clean_model[
        "process_trace"
    ][0]["reacted_amount"]
    assert not np.allclose(uneven_medium.density_cmy, clean_medium.density_cmy)
    assert np.isclose(
        stain_model["process_trace"][0]["reacted_amount"],
        clean_model["process_trace"][0]["reacted_amount"],
        atol=1e-6,
    )
    assert np.allclose(stain_medium.density_cmy, clean_medium.density_cmy, atol=1e-7)
    assert not np.array_equal(stain_medium.density_grain, clean_medium.density_grain)


def test_density_accidents_are_amplified_by_bad_development_state():
    image = np.linspace(0.10, 0.80, 20 * 28 * 3, dtype=np.float32).reshape(20, 28, 3)
    base = DarkroomConfig()
    base.enable_grain = False
    base.enable_halation = False
    base.scanner.scan_normalize = False
    base.chemistry.chemical_stain = 0.28
    base.chemistry.uneven_development = 0.28

    bad = copy.deepcopy(base)
    bad.chemistry.time_min = 2.0
    bad.chemistry.temperature_c = 30.0
    bad.chemistry.concentration = 2.5
    bad.chemistry.agitation = 0.0
    bad.chemistry.developer_exhaustion = 0.7
    bad.chemistry.fixer_exhaustion = 0.8

    negative_base = develop_negative(image, base, rng=np.random.default_rng(21))
    negative_bad = develop_negative(image, bad, rng=np.random.default_rng(21))

    delta_base = float(np.mean(np.abs(negative_base.density_grain - negative_base.density_cmy)))
    delta_bad = float(np.mean(np.abs(negative_bad.density_grain - negative_bad.density_cmy)))

    assert delta_bad > delta_base * 1.25
    assert negative_bad.metadata["accident_stages"] == {
        "exposure_occlusion": "pre_latent_exposure",
        "edge_imprint": "pre_latent_exposure",
        "light_leak": "pre_latent_exposure",
        "uneven_development": "development_formation",
        "chemical_stain": "post_process_component_density",
        "silver_plating": "post_process_surface_silver_density",
        "reticulation": "post_process_emulsion_structure_density",
        "dye_damage": "process_operator_material_dye_pool",
    }


@pytest.mark.parametrize("monochrome", (False, True))
def test_reticulation_changes_emulsion_optics_without_changing_pools_base_or_scan(monochrome):
    image = np.linspace(0.12, 0.88, 31 * 43 * 3, dtype=np.float32).reshape(
        31,
        43,
        3,
    )
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.scanner.scan_normalize = False
    if monochrome:
        clean.mode = "bw_negative"
        clean.film.color_process = "monochrome"
        clean.chemistry.program_key = "bw_negative"
    damaged = copy.deepcopy(clean)
    damaged.chemistry.reticulation_strength = 0.85

    medium_clean = develop_negative(image, clean, rng=np.random.default_rng(71))
    medium_damaged = develop_negative(
        image,
        damaged,
        rng=np.random.default_rng(71),
    )
    optical_before_scan = medium_damaged.optical_density_rgb.copy()
    pools_clean = medium_clean.metadata["film_process_model"]["pool_totals"]
    pools_damaged = medium_damaged.metadata["film_process_model"]["pool_totals"]

    assert pools_damaged == pools_clean
    assert (
        medium_damaged.clear_base_optical_density_rgb
        == medium_clean.clear_base_optical_density_rgb
    )
    assert "reticulation" in medium_damaged.metadata["accident_maps"]
    reticulation_contract = medium_damaged.metadata["film_process_model"][
        "emulsion_damage"
    ]["reticulation"]
    assert reticulation_contract["stage"] == "post_process_emulsion_structure_density"
    assert reticulation_contract["scanner_owned"] is False
    delta = medium_damaged.optical_density_rgb - medium_clean.optical_density_rgb
    assert float(np.mean(np.abs(delta))) > 1e-4
    assert abs(float(np.mean(delta, dtype=np.float64))) < 2e-7
    assert np.allclose(delta[..., 0], delta[..., 1], atol=2e-7, rtol=0.0)
    assert np.allclose(delta[..., 1], delta[..., 2], atol=2e-7, rtol=0.0)
    base = np.asarray(
        medium_damaged.clear_base_optical_density_rgb,
        dtype=np.float32,
    )
    assert np.all(medium_damaged.optical_density_rgb >= base - np.float32(2e-7))

    scan_negative(medium_damaged)
    assert np.array_equal(medium_damaged.optical_density_rgb, optical_before_scan)


def test_explicit_dye_damage_reduces_only_formed_color_dye_and_scan_is_read_only():
    image = np.linspace(0.10, 0.90, 29 * 41 * 3, dtype=np.float32).reshape(
        29,
        41,
        3,
    )
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.scanner.scan_normalize = False
    damaged = copy.deepcopy(clean)
    damaged.chemistry.dye_damage_strength = 0.40

    medium_clean = develop_negative(image, clean, rng=np.random.default_rng(73))
    medium_damaged = develop_negative(
        image,
        damaged,
        rng=np.random.default_rng(73),
    )
    pools_clean = medium_clean.metadata["film_process_model"]["pool_totals"]
    pools_damaged = medium_damaged.metadata["film_process_model"]["pool_totals"]
    explicit_steps = [
        step
        for step in medium_damaged.metadata["film_process_model"]["process_trace"]
        if step["dye_destruction_basis"] == "explicit"
    ]

    assert len(explicit_steps) == 1
    assert explicit_steps[0]["action"] == "destroy_dye"
    assert explicit_steps[0]["reacted_amount"] > 0.0
    assert pools_damaged["dye"] < pools_clean["dye"]
    for pool in (
        "halide",
        "developable_halide",
        "inactive_halide",
        "metallic_silver",
        "coupler",
        "bleached_halide",
        "auxiliary_remaining",
        "masking_coupler_remaining",
    ):
        assert pools_damaged[pool] == pytest.approx(pools_clean[pool], abs=1e-7)
    assert (
        medium_damaged.clear_base_optical_density_rgb
        == medium_clean.clear_base_optical_density_rgb
    )
    assert not np.allclose(
        medium_damaged.optical_density_rgb,
        medium_clean.optical_density_rgb,
    )

    optical_before_scan = medium_damaged.optical_density_rgb.copy()
    scan_negative(medium_damaged)
    assert np.array_equal(medium_damaged.optical_density_rgb, optical_before_scan)


def test_explicit_dye_damage_rejects_non_color_material_programs():
    image = np.full((9, 13, 3), 0.5, dtype=np.float32)
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.film.color_process = "monochrome"
    config.chemistry.program_key = "bw_negative"
    config.chemistry.dye_damage_strength = 0.2

    with pytest.raises(
        ValueError,
        match="explicit dye damage requires a unified color material-pool program",
    ):
        develop_negative(image, config, rng=np.random.default_rng(79))

    legacy = DarkroomConfig()
    legacy.chemistry.program_key = "legacy_density"
    legacy.chemistry.dye_damage_strength = 0.2
    with pytest.raises(
        ValueError,
        match="explicit dye damage requires a unified color material-pool program",
    ):
        develop_negative(image, legacy, rng=np.random.default_rng(79))


def test_process_variation_changes_negative_state_but_stays_seed_reproducible():
    image = np.linspace(0.08, 0.92, 18 * 24 * 3, dtype=np.float32).reshape(18, 24, 3)
    base = DarkroomConfig()
    base.enable_grain = False
    base.enable_halation = False
    base.scanner.scan_normalize = False

    varied = copy.deepcopy(base)
    varied.chemistry.process_variation = 0.80

    clean_negative = develop_negative(image, base, rng=np.random.default_rng(31))
    varied_a = develop_negative(image, varied, rng=np.random.default_rng(31))
    varied_b = develop_negative(image, varied, rng=np.random.default_rng(31))

    runtime = varied_a.metadata["runtime_config"]
    variation = varied_a.metadata["process_variation"]

    assert variation["applied"] is True
    assert runtime.chemistry.process_variation == 0.80
    assert runtime.chemistry.time_min != varied.chemistry.time_min
    assert runtime.scanner.scan_normalize_strength == varied.scanner.scan_normalize_strength
    assert runtime.scanner.print_color_shift == varied.scanner.print_color_shift
    assert runtime.look.print_exposure_ev == varied.look.print_exposure_ev
    assert "print_exposure_delta" not in variation
    assert "scan_normalize_strength_delta" not in variation
    assert "print_color_shift_delta" not in variation
    assert not np.allclose(clean_negative.density_cmy, varied_a.density_cmy)
    assert np.allclose(varied_a.density_cmy, varied_b.density_cmy)
    assert np.allclose(varied_a.density_grain, varied_b.density_grain)


def test_process_variation_does_not_invent_disabled_spatial_accidents():
    image = np.linspace(0.08, 0.92, 18 * 24 * 3, dtype=np.float32).reshape(18, 24, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    config.chemistry.process_variation = 1.0
    config.chemistry.chemical_stain = 0.0
    config.chemistry.uneven_development = 0.0
    config.chemistry.reticulation_strength = 0.0
    config.chemistry.dye_damage_strength = 0.0

    negative = develop_negative(image, config, rng=np.random.default_rng(31))
    runtime = negative.metadata["runtime_config"]
    variation = negative.metadata["process_variation"]

    assert runtime.chemistry.chemical_stain == 0.0
    assert runtime.chemistry.uneven_development == 0.0
    assert runtime.chemistry.reticulation_strength == 0.0
    assert runtime.chemistry.dye_damage_strength == 0.0
    assert runtime.chemistry.exposure_occlusion_dust == 0.0
    assert variation["chemical_stain_delta"] == 0.0
    assert variation["uneven_development_delta"] == 0.0
    assert variation["reticulation_strength_delta"] == 0.0
    assert variation["dye_damage_strength_delta"] == 0.0
    assert variation["exposure_occlusion_dust_delta"] == 0.0
    assert "chemical_stain" not in negative.metadata["accident_maps"]
    assert "uneven_development" not in negative.metadata["accident_maps"]
    assert "reticulation" not in negative.metadata["accident_maps"]


def test_process_variation_only_jitters_explicit_accident_tendencies():
    image = np.linspace(0.08, 0.92, 18 * 24 * 3, dtype=np.float32).reshape(18, 24, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    config.chemistry.process_variation = 1.0
    config.chemistry.chemical_stain = 0.35
    config.chemistry.uneven_development = 0.45
    config.chemistry.reticulation_strength = 0.55
    config.chemistry.dye_damage_strength = 0.50

    negative = develop_negative(image, config, rng=np.random.default_rng(31))
    runtime = negative.metadata["runtime_config"]

    assert runtime.chemistry.chemical_stain > 0.0
    assert runtime.chemistry.uneven_development > 0.0
    assert runtime.chemistry.reticulation_strength > 0.0
    assert runtime.chemistry.dye_damage_strength > 0.0
    assert runtime.chemistry.chemical_stain != config.chemistry.chemical_stain
    assert runtime.chemistry.uneven_development != config.chemistry.uneven_development
    assert (
        runtime.chemistry.reticulation_strength
        != config.chemistry.reticulation_strength
    )
    assert runtime.chemistry.dye_damage_strength != config.chemistry.dye_damage_strength
    assert "chemical_stain" in negative.metadata["accident_maps"]
    assert "uneven_development" in negative.metadata["accident_maps"]
    assert "reticulation" in negative.metadata["accident_maps"]


def test_process_array_uses_varied_runtime_for_scan_interpretation():
    image = np.linspace(0.06, 0.94, 16 * 20 * 3, dtype=np.float32).reshape(16, 20, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    config.scanner.scan_normalize = False
    config.chemistry.process_variation = 0.75

    direct = process_array(image, copy.deepcopy(config), rng=np.random.default_rng(41))
    negative = develop_negative(image, copy.deepcopy(config), rng=np.random.default_rng(41))
    scanned = scan_negative(negative)

    assert negative.metadata["process_variation"]["applied"] is True
    assert np.allclose(direct, scanned.output_srgb)


def test_scan_interpretation_cannot_mutate_or_replace_developed_material():
    image = np.linspace(0.08, 0.92, 12 * 15 * 3, dtype=np.float32).reshape(12, 15, 3)
    develop_config = DarkroomConfig()
    develop_config.enable_grain = False
    medium = develop_negative(image, develop_config)
    medium.metadata.pop("runtime_config", None)

    density_cmy_before = medium.density_cmy.copy()
    density_grain_before = medium.density_grain.copy()
    contract_before = copy.deepcopy(medium.metadata["film_process_model"])

    neutral_scan = DarkroomConfig()
    neutral_scan.scanner.scan_normalize = False
    hostile_material_scan = copy.deepcopy(neutral_scan)
    hostile_material_scan.film.film_base_density_rgb = (2.0, 0.0, 0.0)
    hostile_material_scan.film.dye_absorption_matrix = (
        (0.0, 0.0, 2.5),
        (2.5, 0.0, 0.0),
        (0.0, 2.5, 0.0),
    )
    hostile_material_scan.film.base_dye_interaction_strength = 1.0
    hostile_material_scan.film.base_dye_interaction_matrix = (
        (0.0, 0.0, 1.0),
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
    )
    hostile_material_scan.film.density_min = (1.5, 1.5, 1.5)

    neutral = scan_negative(medium, neutral_scan)
    hostile = scan_negative(medium, hostile_material_scan)

    # Scanner presets may change interpretation, never the stored material optics.
    assert np.allclose(neutral.output_srgb, hostile.output_srgb)
    assert np.array_equal(medium.density_cmy, density_cmy_before)
    assert np.array_equal(medium.density_grain, density_grain_before)
    assert medium.metadata["film_process_model"] == contract_before
    assert hostile_material_scan.film.film_base_density_rgb == (2.0, 0.0, 0.0)

    brighter_scan = copy.deepcopy(neutral_scan)
    brighter_scan.look.print_exposure_ev = 1.0
    brighter = scan_negative(medium, brighter_scan)
    assert not np.allclose(neutral.output_srgb, brighter.output_srgb)
    assert np.array_equal(medium.density_grain, density_grain_before)


def test_explicit_bw_negative_program_uses_reduced_silver_pool_pixel_path():
    image = np.linspace(0.05, 0.95, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    legacy = DarkroomConfig()
    legacy.chemistry.program_key = "legacy_density"
    legacy.mode = "bw_negative"
    legacy.film.color_process = "monochrome"
    legacy.enable_grain = False
    legacy.enable_halation = False

    reduced = copy.deepcopy(legacy)
    reduced.chemistry.program_key = "bw_negative"

    legacy_medium = develop_negative(image, legacy)
    reduced_medium = develop_negative(image, reduced)
    model = reduced_medium.metadata["film_process_model"]

    # The neutral migration baseline preserves current density within float error.
    assert np.allclose(reduced_medium.density_cmy, legacy_medium.density_cmy, atol=2e-6)
    assert model["representation"] == "reduced_bw_silver_pool_v1"
    assert model["components"]["metallic_silver"] is True
    assert model["components"]["dye"] is False
    assert [step["action"] for step in model["process_trace"]] == [
        "develop_silver",
        "fix_halide",
        "remove_auxiliary",
    ]
    assert model["pool_totals"]["halide"] == 0.0


def test_color_negative_material_can_run_bw_silver_process_without_identity_loss():
    image = np.zeros((10, 14, 3), dtype=np.float32)
    image[..., 0] = np.linspace(0.05, 0.95, 14, dtype=np.float32)[None, :]
    image[..., 1] = 0.35
    image[..., 2] = 0.12
    config = DarkroomConfig()
    config.chemistry.program_key = "bw_negative"
    config.enable_grain = False
    config.enable_halation = False

    original_sensitivity = config.film.layer_sensitivity_matrix
    original_absorption = config.film.dye_absorption_matrix
    medium = develop_negative(image, config)
    model = medium.metadata["film_process_model"]

    assert medium.base_type == "orange_mask"
    assert medium.color_system == "silver_on_color_material"
    assert model["representation"] == "reduced_color_material_bw_silver_pool_v1"
    assert model["components"]["metallic_silver"] is True
    assert model["components"]["dye"] is False
    assert model["cross_process"]["material_color_system"] == "color_coupler"
    assert model["cross_process"]["program_color_system"] == "silver_only"
    assert model["cross_process"]["dye_formed"] is False
    assert model["cross_process"]["material_process"] == "negative"
    effective = model["effective_development"]
    assert effective["activity"] > 0.0
    assert 0.0 <= effective["progress"] <= 1.0
    assert "clearing_failure" in effective
    runtime = medium.metadata["runtime_config"]
    assert runtime.film.layer_sensitivity_matrix == original_sensitivity
    assert np.allclose(runtime.film.dye_absorption_matrix, original_absorption)
    assert not np.allclose(medium.density_cmy[:, 0], medium.density_cmy[:, -1])


def test_monochrome_material_can_run_color_program_without_inventing_dye():
    image = np.linspace(0.08, 0.92, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    bleached = DarkroomConfig()
    bleached.mode = "bw_negative"
    bleached.film.color_process = "monochrome"
    bleached.chemistry.program_key = "color_negative"
    bleached.enable_grain = False
    bleached.enable_halation = False
    bypass = copy.deepcopy(bleached)
    bypass.chemistry.program_key = "color_negative_bleach_bypass"
    bypass.chemistry.silver_bleach_completion = 0.35

    bleached_medium = develop_negative(image, bleached)
    bypass_medium = develop_negative(image, bypass)
    model = bleached_medium.metadata["film_process_model"]
    bypass_model = bypass_medium.metadata["film_process_model"]
    master_before = bleached_medium.density_grain.copy()
    scanned = scan_negative(bleached_medium)

    assert bleached_medium.color_system == "silver_bw"
    assert bleached_medium.base_type == "clear_base"
    assert model["representation"] == "reduced_bw_material_color_coupler_pool_v1"
    assert model["process_program"]["key"] == "color_negative"
    assert model["components"]["dye"] is False
    assert model["components"]["metallic_silver"] is False
    assert model["pool_totals"]["dye"] == 0.0
    assert model["cross_process"]["material_color_system"] == "silver_bw"
    assert model["cross_process"]["program_color_system"] == "color_coupler"
    assert model["cross_process"]["dye_formed"] is False
    assert [step["action"] for step in model["process_trace"]] == [
        "develop_color",
        "bleach_silver",
        "fix_halide",
        "destroy_dye",
        "remove_auxiliary",
    ]
    assert bypass_model["representation"] == "reduced_bw_material_color_coupler_pool_v1"
    assert bypass_model["components"]["dye"] is False
    assert bypass_model["components"]["metallic_silver"] is True
    assert bypass_model["pool_totals"]["metallic_silver"] > model["pool_totals"]["metallic_silver"]
    assert np.array_equal(bleached_medium.density_grain, master_before)
    assert np.all(np.isfinite(scanned.output_srgb))


def test_monochrome_material_color_reversal_stays_on_the_material_pool():
    image = np.linspace(0.06, 0.94, 8 * 12 * 3, dtype=np.float32).reshape(8, 12, 3)
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.film.color_process = "monochrome"
    config.chemistry.program_key = "color_reversal"
    config.enable_grain = False
    config.enable_halation = False
    medium = develop_negative(image, config)
    model = medium.metadata["film_process_model"]

    assert medium.image_polarity == "positive"
    assert medium.color_system == "silver_bw"
    assert model["representation"] == "reduced_bw_material_color_reversal_pool_v1"
    assert model["components"]["dye"] is False
    assert model["cross_process"]["material_color_system"] == "silver_bw"
    assert model["cross_process"]["program_process"] == "reversal"
    assert model["cross_process"]["final_polarity"] == "positive"


def test_reduced_bw_development_and_fixing_change_material_master_before_scan():
    image = np.linspace(0.05, 0.95, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    base = DarkroomConfig()
    base.mode = "bw_negative"
    base.chemistry.program_key = "bw_negative"
    base.enable_grain = False
    base.enable_halation = False
    base.scanner.scan_normalize = False

    underdeveloped = copy.deepcopy(base)
    underdeveloped.chemistry.time_min = 1.0
    bad_fix = copy.deepcopy(base)
    bad_fix.chemistry.halide_fixing_completion = 0.0

    normal_medium = develop_negative(image, base)
    under_medium = develop_negative(image, underdeveloped)
    bad_fix_medium = develop_negative(image, bad_fix)

    assert float(under_medium.density_cmy.mean()) < float(normal_medium.density_cmy.mean())
    assert float(bad_fix_medium.density_cmy.mean()) > float(normal_medium.density_cmy.mean())
    assert bad_fix_medium.metadata["film_process_model"]["pool_totals"]["halide"] > 0.0
    pool_optics = bad_fix_medium.metadata["film_process_model"][
        "material_pool_optical_observation"
    ]
    # This is a color material run through a B&W program; material identity and
    # its retained-halide color tendency remain intact.
    assert pool_optics["retained_halide_density_rgb"] == [0.62, 0.82, 1.0]

    density_before = bad_fix_medium.density_grain.copy()
    neutral_scan = scan_negative(bad_fix_medium, base)
    brighter_config = copy.deepcopy(base)
    brighter_config.look.print_exposure_ev = 0.8
    brighter_scan = scan_negative(bad_fix_medium, brighter_config)
    assert not np.allclose(neutral_scan.output_srgb, brighter_scan.output_srgb)
    assert np.array_equal(bad_fix_medium.density_grain, density_before)


def test_explicit_color_negative_program_uses_reduced_coupler_pool_pixel_path():
    image = np.linspace(0.05, 0.95, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    legacy = DarkroomConfig()
    legacy.chemistry.program_key = "legacy_density"
    legacy.enable_grain = False
    legacy.enable_halation = False
    reduced = copy.deepcopy(legacy)
    reduced.chemistry.program_key = "color_negative"

    legacy_medium = develop_negative(image, legacy)
    reduced_medium = develop_negative(image, reduced)
    model = reduced_medium.metadata["film_process_model"]

    assert np.allclose(reduced_medium.density_cmy, legacy_medium.density_cmy, atol=2e-6)
    assert model["representation"] == "reduced_color_coupler_pool_v1"
    assert model["components"]["dye"] is True
    assert model["components"]["metallic_silver"] is False
    assert [step["action"] for step in model["process_trace"]] == [
        "develop_color",
        "bleach_silver",
        "fix_halide",
        "destroy_dye",
        "remove_auxiliary",
    ]
    assert model["pool_totals"]["halide"] == 0.0
    assert model["pool_totals"]["metallic_silver"] == 0.0


def test_color_bleach_fix_and_dye_coupling_are_formation_not_scan_controls():
    image = np.linspace(0.05, 0.95, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    normal = DarkroomConfig()
    normal.chemistry.program_key = "color_negative"
    normal.enable_grain = False
    normal.enable_halation = False
    normal.scanner.scan_normalize = False

    bypass = copy.deepcopy(normal)
    bypass.chemistry.program_key = "color_negative_bleach_bypass"
    bypass.chemistry.silver_bleach_completion = 0.0
    low_dye = copy.deepcopy(normal)
    low_dye.chemistry.dye_coupling_efficiency = 0.30
    bad_fix = copy.deepcopy(normal)
    bad_fix.chemistry.halide_fixing_completion = 0.0

    normal_medium = develop_negative(image, normal)
    bypass_medium = develop_negative(image, bypass)
    low_dye_medium = develop_negative(image, low_dye)
    bad_fix_medium = develop_negative(image, bad_fix)

    bypass_model = bypass_medium.metadata["film_process_model"]
    assert bypass_model["components"]["metallic_silver"] is True
    assert bypass_model["pool_totals"]["metallic_silver"] > 0.0
    assert bypass_model["neutral_density_components"]["silver_rgb_mean"][0] > 0.0
    assert float(bypass_medium.density_cmy.mean()) > float(normal_medium.density_cmy.mean())
    assert float(low_dye_medium.density_cmy.mean()) < float(normal_medium.density_cmy.mean())
    assert bad_fix_medium.metadata["film_process_model"]["pool_totals"]["halide"] > 0.0
    residual_rgb = bad_fix_medium.metadata["film_process_model"]["neutral_density_components"][
        "residual_halide_rgb_mean"
    ]
    assert residual_rgb[2] > residual_rgb[1] > residual_rgb[0] > 0.0
    pool_optics = bad_fix_medium.metadata["film_process_model"][
        "material_pool_optical_observation"
    ]
    assert pool_optics["retained_halide_density_rgb"] == [0.62, 0.82, 1.0]

    master_before = bypass_medium.density_grain.copy()
    neutral_scan = scan_negative(bypass_medium, normal)
    brighter = copy.deepcopy(normal)
    brighter.look.print_exposure_ev = 0.75
    brighter_scan = scan_negative(bypass_medium, brighter)
    assert not np.allclose(neutral_scan.output_srgb, brighter_scan.output_srgb)
    assert np.array_equal(bypass_medium.density_grain, master_before)


def test_developer_exhaustion_reduces_unified_pool_reaction_amount():
    image = np.full((12, 16, 3), 0.58, dtype=np.float32)
    fresh = DarkroomConfig()
    fresh.chemistry.program_key = "color_negative"
    fresh.enable_grain = False
    fresh.enable_halation = False
    exhausted = copy.deepcopy(fresh)
    exhausted.chemistry.developer_exhaustion = 1.0

    fresh_medium = develop_negative(image, fresh)
    exhausted_medium = develop_negative(image, exhausted)
    fresh_model = fresh_medium.metadata["film_process_model"]
    exhausted_model = exhausted_medium.metadata["film_process_model"]

    fresh_development = fresh_model["process_trace"][0]
    exhausted_development = exhausted_model["process_trace"][0]
    assert fresh_development["action"] == "develop_color"
    assert exhausted_development["action"] == "develop_color"
    assert exhausted_development["reacted_amount"] < fresh_development["reacted_amount"]
    assert exhausted_model["pool_totals"]["dye"] < fresh_model["pool_totals"]["dye"]
    assert exhausted_model["effective_development"]["developer_capacity"] == 0.4


def test_fixer_exhaustion_leaves_formed_dye_unchanged_and_retains_halide():
    image = np.full((12, 16, 3), 0.58, dtype=np.float32)
    fresh = DarkroomConfig()
    fresh.chemistry.program_key = "color_negative"
    fresh.enable_grain = False
    fresh.enable_halation = False
    exhausted = copy.deepcopy(fresh)
    exhausted.chemistry.fixer_exhaustion = 0.80

    fresh_medium = develop_negative(image, fresh)
    exhausted_medium = develop_negative(image, exhausted)
    fresh_model = fresh_medium.metadata["film_process_model"]
    exhausted_model = exhausted_medium.metadata["film_process_model"]

    assert exhausted_model["process_trace"][0]["reacted_amount"] == fresh_model[
        "process_trace"
    ][0]["reacted_amount"]
    assert exhausted_model["pool_totals"]["dye"] == fresh_model["pool_totals"]["dye"]
    assert fresh_model["pool_totals"]["halide"] == 0.0
    assert exhausted_model["pool_totals"]["halide"] > 0.0
    assert exhausted_model["effective_development"]["d_max_factor"] == fresh_model[
        "effective_development"
    ]["d_max_factor"]


def test_retained_halide_optics_follow_material_definition():
    image = np.full((10, 14, 3), 0.50, dtype=np.float32)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_negative"
    config.chemistry.halide_fixing_completion = 0.0
    config.film.retained_halide_density_rgb = (1.0, 0.50, 0.25)
    config.enable_grain = False
    config.enable_halation = False

    medium = develop_negative(image, config)
    model = medium.metadata["film_process_model"]
    residual = model["neutral_density_components"]["residual_halide_rgb_mean"]

    assert model["material_pool_optical_observation"][
        "retained_halide_density_rgb"
    ] == [1.0, 0.5, 0.25]
    assert residual[0] > residual[1] > residual[2] > 0.0
    assert np.isclose(residual[1] / residual[0], 0.50)
    assert np.isclose(residual[2] / residual[0], 0.25)


def test_explicit_color_reversal_uses_remaining_halide_in_unified_pool_path():
    image = np.linspace(0.05, 0.95, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    config = DarkroomConfig()
    # The stock keeps its native negative identity; the program alone changes
    # the resulting medium. This is the supported cross-process direction.
    assert config.film.medium_process == "negative"
    config.chemistry.program_key = "color_reversal"
    config.enable_grain = False
    config.enable_halation = False

    medium = develop_negative(image, config)
    model = medium.metadata["film_process_model"]

    assert medium.image_polarity == "positive"
    assert medium.compatible_interpreters == ("positive_transparency_scan",)
    assert model["representation"] == "reduced_color_reversal_pool_v1"
    assert [step["action"] for step in model["process_trace"]] == [
        "develop_silver",
        "activate_remaining_halide",
        "develop_color",
        "bleach_silver",
        "fix_halide",
        "destroy_dye",
        "remove_auxiliary",
    ]


def test_experimental_clear_base_reversal_freezes_bleached_mask_in_medium_optics():
    image = np.linspace(0.03, 0.97, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.chemistry.mask_bleach_completion = 1.0
    config.enable_grain = False
    config.enable_halation = False

    medium = develop_negative(image, config)
    model = medium.metadata["film_process_model"]
    observation = model["optical_observation"]

    assert medium.image_polarity == "positive"
    assert medium.base_type == "clear_base"
    assert "bleach_mask_dye" in [step["action"] for step in model["process_trace"]]
    assert np.allclose(observation["base_density_rgb"], config.film.clear_support_density_rgb)
    assert observation["masking_coupler_remaining"] == 0.0


def test_color_negative_stock_reversal_process_keeps_native_base_and_uses_reversal_controls():
    image = np.linspace(0.05, 0.95, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    normal = DarkroomConfig()
    normal.chemistry.program_key = "color_reversal"
    normal.enable_grain = False
    normal.enable_halation = False
    weak = copy.deepcopy(normal)
    weak.chemistry.reversal_activation = 0.30
    weak.chemistry.dye_coupling_efficiency = 0.45
    weak.film.cross_process_dye_coupling = 0.35

    normal_medium = develop_negative(image, normal)
    weak_medium = develop_negative(image, weak)
    model = normal_medium.metadata["film_process_model"]

    assert normal_medium.base_type == "orange_mask"
    assert normal_medium.image_polarity == "positive"
    assert model["representation"] == "reduced_color_reversal_pool_v1"
    assert model["cross_process"]["material_process"] == "negative"
    assert model["cross_process"]["program_process"] == "reversal"
    assert model["cross_process"]["material_polarity"] == "negative"
    assert model["cross_process"]["final_polarity"] == "positive"
    assert model["compatibility_profile"]["dye_coupling"] == 1.0
    assert weak_medium.metadata["film_process_model"]["compatibility_profile"]["dye_coupling"] == 0.35
    assert not np.allclose(normal_medium.density_cmy, weak_medium.density_cmy)


def test_color_reversal_stock_negative_process_keeps_clear_base_and_uses_negative_controls():
    image = np.linspace(0.05, 0.95, 10 * 14 * 3, dtype=np.float32).reshape(10, 14, 3)
    normal = DarkroomConfig()
    normal.film.name = "Test Color Reversal Stock"
    normal.film.medium_process = "slide"
    normal.film.image_polarity = "positive"
    normal.film.film_base_density_rgb = (0.03, 0.03, 0.04)
    normal.chemistry.program_key = "color_negative"
    normal.enable_grain = False
    normal.enable_halation = False
    bypass = copy.deepcopy(normal)
    bypass.chemistry.program_key = "color_negative_bleach_bypass"
    bypass.chemistry.silver_bleach_completion = 0.0

    normal_medium = develop_negative(image, normal)
    bypass_medium = develop_negative(image, bypass)
    model = normal_medium.metadata["film_process_model"]

    assert normal_medium.base_type == "clear_base"
    assert normal_medium.image_polarity == "negative"
    assert model["representation"] == "reduced_color_coupler_pool_v1"
    assert model["cross_process"]["material_process"] == "slide"
    assert model["cross_process"]["program_process"] == "negative"
    assert model["cross_process"]["material_polarity"] == "positive"
    assert model["cross_process"]["final_polarity"] == "negative"
    assert bypass_medium.metadata["film_process_model"]["pool_totals"]["metallic_silver"] > 0.0
    assert float(bypass_medium.density_cmy.mean()) > float(normal_medium.density_cmy.mean())


def test_bw_reversal_formation_controls_change_master_but_scanner_does_not():
    image = np.linspace(0.05, 0.95, 12 * 16, dtype=np.float32)
    image = np.repeat(image.reshape(12, 16, 1), 3, axis=-1)
    normal = DarkroomConfig()
    normal.mode = "bw_negative"
    normal.film.color_process = "monochrome"
    normal.chemistry.program_key = "bw_reversal"
    normal.enable_grain = False
    normal.enable_halation = False
    normal.scanner.scan_normalize = False
    normal.scanner.interpreter_key = "positive_transparency_scan"
    normal.scanner.input_polarity = "positive"
    normal.scanner.scan_method = "positive_transparency"

    weak_activation = copy.deepcopy(normal)
    weak_activation.chemistry.reversal_activation = 0.25
    normal_medium = develop_negative(image, normal)
    weak_medium = develop_negative(image, weak_activation)
    model = normal_medium.metadata["film_process_model"]

    assert model["representation"] == "reduced_bw_reversal_pool_v1"
    assert [step["action"] for step in model["process_trace"]] == [
        "develop_silver",
        "remove_silver",
        "activate_remaining_halide",
        "develop_silver",
        "fix_halide",
        "remove_auxiliary",
    ]
    assert not np.allclose(normal_medium.density_cmy, weak_medium.density_cmy)

    master_before = normal_medium.density_grain.copy()
    base_scan = scan_negative(normal_medium, normal)
    altered_scanner = copy.deepcopy(normal)
    altered_scanner.look.print_exposure_ev = 1.0
    altered_scan = scan_negative(normal_medium, altered_scanner)
    assert not np.allclose(base_scan.output_srgb, altered_scan.output_srgb)
    assert np.array_equal(normal_medium.density_grain, master_before)


def test_quality_mode_resolves_real_internal_work_sizes_and_fast_cap():
    config = DarkroomConfig()
    config.fast_mode = False

    config.processing.quality_mode = "draft"
    assert _processing_work_long_edge(config, "halation") == 1200
    assert _processing_work_long_edge(config, "grain") == 1200
    assert _emulsion_spread_work_long_edge(config) == 1400

    config.processing.quality_mode = "standard"
    assert _processing_work_long_edge(config, "halation") == 1800
    assert _emulsion_spread_work_long_edge(config) == 2200

    config.fast_mode = True
    assert _processing_work_long_edge(config, "halation") == 1600
    assert _emulsion_spread_work_long_edge(config) == 1400

    config.fast_mode = False
    config.processing.quality_mode = "high"
    assert _processing_work_long_edge(config, "halation") is None

    config.fast_mode = True
    assert _processing_work_long_edge(config, "halation") == 1600

    config.processing.halation_work_long_edge = 900
    assert _processing_work_long_edge(config, "halation") == 900

    config.fast_mode = False
    assert _processing_work_long_edge(config, "halation") == 900

    config.processing.quality_mode = "draft"
    assert _processing_work_long_edge(config, "halation") == 900


def test_fast_and_quality_modes_preserve_process_topology_and_medium_contract():
    image = np.full((24, 30, 3), 0.48, dtype=np.float32)
    high = DarkroomConfig()
    high.processing.quality_mode = "high"
    high.fast_mode = False
    draft_fast = copy.deepcopy(high)
    draft_fast.processing.quality_mode = "draft"
    draft_fast.fast_mode = True

    high_medium = develop_negative(image, high, rng=np.random.default_rng(91))
    fast_medium = develop_negative(image, draft_fast, rng=np.random.default_rng(91))
    high_model = high_medium.metadata["film_process_model"]
    fast_model = fast_medium.metadata["film_process_model"]

    assert high_medium.image_polarity == fast_medium.image_polarity
    assert high_medium.compatible_interpreters == fast_medium.compatible_interpreters
    assert high_model["process_program"] == fast_model["process_program"]
    assert [step["action"] for step in high_model["process_trace"]] == [
        step["action"] for step in fast_model["process_trace"]
    ]


def test_surface_plating_and_material_degradation_are_recorded_in_final_medium():
    image = np.full((18, 24, 3), 0.42, dtype=np.float32)
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    config.chemistry.silver_plating = 0.65
    config.chemistry.fixer_exhaustion = 0.5
    config.film.material_degradation = 0.7

    medium = develop_negative(image, config, rng=np.random.default_rng(101))
    model = medium.metadata["film_process_model"]

    assert model["surface_deposits"]["metallic_silver"]["control_strength"] == 0.65
    assert model["material_degradation"]["strength"] == 0.7
    assert "silver_plating" in medium.metadata["accident_maps"]

    master_before = medium.density_grain.copy()
    altered_scan = copy.deepcopy(config)
    altered_scan.look.print_exposure_ev = 1.4
    scan_negative(medium, altered_scan)
    assert np.array_equal(medium.density_grain, master_before)


def test_full_scan_lifetime_topology_preserves_every_diagnostic_stage_exactly():
    image = np.linspace(0.03, 0.97, 48 * 64 * 3, dtype=np.float32).reshape(
        48,
        64,
        3,
    )
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    medium = develop_negative(image, config, rng=np.random.default_rng(20260808))

    with execution_topology(REFERENCE_TOPOLOGY):
        reference = scan_negative(medium, config)
    with execution_topology(OPTIMIZED_TOPOLOGY):
        optimized = scan_negative(medium, config)

    for field_name in (
        "negative_linear",
        "negative_base_balanced",
        "positive_raw",
        "negative_channel_reconstructed",
        "scanner_raw",
        "negative_total_density",
        "positive_linear",
        "output_srgb",
        "positive_no_grain",
    ):
        assert np.array_equal(
            getattr(optimized, field_name),
            getattr(reference, field_name),
        ), field_name
    assert not np.shares_memory(
        optimized.negative_linear,
        optimized.scanner_raw,
    )
    assert not np.shares_memory(
        reference.negative_linear,
        reference.scanner_raw,
    )


def test_positive_full_scan_lifetime_topology_preserves_diagnostics_exactly():
    image = np.linspace(0.04, 0.96, 48 * 64 * 3, dtype=np.float32).reshape(
        48,
        64,
        3,
    )
    config = DarkroomConfig()
    config.chemistry.program_key = "color_reversal"
    config.enable_grain = False
    config.enable_halation = False
    medium = develop_negative(image, config, rng=np.random.default_rng(20260809))
    assert medium.image_polarity == "positive"

    with execution_topology(REFERENCE_TOPOLOGY):
        reference = scan_negative(medium)
    with execution_topology(OPTIMIZED_TOPOLOGY):
        optimized = scan_negative(medium)

    for field_name in (
        "negative_linear",
        "negative_base_balanced",
        "positive_raw",
        "negative_channel_reconstructed",
        "scanner_raw",
        "negative_total_density",
        "positive_linear",
        "output_srgb",
        "positive_no_grain",
    ):
        assert np.array_equal(
            getattr(optimized, field_name),
            getattr(reference, field_name),
        ), field_name
    independent_fields = (
        "negative_linear",
        "negative_base_balanced",
        "positive_raw",
        "negative_channel_reconstructed",
        "scanner_raw",
    )
    for index, left in enumerate(independent_fields):
        for right in independent_fields[index + 1 :]:
            assert not np.shares_memory(
                getattr(optimized, left),
                getattr(optimized, right),
            )


@pytest.mark.parametrize("program_key", ("bw_negative", "bw_reversal"))
def test_monochrome_full_scan_lifetime_topology_is_exact(program_key):
    ramp = np.linspace(0.05, 0.95, 36 * 52, dtype=np.float32).reshape(36, 52)
    image = np.repeat(ramp[..., None], 3, axis=-1)
    config = DarkroomConfig()
    config.chemistry.program_key = program_key
    config.enable_grain = False
    config.enable_halation = False
    medium = develop_negative(image, config, rng=np.random.default_rng(20260810))

    with execution_topology(REFERENCE_TOPOLOGY):
        reference = scan_negative(medium)
    with execution_topology(OPTIMIZED_TOPOLOGY):
        optimized = scan_negative(medium)

    for field_name in (
        "negative_linear",
        "negative_base_balanced",
        "positive_raw",
        "negative_channel_reconstructed",
        "scanner_raw",
        "negative_total_density",
        "positive_linear",
        "output_srgb",
        "positive_no_grain",
    ):
        assert np.array_equal(
            getattr(optimized, field_name),
            getattr(reference, field_name),
        ), (program_key, field_name)


def test_engine_reexported_validate_still_intercepts_develop_negative(monkeypatch):
    import film_foundry.core.engine as engine
    import film_foundry.core.runtime_validate as runtime_validate

    assert engine._validate_develop_runtime is runtime_validate._validate_develop_runtime
    called = []

    def boom(config, *, allow_discarded_history=False):
        called.append(allow_discarded_history)
        raise RuntimeError("patched-validate")

    monkeypatch.setattr(engine, "_validate_develop_runtime", boom)
    image = np.full((6, 8, 3), 0.4, dtype=np.float32)
    with pytest.raises(RuntimeError, match="patched-validate"):
        engine.develop_negative(image, DarkroomConfig())
    assert called == [False]
    assert runtime_validate._validate_develop_runtime is not boom


def test_medium_api_aliases_are_the_same_functions():
    from film_foundry.core.engine import develop_medium, scan_medium

    assert develop_medium is develop_negative
    assert scan_medium is scan_negative

def test_scan_runtime_uses_only_explicit_engine_services(monkeypatch):
    from film_foundry.core import engine, scan_runtime

    assert not hasattr(scan_runtime, "develop_negative")
    assert not hasattr(scan_runtime, "save_developed_medium_at_path")
    sentinel = object()
    monkeypatch.setattr(engine, "capture_optical_density", lambda *args, **kwargs: sentinel)
    assert scan_runtime.capture_optical_density(None) is sentinel
