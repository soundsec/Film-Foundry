import numpy as np

from film_foundry.core.execution_topology import (
    OPTIMIZED_TOPOLOGY,
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.core.scanner import (
    balance_negative_base,
    capture_optical_density,
    capture_transmitted_medium,
    estimate_negative_base_transmittance,
    invert_negative_image,
    layer_density_to_optical_density_rgb,
    negative_backlight_illuminant_rgb,
    negative_total_density_rgb,
    negative_transmittance_rgb,
    render_negative_image,
    render_positive_scan,
    render_positive_transparency_scan,
    reconstruct_negative_channels,
    scan_negative_raw,
    scanner_raw_to_positive_rgb,
)
from film_foundry.model.config import FilmStockConfig, ScannerConfig


def test_transmittance_decreases_with_density():
    film = FilmStockConfig()
    low = np.full((1, 1, 3), 0.2, dtype=np.float32)
    high = np.full((1, 1, 3), 1.0, dtype=np.float32)

    low_t = negative_transmittance_rgb(low, film)
    high_t = negative_transmittance_rgb(high, film)

    assert np.all(high_t < low_t)


def test_neutral_base_reduces_exactly_to_legacy_additive_density():
    density = np.asarray([[[0.20, 0.70, 1.10], [1.00, 0.30, 0.55]]], dtype=np.float32)
    film = FilmStockConfig(
        film_base_density_rgb=(0.30, 0.30, 0.30),
        base_dye_interaction_strength=1.0,
    )
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32)
    expected = np.einsum("...l,rl->...r", density, absorption) + 0.30

    assert np.allclose(negative_total_density_rgb(density, film), expected, atol=1e-6)


def test_custom_coloured_base_changes_dye_response_after_exact_base_removal():
    density = np.asarray([[[0.15, 0.85, 1.20], [1.10, 0.25, 0.50]]], dtype=np.float32)
    no_interaction = FilmStockConfig(
        film_base_density_rgb=(0.18, 0.55, 0.85),
        base_dye_interaction_strength=0.0,
    )
    orange_base = FilmStockConfig(
        film_base_density_rgb=(0.18, 0.55, 0.85),
        base_dye_interaction_strength=1.0,
    )
    cyan_base = FilmStockConfig(
        film_base_density_rgb=(0.80, 0.42, 0.16),
        base_dye_interaction_strength=1.0,
    )

    legacy_residual = negative_total_density_rgb(density, no_interaction) - np.asarray(
        no_interaction.film_base_density_rgb,
        dtype=np.float32,
    )
    orange_residual = negative_total_density_rgb(density, orange_base) - np.asarray(
        orange_base.film_base_density_rgb,
        dtype=np.float32,
    )
    cyan_residual = negative_total_density_rgb(density, cyan_base) - np.asarray(
        cyan_base.film_base_density_rgb,
        dtype=np.float32,
    )
    empty = np.zeros((1, 1, 3), dtype=np.float32)

    assert np.allclose(
        negative_total_density_rgb(empty, orange_base)[0, 0],
        orange_base.film_base_density_rgb,
        atol=1e-6,
    )
    assert not np.allclose(orange_residual, legacy_residual)
    assert not np.allclose(cyan_residual, orange_residual)


def test_coloured_base_projection_can_consume_private_layers_exactly():
    density = np.linspace(0.02, 1.4, 11 * 7 * 3, dtype=np.float32).reshape(11, 7, 3)
    original = density.copy()
    film = FilmStockConfig(
        film_base_density_rgb=(0.18, 0.55, 0.85),
        base_dye_interaction_strength=0.73,
    )

    expected = layer_density_to_optical_density_rgb(density, film)
    private_density = density.copy()
    actual = layer_density_to_optical_density_rgb(
        private_density,
        film,
        consume_input=True,
    )

    assert np.array_equal(density, original)
    assert np.array_equal(actual, expected)
    assert not np.array_equal(private_density, original)


def test_coloured_base_projection_rejects_consuming_read_only_layers():
    density = np.ones((2, 3, 3), dtype=np.float32)
    density.setflags(write=False)
    with np.testing.assert_raises_regex(ValueError, "writable private"):
        layer_density_to_optical_density_rgb(
            density,
            FilmStockConfig(),
            consume_input=True,
        )


