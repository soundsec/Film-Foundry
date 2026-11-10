from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from film_foundry.core import io_utils
from film_foundry.core.electronic_negative import export_transparent_plate_set
from film_foundry.core.execution_topology import execution_topology
from film_foundry.model.config import FilmStockConfig


@pytest.mark.parametrize("optical", [False, True])
def test_transparent_rows_keep_all_renditions_and_source(tmp_path, monkeypatch, optical):
    from film_foundry.core import material_parallel
    monkeypatch.setattr(material_parallel, 'PARALLEL_MIN_PIXELS', 1)
    monkeypatch.setenv('FILM_FOUNDRY_MATERIAL_THREADS', '4')
    rng = np.random.default_rng(77)
    density = rng.uniform(-0.03, 2.2, (269, 257, 3)).astype(np.float32)
    density.setflags(write=False)
    film = FilmStockConfig()
    kwargs = dict(polarity="positive")
    if optical:
        kwargs.update(optical_density_rgb=density,
                      clear_base_optical_density_rgb=(0.1, 0.2, 0.05))
    with execution_topology("reference"):
        before = export_transparent_plate_set(density, film, tmp_path / "before", **kwargs)
    after = export_transparent_plate_set(density, film, tmp_path / "after", workers=4, **kwargs)
    assert before.keys() == after.keys()
    for key in before:
        np.testing.assert_array_equal(io_utils.cv_imread_unicode(before[key]),
                                      io_utils.cv_imread_unicode(after[key]))


def test_tiff_transport_chooses_compression_without_changing_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(io_utils, "_TIFF_SAMPLE_MIN_PIXELS", 1)
    rng = np.random.default_rng(13)
    for name, pixels, expected in (
        ("noise", rng.integers(0, 65536, (80, 129, 4), dtype=np.uint16), 1),
        ("flat", np.full((80, 129, 3), 40000, np.uint16), 5),
    ):
        path = tmp_path / (name + ".tiff")
        assert io_utils.cv_imwrite_unicode(path, pixels)
        with Image.open(path) as image:
            assert image.tag_v2[259] == expected
        np.testing.assert_array_equal(io_utils.cv_imread_unicode(path), pixels)
    with execution_topology("reference"):
        assert io_utils._tiff_write_params(pixels) == []
