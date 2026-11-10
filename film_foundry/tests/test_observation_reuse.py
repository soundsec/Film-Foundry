from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from film_foundry.core import engine, optical_observation
from film_foundry.core.execution_topology import execution_topology
from film_foundry.core.observation_reuse import observation_reuse_scope, observe_density, _current
from film_foundry.core.spectral_optics import SpectralMedium, spectral_observation_density
from film_foundry.tests.test_spectral_compute import profiles
from film_foundry.model.config import DarkroomConfig


def test_tiled_observation_reused_without_aliasing_or_changing_material():
    material, observer = profiles(9)
    medium = SpectralMedium(material, np.full((13, 17, 9), .2, np.float32), (0.,)*9)
    expected = spectral_observation_density(medium, observer)
    calls = []
    def compute(*args, **kwargs):
        calls.append(kwargs)
        return spectral_observation_density(*args, **kwargs)
    with observation_reuse_scope():
        for start in range(0, 13, 4):
            value = observe_density(medium, observer, slice(start, start+4), compute)
            value[:] = 99  # A consumer may alter even the first, computed result.
        np.testing.assert_array_equal(observe_density(medium, observer, None, compute), expected)
        value = observe_density(medium, observer, None, compute)
        value[:] = 88
        np.testing.assert_array_equal(observe_density(medium, observer, None, compute), expected)
        assert len(calls) == 4
        # Same profile id, different physical observer must not reuse stale data.
        changed = replace(observer, response_rgb=tuple(reversed(observer.response_rgb)))
        observe_density(medium, changed, None, compute)
        assert len(calls) == 5
    assert _current.get() is None
    assert np.all(medium.component_amounts == np.float32(.2))
    assert not medium.component_amounts.flags.writeable


@pytest.mark.parametrize('reference,budget,disabled', [(True, 10000, False), (False, 0, False), (False, 10000, True)])
def test_reference_and_budget_disable_optional_reuse(reference, budget, disabled, monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_OBSERVATION_REUSE', '0' if disabled else '1')
    material, observer = profiles(4)
    medium = SpectralMedium(material, np.zeros((3, 4, 9), np.float32), (0.,)*9)
    calls = []
    def compute(*args, **kwargs):
        calls.append(1)
        return spectral_observation_density(*args, **kwargs)
    with execution_topology('reference' if reference else 'optimized'), observation_reuse_scope(budget):
        for _ in range(2):
            observe_density(medium, observer, None, compute)
    assert len(calls) == 2


def test_nested_scope_restored_and_failure_releases_cache():
    assert _current.get() is None
    with pytest.raises(RuntimeError), observation_reuse_scope():
        outer = _current.get()
        with observation_reuse_scope():
            assert _current.get() is not outer
        assert _current.get() is outer
        raise RuntimeError('export failed')
    assert _current.get() is None and outer['entry'] is None


@pytest.mark.parametrize('process', ['color_negative', 'color_reversal'])
def test_real_file_reuses_scan_for_export_and_preserves_pixels(tmp_path, monkeypatch, process):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cpu')
    material, observer = profiles(9)
    cfg = DarkroomConfig(enable_grain=False, enable_mtf=False, enable_halation=False)
    cfg.seed_strategy, cfg.random_seed = 'fixed', 123
    cfg.film.spectral_material_profile = material
    cfg.scanner.spectral_observation_profile = observer
    cfg.output.format = 'png'
    cfg.output.anti_banding_strength = 0
    cfg.output.save_scanner_raw = True
    cfg.output.export_transparent_plate = False
    cfg.chemistry.chemical_stain = .2
    cfg.chemistry.program_key = process
    cfg.processing.scan_tile_rows = 4
    cfg.processing.scan_tile_threshold_megapixels = .000001
    source = tmp_path/'source.png'
    Image.fromarray(np.arange(18*24*3, dtype=np.uint8).reshape(18,24,3)).save(source)
    original = optical_observation.spectral_observation_density
    work = []
    def tracked(medium, observer, **kwargs):
        if not kwargs.get('clear_base'):
            row = kwargs.get('row_slice') or slice(None)
            work.append(medium.component_amounts[row].shape[0])
        return original(medium, observer, **kwargs)
    monkeypatch.setattr(optical_observation, 'spectral_observation_density', tracked)
    engine._process_file_impl(source, tmp_path/'before'/'image.png', cfg)
    baseline_rows = sum(work)
    work.clear()
    engine.process_file(source, tmp_path/'after'/'image.png', cfg)
    assert sum(work) == 18 and baseline_rows > sum(work)
    assert _current.get() is None
    before = tmp_path/'before'
    for path in before.iterdir():
        if path.suffix in ('.png', '.tiff'):
            with Image.open(path) as a, Image.open(tmp_path/'after'/path.name) as b:
                np.testing.assert_array_equal(np.asarray(a), np.asarray(b))