def test_positive_transparency_color_controls_are_bounded_by_device_strength():
    transparency = np.asarray([[[0.15, 0.45, 0.85], [0.80, 0.35, 0.10]]], dtype=np.float32)
    neutral = ScannerConfig(
        positive_scan_color_control_strength=0.0,
        print_gamma=1.0,
        scan_saturation=1.0,
    )
    hostile = ScannerConfig(
        positive_scan_color_control_strength=0.0,
        print_gamma=2.0,
        scan_saturation=2.0,
        print_color_shift=(0.2, -0.2, 0.2),
    )

    expected = render_positive_transparency_scan(transparency, neutral, print_contrast=1.0)
    actual = render_positive_transparency_scan(transparency, hostile, print_contrast=1.8)

    assert np.allclose(actual, expected, atol=1e-6)


def test_positive_highlight_gain_remains_an_explicit_highlight_only_control():
    transparency = np.asarray([[[0.12, 0.12, 0.12], [0.62, 0.62, 0.62]]], dtype=np.float32)
    neutral = ScannerConfig(
        positive_scan_color_control_strength=0.0,
        highlight_bias_threshold=0.30,
        highlight_bias_softness=0.20,
        highlight_color_bias=(1.0, 1.0, 1.0),
    )
    biased = ScannerConfig(
        positive_scan_color_control_strength=0.0,
        highlight_bias_threshold=0.30,
        highlight_bias_softness=0.20,
        highlight_color_bias=(1.0, 1.20, 0.75),
    )

    baseline = render_positive_transparency_scan(transparency, neutral)
    altered = render_positive_transparency_scan(transparency, biased)

    assert np.allclose(altered[0, 0], baseline[0, 0], atol=1e-6)
    assert altered[0, 1, 1] > baseline[0, 1, 1]
    assert altered[0, 1, 2] < baseline[0, 1, 2]


def test_positive_black_adaptation_preserves_dark_order_without_grey_floor():
    transparency = np.asarray(
        [[[[0.0, 0.0, 0.0], [0.001, 0.001, 0.001], [0.01, 0.01, 0.01], [0.04, 0.04, 0.04]]]],
        dtype=np.float32,
    ).reshape(1, 4, 3)
    scanner = ScannerConfig(
        positive_scan_color_control_strength=0.0,
        projection_black_adaptation=0.50,
        projection_white_softness=0.0,
    )

    adapted = render_positive_transparency_scan(transparency, scanner)[0, :, 0]

    assert adapted[0] == 0.0
    assert np.all(np.diff(adapted) > 0.0)


def test_black_adaptation_scratch_reuse_is_exact_and_mode_independent():
    rng = np.random.default_rng(20260811)
    transparency = rng.uniform(0.0, 1.2, (127, 191, 3)).astype(np.float32)
    transparency[0, 0] = 0.0
    scanner = ScannerConfig(
        positive_scan_color_control_strength=0.68,
        projection_black_adaptation=0.61,
        projection_white_softness=0.0,
        scan_saturation=1.17,
    )

    with execution_topology(REFERENCE_TOPOLOGY):
        reference = render_positive_transparency_scan(
            transparency.copy(),
            scanner,
            consume_input=True,
        )
    with execution_topology(OPTIMIZED_TOPOLOGY):
        optimized = render_positive_transparency_scan(
            transparency.copy(),
            scanner,
            consume_input=True,
        )

    assert np.array_equal(optimized, reference)


