import numpy as np

from half_frame_darkroom.core.sensitometry import exposure_to_density, hd_density_curve
from half_frame_darkroom.core.subtractive import density_to_positive_rgb
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def test_hd_density_increases_with_exposure():
    film = FilmStockConfig()
    chemistry = ChemistryConfig()
    exposure = np.array([[[0.01, 0.01, 0.01], [1.0, 1.0, 1.0]]], dtype=np.float32)
    density = hd_density_curve(exposure, film, chemistry)
    assert np.all(density[:, 1] > density[:, 0])


def test_density_stays_inside_physical_bounds():
    film = FilmStockConfig()
    density = exposure_to_density(np.ones((4, 4, 3), dtype=np.float32) * 4.0, film, ChemistryConfig())
    assert np.all(density >= np.asarray(film.density_min, dtype=np.float32))
    assert np.all(density <= np.asarray(film.density_max, dtype=np.float32))


def test_subtractive_positive_is_displayable():
    film = FilmStockConfig()
    density = np.ones((4, 4, 3), dtype=np.float32) * 1.2
    out = density_to_positive_rgb(density, film)
    assert out.shape == (4, 4, 3)
    assert out.min() >= 0.0
    assert out.max() <= 1.0

