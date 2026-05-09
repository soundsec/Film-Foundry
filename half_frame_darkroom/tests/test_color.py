import numpy as np

from half_frame_darkroom.core.color import linear_to_srgb, luminance, srgb_to_linear


def test_srgb_round_trip_is_close():
    image = np.linspace(0.0, 1.0, 64, dtype=np.float32).reshape(8, 8, 1)
    image = np.repeat(image, 3, axis=-1)
    round_trip = linear_to_srgb(srgb_to_linear(image))
    assert np.allclose(round_trip, image, atol=1e-5)


def test_luminance_preserves_shape():
    image = np.ones((5, 7, 3), dtype=np.float32)
    assert luminance(image).shape == (5, 7)