def test_private_positive_transparency_render_matches_public_result():
    rng = np.random.default_rng(337)
    transparency = rng.uniform(0.001, 0.98, (43, 61, 3)).astype(np.float32)
    original = transparency.copy()
    scanner = ScannerConfig(
        positive_scan_color_control_strength=0.72,
        print_gamma=1.17,
        print_color_shift=(0.03, -0.02, 0.01),
        print_color_bias=(1.04, 0.98, 1.02),
        highlight_color_bias=(0.98, 1.06, 0.94),
        projection_black_adaptation=0.24,
        projection_white_softness=0.18,
        scan_saturation=1.13,
    )

    public = render_positive_transparency_scan(
        transparency,
        scanner,
        print_contrast=1.08,
        print_exposure_ev=0.21,
    )
    private_input = transparency.copy()
    consumed = render_positive_transparency_scan(
        private_input,
        scanner,
        print_contrast=1.08,
        print_exposure_ev=0.21,
        consume_input=True,
    )

    assert np.array_equal(consumed, public)
    assert np.shares_memory(consumed, private_input)
    assert np.array_equal(transparency, original)


def test_scanner_raw_and_positive_are_displayable():
    film = FilmStockConfig()
    scanner = ScannerConfig()
    density = np.full((4, 4, 3), 0.8, dtype=np.float32)

    total_density = negative_total_density_rgb(density, film)
    raw = scan_negative_raw(density, film)
    negative_linear = render_negative_image(density, film)
    balanced = balance_negative_base(negative_linear)
    raw_positive = invert_negative_image(balanced)
    rendered = render_positive_scan(raw_positive, scanner)
    positive = scanner_raw_to_positive_rgb(raw, scanner)

    assert total_density.shape == density.shape
    assert raw.shape == density.shape
    assert negative_linear.shape == density.shape
    assert balanced.shape == density.shape
    assert raw_positive.shape == density.shape
    assert rendered.shape == density.shape
    assert positive.shape == density.shape
    assert raw.min() > 0.0
    assert positive.min() >= 0.0
    assert positive.max() <= 1.0


def test_private_capture_target_is_exact_and_keeps_density_read_only():
    rng = np.random.default_rng(311)
    density = rng.uniform(0.0, 2.4, (31, 47, 3)).astype(np.float32)
    original = density.copy()
    scanner = ScannerConfig()
    illuminant = np.asarray((0.91, 0.78, 0.63), dtype=np.float32)
    expected = capture_optical_density(
        density,
        scanner,
        illuminant_rgb=illuminant,
    )
    target = np.empty_like(density)

    actual = capture_optical_density(
        density,
        scanner,
        illuminant_rgb=illuminant,
        _out=target,
    )

    assert np.array_equal(actual, expected)
    assert np.shares_memory(actual, target)
    assert np.array_equal(density, original)


def test_missing_base_estimator_matches_the_scanner_domain_percentile_fallback():
    raw = np.linspace(0.03, 0.97, 17 * 23 * 3, dtype=np.float32).reshape(17, 23, 3)
    expected = np.percentile(raw, 98.75, axis=(0, 1)).astype(np.float32)

    estimated = estimate_negative_base_transmittance(raw, 98.75)
    balanced = balance_negative_base(raw, base_percentile=98.75)

    assert np.array_equal(estimated, expected)
    assert np.array_equal(
        balanced,
        np.clip(raw / expected.reshape(1, 1, 3), 1e-6, 1.0).astype(np.float32),
    )


def test_private_positive_render_consumption_is_bit_exact_and_public_input_is_read_only():
    rng = np.random.default_rng(61)
    raw_positive = rng.random((47, 71, 3), dtype=np.float32) * 2.2
    original = raw_positive.copy()
    scanner = ScannerConfig(
        print_mapping_mode="sigmoid",
        scan_saturation=1.17,
        highlight_color_bias=(1.03, 0.98, 0.94),
    )

    expected = render_positive_scan(
        raw_positive,
        scanner,
        print_contrast=1.13,
        print_exposure_ev=0.27,
        paper_black=0.02,
        paper_white=0.96,
    )
    assert np.array_equal(raw_positive, original)

    consumed = raw_positive.copy()
    actual = render_positive_scan(
        consumed,
        scanner,
        print_contrast=1.13,
        print_exposure_ev=0.27,
        paper_black=0.02,
        paper_white=0.96,
        consume_input=True,
    )
    assert np.array_equal(actual, expected)
    assert np.shares_memory(actual, consumed)

    scanner.print_mapping_mode = "exponential"
    expected_exponential = render_positive_scan(raw_positive, scanner)
    consumed_exponential = raw_positive.copy()
    actual_exponential = render_positive_scan(
        consumed_exponential,
        scanner,
        consume_input=True,
    )
    assert np.array_equal(actual_exponential, expected_exponential)


