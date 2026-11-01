import numpy as np
import pytest

from film_foundry.core.antibanding import (
    _coordinate_noise, _coordinate_noise_region,
    apply_output_antibanding, apply_output_antibanding_region,
)


@pytest.mark.parametrize('shape', [(2, 2), (7, 11), (73, 91)])
def test_dither_is_coordinate_stable_across_row_partitions(shape):
    height, width = shape
    whole = _coordinate_noise(height, width)
    for rows in (1, 5, 32):
        tiled = np.concatenate([
            _coordinate_noise_region(y, min(y + rows, height), width, height)
            for y in range(0, height, rows)
        ])
        assert np.array_equal(whole, tiled)
    assert np.array_equal(whole, _coordinate_noise(height, width))


def test_dither_is_bounded_centered_and_decorrelated():
    noise = _coordinate_noise(256, 384)
    assert noise.dtype == np.float32
    assert np.isfinite(noise).all() and np.max(np.abs(noise)) <= 0.5
    assert np.max(np.abs(noise.mean(axis=(0, 1)))) < 0.003
    assert np.all((noise.std(axis=(0, 1)) > 0.19) & (noise.std(axis=(0, 1)) < 0.22))
    for channel in range(3):
        values = noise[..., channel]
        assert abs(np.corrcoef(values[:, :-1].ravel(), values[:, 1:].ravel())[0, 1]) < 0.02
        assert abs(np.corrcoef(values[:-1].ravel(), values[1:].ravel())[0, 1]) < 0.02
    cross = np.corrcoef(noise.reshape(-1, 3), rowvar=False) - np.eye(3)
    assert np.max(np.abs(cross)) < 0.02


def test_output_dither_preserves_input_and_amplitude_and_tile_edges():
    ramp = np.broadcast_to(np.linspace(0.2, 0.8, 91, dtype=np.float32)[None, :, None], (73, 91, 3)).copy()
    original = ramp.copy()
    output = apply_output_antibanding(ramp, 0.73)
    assert np.array_equal(ramp, original)
    assert np.max(np.abs(output - ramp)) <= 0.73 * 1.35 / 510 + 1e-7
    assert np.array_equal(apply_output_antibanding(ramp, 0), ramp)
    tiled = np.concatenate([apply_output_antibanding_region(ramp, 0.73, y, min(y + 11, 73)) for y in range(0, 73, 11)])
    np.testing.assert_allclose(output, tiled, rtol=0, atol=1e-7)
