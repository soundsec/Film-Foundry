import numpy as np

from film_foundry.core.scanner import (
    apply_scan_normalization_range,
    balance_negative_base,
    invert_negative_image,
    normalize_scan_rgb,
    reconstruct_negative_channels,
    scan_normalization_range,
)
from film_foundry.core.execution_topology import (
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.model.config import ScannerConfig


def _legacy_scan_normalization_range(
    image: np.ndarray,
    black_percentile: float,
    white_percentile: float,
    mode: str,
) -> tuple[np.ndarray, np.ndarray]:
    if mode == "rgb":
        black = np.asarray(
            [
                np.percentile(image[..., channel], black_percentile)
                for channel in range(3)
            ],
            dtype=np.float32,
        )
        white = np.asarray(
            [
                np.percentile(image[..., channel], white_percentile)
                for channel in range(3)
            ],
            dtype=np.float32,
        )
        return black, white
    luma = (
        image[..., 0] * 0.2126
        + image[..., 1] * 0.7152
        + image[..., 2] * 0.0722
    )
    black_value = float(np.percentile(luma, black_percentile))
    white_value = float(np.percentile(luma, white_percentile))
    return (
        np.asarray((black_value,) * 3, dtype=np.float32),
        np.asarray((white_value,) * 3, dtype=np.float32),
    )


def test_scan_normalize_preserves_flat_images():
    image = np.full((8, 8, 3), 0.42, dtype=np.float32)
    out = normalize_scan_rgb(image)
    assert np.allclose(out, image)


def test_scan_normalize_expands_range():
    ramp = np.linspace(0.2, 0.8, 64, dtype=np.float32).reshape(8, 8, 1)
    image = np.repeat(ramp, 3, axis=-1)
    out = normalize_scan_rgb(image, 0.0, 100.0)
    assert out.min() == 0.0
    assert out.max() == 1.0


def test_luma_normalization_preserves_pixel_chromaticity():
    ramp = np.linspace(0.12, 0.82, 257, dtype=np.float32).reshape(1, -1, 1)
    tint = np.asarray((1.0, 0.68, 0.34), dtype=np.float32).reshape(1, 1, 3)
    image = ramp * tint

    out = normalize_scan_rgb(
        image,
        black_percentile=0.0,
        white_percentile=100.0,
        strength=0.72,
        mode="luma",
    )

    # Ignore the exact black endpoint, where only the unnormalized blend
    # remains. Everywhere else a luma-only operation must keep channel ratios.
    unclipped = np.max(out, axis=-1) < 1.0 - 1e-6
    original_ratios = image[..., 1:][unclipped] / image[..., 0:1][unclipped]
    output_ratios = out[..., 1:][unclipped] / out[..., 0:1][unclipped]
    assert np.allclose(output_ratios, original_ratios, atol=2e-6)


def test_consumed_rgb_normalization_is_exactly_reference_topology():
    image = (
        np.random.default_rng(149)
        .uniform(-0.15, 1.25, (97, 131, 3))
        .astype(np.float32)
    )
    black = np.asarray((0.04, 0.08, 0.11), dtype=np.float32)
    white = np.asarray((0.86, 0.91, 0.97), dtype=np.float32)
    optimized_input = image.copy()
    reference_input = image.copy()

    optimized = apply_scan_normalization_range(
        optimized_input,
        black,
        white,
        strength=0.63,
        mode="rgb",
        consume_input=True,
    )
    with execution_topology(REFERENCE_TOPOLOGY):
        reference = apply_scan_normalization_range(
            reference_input,
            black,
            white,
            strength=0.63,
            mode="rgb",
            consume_input=True,
        )

    assert optimized is optimized_input
    assert reference is reference_input
    assert np.array_equal(optimized, reference)


def test_rgb_normalization_remains_an_explicit_channel_balancing_mode():
    ramp = np.linspace(0.12, 0.82, 257, dtype=np.float32).reshape(1, -1, 1)
    tint = np.asarray((1.0, 0.68, 0.34), dtype=np.float32).reshape(1, 1, 3)
    image = ramp * tint

    out = normalize_scan_rgb(
        image,
        black_percentile=0.0,
        white_percentile=100.0,
        strength=1.0,
        mode="rgb",
    )

    assert np.allclose(out[..., 0], out[..., 1], atol=2e-6)
    assert np.allclose(out[..., 1], out[..., 2], atol=2e-6)


def test_private_scan_normalization_consumption_matches_public_result():
    rng = np.random.default_rng(941)
    image = rng.uniform(0.03, 0.97, (41, 53, 3)).astype(np.float32)
    original = image.copy()

    for mode in ("luma", "rgb"):
        public = normalize_scan_rgb(
            image,
            black_percentile=1.3,
            white_percentile=98.4,
            strength=0.73,
            mode=mode,
        )
        private_input = image.copy()
        consumed = normalize_scan_rgb(
            private_input,
            black_percentile=1.3,
            white_percentile=98.4,
            strength=0.73,
            mode=mode,
            consume_input=True,
        )

        assert np.array_equal(consumed, public)
        assert np.shares_memory(consumed, private_input)
        assert np.array_equal(image, original)


def test_private_base_balance_and_inversion_match_public_results():
    rng = np.random.default_rng(181)
    scanner_raw = rng.uniform(0.02, 0.91, (37, 59, 3)).astype(np.float32)
    known_base = np.asarray((0.93, 0.79, 0.61), dtype=np.float32)
    original = scanner_raw.copy()

    public_balanced = balance_negative_base(
        scanner_raw,
        known_base_transmittance_rgb=known_base,
    )
    private_raw = scanner_raw.copy()
    private_balanced = balance_negative_base(
        private_raw,
        known_base_transmittance_rgb=known_base,
        consume_input=True,
    )
    assert np.array_equal(private_balanced, public_balanced)
    assert np.shares_memory(private_balanced, private_raw)

    public_positive = invert_negative_image(public_balanced)
    private_positive = invert_negative_image(
        private_balanced,
        consume_input=True,
    )
    assert np.array_equal(private_positive, public_positive)
    assert np.shares_memory(private_positive, private_raw)
    assert np.array_equal(scanner_raw, original)


def test_private_identity_channel_reconstruction_is_exact_and_in_place():
    rng = np.random.default_rng(191)
    density = rng.uniform(0.0, 2.1, (37, 59, 3)).astype(np.float32)
    scanner = ScannerConfig(
        negative_channel_compensation_enabled=False,
        negative_channel_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        negative_channel_gamma=(1.0, 1.0, 1.0),
    )
    expected = reconstruct_negative_channels(density, scanner)
    private_input = density.copy()

    actual = reconstruct_negative_channels(
        private_input,
        scanner,
        consume_input=True,
    )

    assert np.array_equal(actual, expected)
    assert np.shares_memory(actual, private_input)


def test_luma_scan_range_row_work_field_is_bit_exact_and_input_read_only():
    # Width 2000 makes the 2 MP luma writer split 1001 rows into two passes.
    image = np.linspace(
        0.001,
        0.999,
        1001 * 2000 * 3,
        dtype=np.float32,
    ).reshape(1001, 2000, 3)
    image[..., 1] = np.roll(image[..., 1], 71, axis=1)
    original = image.copy()
    expected = _legacy_scan_normalization_range(image, 0.3, 99.7, "luma")

    actual = scan_normalization_range(image, 0.3, 99.7, "luma")

    assert np.array_equal(actual[0], expected[0])
    assert np.array_equal(actual[1], expected[1])
    assert np.array_equal(image, original)


def test_rgb_scan_range_private_channel_partition_is_bit_exact():
    rng = np.random.default_rng(113)
    image = rng.random((173, 251, 3), dtype=np.float32)
    expected = _legacy_scan_normalization_range(image, 1.7, 98.2, "rgb")

    actual = scan_normalization_range(image, 1.7, 98.2, "rgb")

    assert np.array_equal(actual[0], expected[0])
    assert np.array_equal(actual[1], expected[1])