def test_private_scanner_raw_interpretation_consumption_is_bit_exact():
    rng = np.random.default_rng(20260808)
    scanner_raw = rng.uniform(0.02, 0.98, (53, 79, 3)).astype(np.float32)
    original = scanner_raw.copy()
    scanner = ScannerConfig(
        negative_channel_compensation_enabled=True,
        negative_channel_compensation_strength=0.62,
        scan_saturation=1.09,
    )

    expected = scanner_raw_to_positive_rgb(
        scanner_raw,
        scanner,
        known_base_transmittance_rgb=np.asarray((0.92, 0.73, 0.48), dtype=np.float32),
    )
    assert np.array_equal(scanner_raw, original)

    private_raw = scanner_raw.copy()
    actual = scanner_raw_to_positive_rgb(
        private_raw,
        scanner,
        known_base_transmittance_rgb=np.asarray((0.92, 0.73, 0.48), dtype=np.float32),
        consume_input=True,
    )

    assert np.array_equal(actual, expected)
    assert not np.array_equal(private_raw, original)


def test_known_base_removal_cancels_negative_backlight_color_and_brightness():
    film = FilmStockConfig()
    scanner = ScannerConfig()
    scanner.negative_backlight_ev = -0.75
    scanner.negative_backlight_temperature_k = 3400.0
    density = np.full((3, 4, 3), 0.75, dtype=np.float32)
    clear = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)

    raw = render_negative_image(density, film, scanner)
    base = render_negative_image(clear, film, scanner)
    balanced = balance_negative_base(raw, base_samples=base)

    neutral = ScannerConfig()
    neutral_raw = render_negative_image(density, film, neutral)
    neutral_base = render_negative_image(clear, film, neutral)
    neutral_balanced = balance_negative_base(neutral_raw, base_samples=neutral_base)

    assert np.allclose(balanced, neutral_balanced, atol=1e-6)


def test_authoritative_known_base_overrides_samples_and_percentile_guess():
    raw = np.asarray(
        [[[0.08, 0.16, 0.24], [0.10, 0.20, 0.30]]],
        dtype=np.float32,
    )
    known = np.asarray((0.20, 0.40, 0.60), dtype=np.float32)
    hostile_samples = np.asarray([[[0.95, 0.95, 0.95]]], dtype=np.float32)

    balanced = balance_negative_base(
        raw,
        base_percentile=1.0,
        base_samples=hostile_samples,
        known_base_transmittance_rgb=known,
    )

    assert np.allclose(balanced, raw / known.reshape(1, 1, 3), atol=1e-6)


def test_known_base_density_and_captured_transmittance_are_equivalent():
    scanner = ScannerConfig(
        negative_backlight_ev=-0.6,
        negative_backlight_temperature_k=3900.0,
        scanner_light_color=(0.94, 1.0, 0.89),
    )
    known_density = np.asarray((0.18, 0.55, 0.85), dtype=np.float32)
    known_capture = capture_optical_density(
        known_density.reshape(1, 1, 3),
        scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(scanner),
    ).reshape(3)
    scene_density = known_density.reshape(1, 1, 3) + np.asarray(
        [[[0.25, 0.45, 0.70]]], dtype=np.float32
    )
    raw = capture_optical_density(
        scene_density,
        scanner,
        illuminant_rgb=negative_backlight_illuminant_rgb(scanner),
    )

    from_density = scanner_raw_to_positive_rgb(
        raw,
        scanner,
        known_base_density_rgb=known_density,
    )
    from_capture = scanner_raw_to_positive_rgb(
        raw,
        scanner,
        known_base_transmittance_rgb=known_capture,
    )

    assert np.allclose(from_density, from_capture, atol=1e-6)


