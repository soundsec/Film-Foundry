import numpy as np

from film_foundry.core.preview import negative_visual_preview
from film_foundry.model.config import FilmStockConfig


def test_negative_visual_preview_inverts_density_brightness():
    film = FilmStockConfig()
    density = np.array([[[0.2, 0.2, 0.2], [1.0, 1.0, 1.0]]], dtype=np.float32)

    preview = negative_visual_preview(density, film)
    luma = preview[..., 0] * 0.2126 + preview[..., 1] * 0.7152 + preview[..., 2] * 0.0722

    assert preview.shape == (1, 2, 3)
    assert preview.min() >= 0.0
    assert preview.max() <= 1.0
    assert luma[0, 1] < luma[0, 0]


def test_negative_visual_preview_uses_film_base_density_color():
    density = np.zeros((1, 1, 3), dtype=np.float32)
    red_dense_base = FilmStockConfig(film_base_density_rgb=(0.8, 0.0, 0.0))
    green_dense_base = FilmStockConfig(film_base_density_rgb=(0.0, 0.8, 0.0))

    red_dense_preview = negative_visual_preview(density, red_dense_base)[0, 0]
    green_dense_preview = negative_visual_preview(density, green_dense_base)[0, 0]

    assert red_dense_preview[0] < red_dense_preview[1]
    assert green_dense_preview[1] < green_dense_preview[0]
