import numpy as np

from half_frame_darkroom.core.halation import apply_halation, halation_source_energy, soft_threshold
from half_frame_darkroom.model.config import FilmStockConfig


def test_soft_threshold_is_smooth_and_bounded():
    values = np.array([0.0, 0.45, 0.5, 0.55, 1.0], dtype=np.float32)
    mask = soft_threshold(values, threshold=0.5, softness=0.1)
    assert mask[0] == 0.0
    assert mask[-1] == 1.0
    assert np.all(np.diff(mask) >= 0.0)


def test_halation_responds_to_highlights_only():
    film = FilmStockConfig(halation_strength=0.5, halation_threshold=0.7, halation_peak_radius=0.04)
    dark = np.full((32, 32, 3), 0.1, dtype=np.float32)
    bright = dark.copy()
    bright[16, 16] = 1.0

    dark_out = apply_halation(dark, film)
    bright_out = apply_halation(bright, film)

    assert np.allclose(dark_out, dark, atol=1e-4)
    assert bright_out.mean() > bright.mean()


def test_halation_source_prefers_local_peaks_over_broad_bright_areas():
    film = FilmStockConfig(halation_threshold=0.7, halation_peak_radius=0.04)
    broad = np.full((64, 64, 3), 0.95, dtype=np.float32)
    peaked = np.full((64, 64, 3), 0.45, dtype=np.float32)
    peaked[32, 32] = 1.0

    broad_source = halation_source_energy(broad, film)
    peaked_source = halation_source_energy(peaked, film)

    assert peaked_source.max() > broad_source.max()
    assert broad_source.mean() < peaked_source.mean()
