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


def test_development_recipe_changes_density_response():
    film = FilmStockConfig()
    exposure = np.ones((4, 4, 3), dtype=np.float32) * 0.6
    short_dev = ChemistryConfig(time_min=4.0, temperature_c=18.0, concentration=0.7)
    push_dev = ChemistryConfig(developer_type="push", push_stops=1.0, time_min=10.0, temperature_c=24.0)

    short_density = exposure_to_density(exposure, film, short_dev)
    push_density = exposure_to_density(exposure, film, push_dev)

    assert push_density.mean() > short_density.mean()


def test_extreme_development_recipe_keeps_density_finite_and_bounded():
    film = FilmStockConfig()
    image = np.linspace(0.0, 4.0, 6 * 7 * 3, dtype=np.float32).reshape(6, 7, 3)
    chemistry = ChemistryConfig(
        time_min=100000.0,
        temperature_c=250.0,
        concentration=1000.0,
        agitation=100.0,
        push_stops=100.0,
        developer_exhaustion=50.0,
        fixer_exhaustion=50.0,
        compensation=50.0,
        silver_retention=50.0,
    )

    density = exposure_to_density(image, film, chemistry)

    assert np.isfinite(density).all()
    assert density.shape == image.shape
    assert density.min() >= 0.0
    assert density.max() <= max(film.density_max) * 1.35


def test_subtractive_positive_is_displayable():
    film = FilmStockConfig()
    density = np.ones((4, 4, 3), dtype=np.float32) * 1.2
    out = density_to_positive_rgb(density, film)
    assert out.shape == (4, 4, 3)
    assert out.min() >= 0.0
    assert out.max() <= 1.0
