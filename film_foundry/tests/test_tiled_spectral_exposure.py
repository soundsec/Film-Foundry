import numpy as np
import pytest

from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
from film_foundry.core import exposure_compute, layer_compute as gpu
from film_foundry.core.spectral_exposure import map_profile_exposure


@pytest.mark.parametrize('stage', ['spectrum', 'latent'])
def test_partial_device_result_is_discarded_for_wide_strided_input(monkeypatch, stage):
    image = np.random.default_rng(3).random((3, 29, 3), dtype=np.float32)[:, ::-1]
    image.setflags(write=False)
    profile = make_smits_exposure_profile(3)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cpu')
    if stage == 'spectrum':
        calculate = lambda: map_profile_exposure(image, profile)
    else:
        from film_foundry.core.engine import _exposure_material
        from film_foundry.model.config import FilmStockConfig
        material = _exposure_material(FilmStockConfig())
        calculate = lambda: material.expose(image).developability
    expected = calculate()
    sizes, errors = [], []

    def dispatch(tile, out):
        sizes.append(tile.size // 3)
        if len(sizes) == 3:
            raise RuntimeError('device readback failed')
        out.fill(999.)

    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cuda')
    monkeypatch.delenv('FILM_FOUNDRY_EXPOSURE_GPU', raising=False)
    monkeypatch.setattr(gpu, 'MIN_PIXELS', 1)
    monkeypatch.setattr(gpu, 'TILE_PIXELS', 12)
    monkeypatch.setattr(gpu.compute, '_failure', None)
    monkeypatch.setattr(gpu.compute, 'reject', errors.append)
    monkeypatch.setattr(gpu, '_cuda', lambda table, capacity, channels, name, input_channels: dispatch)
    calls = exposure_compute.status(), exposure_compute.curve_status()
    np.testing.assert_array_equal(calculate(), expected)
    assert sizes == [12, 12, 5] and len(errors) == 1
    assert (exposure_compute.status(), exposure_compute.curve_status()) == calls
