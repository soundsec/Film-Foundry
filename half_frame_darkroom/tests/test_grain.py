import numpy as np

from half_frame_darkroom.core.grain import apply_grain, midtone_weight
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def test_midtone_weight_peaks_near_mu():
    luma = np.array([0.1, 0.5, 0.9], dtype=np.float32)
    weights = midtone_weight(luma, mu=0.5, sigma=0.2)
    assert weights[1] > weights[0]
    assert weights[1] > weights[2]


def test_grain_is_repeatable_with_seed():
    image = np.full((32, 32, 3), 0.5, dtype=np.float32)
    film = FilmStockConfig(grain_strength=0.04)
    chemistry = ChemistryConfig(push_stops=1.0)
    first = apply_grain(image, film, chemistry, rng=np.random.default_rng(7))
    second = apply_grain(image, film, chemistry, rng=np.random.default_rng(7))
    assert np.allclose(first, second)
    assert not np.allclose(first, image)

