import numpy as np

from half_frame_darkroom.core.mtf import apply_emulsion_mtf
from half_frame_darkroom.model.config import FilmStockConfig


def test_emulsion_mtf_keeps_flat_fields_stable():
    image = np.full((32, 32, 3), 0.45, dtype=np.float32)
    film = FilmStockConfig(emulsion_mtf_strength=0.8, digital_artifact_suppression=0.8)
    out = apply_emulsion_mtf(image, film)
    assert np.allclose(out, image, atol=1e-5)


def test_emulsion_mtf_reduces_single_pixel_frequency():
    checker = (np.indices((32, 32)).sum(axis=0) % 2).astype(np.float32)
    image = np.repeat(checker[..., None], 3, axis=-1)
    film = FilmStockConfig(
        emulsion_mtf_strength=0.9,
        emulsion_blur_radius=0.004,
        digital_artifact_suppression=0.4,
    )
    out = apply_emulsion_mtf(image, film)
    assert out.std() < image.std()