def test_known_base_rejects_ambiguous_or_invalid_authoritative_input():
    raw = np.full((1, 1, 3), 0.2, dtype=np.float32)
    with np.testing.assert_raises(ValueError):
        scanner_raw_to_positive_rgb(
            raw,
            known_base_density_rgb=np.ones(3, dtype=np.float32),
            known_base_transmittance_rgb=np.ones(3, dtype=np.float32),
        )
    with np.testing.assert_raises(ValueError):
        balance_negative_base(
            raw,
            known_base_transmittance_rgb=np.asarray((1.0, np.nan, 1.0)),
        )


def test_saturated_clear_base_does_not_create_false_negative_density():
    film = FilmStockConfig()
    scanner = ScannerConfig()
    scanner.negative_backlight_ev = 4.0
    clear = np.asarray(film.density_min, dtype=np.float32).reshape(1, 1, 3)

    raw = render_negative_image(clear, film, scanner)
    balanced = balance_negative_base(raw, base_samples=raw)
    scene_density = invert_negative_image(balanced)

    assert np.allclose(balanced, 1.0)
    assert np.allclose(scene_density, 0.0)


def test_transmission_capture_is_shared_and_interpretation_free():
    film = FilmStockConfig()
    scanner = ScannerConfig()
    density = np.full((2, 3, 3), 0.4, dtype=np.float32)
    illuminant = np.asarray((0.8, 1.0, 0.9), dtype=np.float32)

    captured = capture_transmitted_medium(density, film, scanner, illuminant_rgb=illuminant)
    expected = negative_transmittance_rgb(density, film) * illuminant.reshape(1, 1, 3)

    assert np.allclose(captured, expected)


def test_precomputed_optical_density_capture_is_identical_to_medium_capture():
    film = FilmStockConfig()
    scanner = ScannerConfig()
    scanner.scanner_light_color = (0.93, 1.0, 0.88)
    scanner.scanner_response_matrix = (
        (1.0, 0.01, 0.0),
        (0.0, 0.99, 0.01),
        (0.01, 0.0, 0.99),
    )
    density = np.linspace(0.02, 1.4, 5 * 7 * 3, dtype=np.float32).reshape(5, 7, 3)
    total_density = negative_total_density_rgb(density, film)

    direct = capture_transmitted_medium(density, film, scanner)
    reused = capture_optical_density(total_density, scanner)

    assert np.array_equal(reused, direct)


def test_negative_channel_reconstruction_is_an_explicit_post_mask_stage():
    scanner = ScannerConfig()
    scanner.negative_channel_matrix = (
        (1.0, 0.0, 0.0),
        (0.05, 0.95, 0.0),
        (0.08, -0.03, 0.95),
    )
    scanner.negative_channel_gamma = (1.0, 0.98, 0.94)
    scene_density = np.asarray([[[0.3, 0.6, 0.9]]], dtype=np.float32)

    reconstructed = reconstruct_negative_channels(scene_density, scanner)

    assert reconstructed.shape == scene_density.shape
    assert not np.allclose(reconstructed, scene_density)
    assert np.all(reconstructed >= 0.0)


def test_blue_green_compensation_switch_is_scanner_side_and_material_independent():
    scanner = ScannerConfig()
    scene_density = np.asarray([[[0.35, 0.65, 0.90]]], dtype=np.float32)
    absorption = (
        (1.00, 0.10, 0.04),
        (0.08, 1.00, 0.12),
        (0.03, 0.16, 1.00),
    )

    disabled = reconstruct_negative_channels(
        scene_density,
        scanner,
        dye_absorption_matrix=absorption,
    )
    scanner.negative_channel_compensation_enabled = True
    scanner.negative_channel_compensation_strength = 0.35
    enabled = reconstruct_negative_channels(
        scene_density,
        scanner,
        dye_absorption_matrix=absorption,
    )
    different_material = reconstruct_negative_channels(
        scene_density,
        scanner,
        dye_absorption_matrix=np.eye(3, dtype=np.float32),
    )

    assert np.allclose(disabled, scene_density)
    assert not np.allclose(enabled, disabled)
    assert np.allclose(enabled, different_material)
    assert np.all(np.isfinite(enabled))
    assert np.all(enabled >= 0.0)
