import numpy as np

from film_foundry.core.color import linear_to_srgb, luminance, srgb_to_linear


def test_srgb_round_trip_is_close():
    image = np.linspace(0.0, 1.0, 64, dtype=np.float32).reshape(8, 8, 1)
    image = np.repeat(image, 3, axis=-1)
    round_trip = linear_to_srgb(srgb_to_linear(image))
    assert np.allclose(round_trip, image, atol=1e-5)


def test_private_linear_to_srgb_consumption_matches_public_result():
    image = np.linspace(-0.2, 1.4, 31 * 47 * 3, dtype=np.float32).reshape(31, 47, 3)
    original = image.copy()
    public = linear_to_srgb(image)
    private_input = image.copy()
    consumed = linear_to_srgb(private_input, consume_input=True)

    assert np.array_equal(consumed, public)
    assert np.shares_memory(consumed, private_input)
    assert np.array_equal(image, original)


def test_private_linear_to_srgb_dark_branch_is_exact_and_in_place():
    image = np.linspace(0.0, 0.004, 29 * 41 * 3, dtype=np.float32).reshape(29, 41, 3)
    image[..., 1:] *= 0.25
    public = linear_to_srgb(image)
    private_input = image.copy()

    consumed = linear_to_srgb(private_input, consume_input=True)

    assert np.array_equal(consumed, public)
    assert np.shares_memory(consumed, private_input)


def test_srgb_decode_matches_piecewise_reference_without_mutating_input():
    image = np.linspace(-0.2, 1.4, 29 * 43 * 3, dtype=np.float32).reshape(29, 43, 3)
    original = image.copy()
    clipped = np.clip(image, 0.0, 1.0)
    expected = np.where(
        clipped <= 0.04045,
        clipped / 12.92,
        ((clipped + 0.055) / 1.055) ** 2.4,
    ).astype(np.float32)

    actual = srgb_to_linear(image)

    assert np.array_equal(actual, expected)
    assert np.array_equal(image, original)


def test_luminance_preserves_shape():
    image = np.ones((5, 7, 3), dtype=np.float32)
    assert luminance(image).shape == (5, 7)


def test_private_luminance_buffers_are_exact_and_do_not_modify_rgb_input():
    image = np.linspace(
        -0.2,
        1.4,
        37 * 53 * 3,
        dtype=np.float32,
    ).reshape(37, 53, 3)
    original = image.copy()
    expected = luminance(image)
    output = np.full((37, 53), -999.0, dtype=np.float32)
    work = np.full((37, 53), 999.0, dtype=np.float32)

    actual = luminance(image, _out=output, _work=work)

    assert actual is output
    assert np.array_equal(actual, expected)
    assert np.array_equal(image, original)
