import numpy as np

from half_frame_darkroom.core.density_grain import apply_density_grain
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def test_density_grain_is_repeatable_with_seed():
    density = np.ones((24, 24, 3), dtype=np.float32) * 1.0
    film = FilmStockConfig()
    chemistry = ChemistryConfig()
    first = apply_density_grain(density, film, chemistry, rng=np.random.default_rng(3))
    second = apply_density_grain(density, film, chemistry, rng=np.random.default_rng(3))
    assert np.allclose(first, second)


def test_density_grain_variance_grows_with_density():
    film = FilmStockConfig(grain_density_correlation_radius=0.0005)
    chemistry = ChemistryConfig()
    low = np.ones((48, 48, 3), dtype=np.float32) * 0.2
    high = np.ones((48, 48, 3), dtype=np.float32) * 1.4
    low_out = apply_density_grain(low, film, chemistry, rng=np.random.default_rng(4))
    high_out = apply_density_grain(high, film, chemistry, rng=np.random.default_rng(4))
    assert (high_out - high).std() > (low_out - low).std()


def test_grain_correlation_radius_changes_spatial_texture():
    density = np.ones((512, 512, 3), dtype=np.float32) * 1.2
    chemistry = ChemistryConfig()
    fine = FilmStockConfig(grain_density_correlation_radius=0.0005)
    coarse = FilmStockConfig(grain_density_correlation_radius=0.004)

    fine_out = apply_density_grain(density, fine, chemistry, rng=np.random.default_rng(5))
    coarse_out = apply_density_grain(density, coarse, chemistry, rng=np.random.default_rng(5))

    fine_neighbor_delta = np.abs(np.diff(fine_out[..., 0], axis=1)).mean()
    coarse_neighbor_delta = np.abs(np.diff(coarse_out[..., 0], axis=1)).mean()
    assert coarse_neighbor_delta < fine_neighbor_delta
