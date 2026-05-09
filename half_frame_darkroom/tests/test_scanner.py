import numpy as np

from half_frame_darkroom.core.scanner import normalize_scan_rgb


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

