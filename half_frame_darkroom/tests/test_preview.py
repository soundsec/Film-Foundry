import numpy as np

from half_frame_darkroom.core.preview import negative_visual_preview
from half_frame_darkroom.model.config import FilmStockConfig


def test_negative_visual_preview_inverts_density_brightness():
    film = FilmStockConfig()
    density = np.array([[[0.2, 0.2, 0.2], [1.0, 1.0, 1.0]]], dtype=np.float32)

    preview = negative_visual_preview(density, film)
    luma = preview[..., 0] * 0.2126 + preview[..., 1] * 0.7152 + preview[..., 2] * 0.0722

    assert preview.shape == (1, 2, 3)
    assert preview.min() >= 0.0
    assert preview.max() <= 1.0
    assert luma[0, 1] < luma[0, 0]
