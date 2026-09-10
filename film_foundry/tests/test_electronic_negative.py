from types import SimpleNamespace

import numpy as np
from PIL import Image

from film_foundry.core import electronic_negative
from film_foundry.core.electronic_negative import export_layer_pack, export_plate_set
from film_foundry.model.config import FilmStockConfig


def test_layer_pack_density_alpha_and_grain_layer_share_grained_density(tmp_path):
    density_cmy = np.zeros((8, 8, 3), dtype=np.float32)
    density_grain = density_cmy.copy()
    density_grain[3, 4] = (0.8, 0.8, 0.8)
    empty_rgb = np.zeros((8, 8, 3), dtype=np.float32)
    negative = SimpleNamespace(
        density_cmy=density_cmy,
        density_grain=density_grain,
        after_mtf=empty_rgb,
        after_halation=empty_rgb,
    )

    paths = export_layer_pack(negative, FilmStockConfig(), tmp_path / "pack")
    density_alpha = np.asarray(Image.open(paths["density_alpha_png"]), dtype=np.uint8)
    grain_layer = np.asarray(Image.open(paths["grain_layer"]), dtype=np.uint8)

    assert density_alpha[3, 4] > 0
    assert grain_layer[3, 4] > 0
    assert density_alpha.sum() == density_alpha[3, 4]
    assert grain_layer.sum() == grain_layer[3, 4]
    assert paths["postprocess_density_delta_layer"] == paths["grain_layer"]


def test_plate_export_reuses_one_halation_luma_transform(tmp_path, monkeypatch):
    after_mtf = np.linspace(0.0, 0.7, 7 * 9 * 3, dtype=np.float32).reshape(7, 9, 3)
    after_halation = np.clip(after_mtf + 0.03, 0.0, 1.0)
    density_cmy = np.clip(after_mtf * 1.2, 0.0, 1.0)
    density_grain = np.clip(density_cmy + 0.01, 0.0, 1.0)
    original = electronic_negative._halation_luma
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(electronic_negative, "_halation_luma", counted)
    paths = export_plate_set(
        density_cmy,
        density_grain,
        after_mtf,
        after_halation,
        FilmStockConfig(),
        tmp_path / "plates",
    )

    assert calls == 1
    preview = np.asarray(Image.open(paths["halation_layer_preview"]), dtype=np.uint8)
    linear = np.asarray(Image.open(paths["halation_layer_linear"]), dtype=np.uint8)
    expected_luma = original(after_mtf, after_halation)
    expected_preview = np.round(
        electronic_negative._normalize_channel(expected_luma) * 255.0
    ).astype(np.uint8)
    expected_linear = np.round(np.clip(expected_luma / 0.05, 0.0, 1.0) * 255.0).astype(
        np.uint8
    )
    assert np.array_equal(preview, expected_preview)
    assert np.array_equal(linear, expected_linear)
