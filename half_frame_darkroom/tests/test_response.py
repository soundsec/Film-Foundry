import numpy as np

from half_frame_darkroom.core.response import apply_film_response
from half_frame_darkroom.model.config import ChemistryConfig, FilmStockConfig


def test_response_keeps_values_displayable():
    image = np.linspace(0.0, 1.5, 96, dtype=np.float32).reshape(4, 8, 3)
    out = apply_film_response(image, FilmStockConfig(), ChemistryConfig(push_stops=1.0))
    assert out.dtype == np.float32
    assert out.min() >= 0.0
    assert out.max() <= 1.0


def test_exhaustion_lifts_fog():
    image = np.zeros((8, 8, 3), dtype=np.float32)
    film = FilmStockConfig()
    fresh = apply_film_response(image, film, ChemistryConfig(developer_exhaustion=0.0))
    tired = apply_film_response(image, film, ChemistryConfig(developer_exhaustion=1.0))
    assert tired.mean() > fresh.mean()

