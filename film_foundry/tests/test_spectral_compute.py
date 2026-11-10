import os
import subprocess

import numpy as np
import pytest

from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile
from film_foundry.core import desktop_compute, opencl_compute
from film_foundry.core.execution_topology import execution_topology
from film_foundry.core.spectral_optics import SpectralMedium, capture_spectral_medium


def profiles(bands=81):
    wave = np.linspace(380, 780, bands)
    absorption = np.stack([.05 + .9*np.exp(-((wave-center)/70)**2) for center in np.linspace(420, 690, 9)])
    response = np.stack([np.exp(-((wave-center)/55)**2) for center in (620, 540, 450)])
    return (SpectralMaterialProfile('compute-test', tuple(wave), tuple(map(tuple, absorption))),
            SpectralObservationProfile('compute-test', tuple(wave), tuple(np.ones(bands)), tuple(map(tuple, response))))


@pytest.mark.parametrize('timeout', [False, True])
def test_opencl_crashing_probe_never_enters_host_driver(monkeypatch, timeout):
    monkeypatch.setattr(opencl_compute, '_runtime', None)
    monkeypatch.setattr(opencl_compute, '_failure', None)
    def failed_probe(*args, **kwargs):
        if timeout:
            raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
        return subprocess.CompletedProcess([], 1, '', '')
    monkeypatch.setattr(subprocess, 'run', failed_probe)
    def forbidden(*a):
        pytest.fail('Failed child must never load the driver in the host')
    monkeypatch.setattr(opencl_compute, '_create_runtime', forbidden)
    with pytest.warns(RuntimeWarning, match='probe failed|TimeoutExpired'):
        assert not opencl_compute.available()
    assert not opencl_compute.available()  # latched, no retry storm


@pytest.mark.parametrize('opencl_available', [False, True])
def test_auto_unavailable_cuda_tries_opencl_then_cpu(monkeypatch, opencl_available):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'auto')
    monkeypatch.setattr(desktop_compute, '_auto_backend', None)
    monkeypatch.setattr(desktop_compute, '_failure', None)
    def unavailable():
        raise ImportError('CUDA not installed')
    monkeypatch.setattr(desktop_compute, '_get_runtime', unavailable)
    monkeypatch.setattr(opencl_compute, 'available', lambda: opencl_available)
    assert desktop_compute.selected_backend() == ('opencl' if opencl_available else 'cpu')


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_OPENCL') != '1', reason='Explicit real GPU test')
def test_real_opencl_dye_rgb_and_legacy(monkeypatch):
    from film_foundry.core.scanner import layer_density_to_optical_density_rgb
    from film_foundry.model.config import FilmStockConfig
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'opencl')
    values = np.random.default_rng(34).uniform(-.2, 8, (513, 1030, 3)).astype(np.float32)[:, ::2]
    values.setflags(write=False)
    for strength in (0., .8):
        film = FilmStockConfig(base_dye_interaction_strength=strength)
        with execution_topology('reference'):
            expected = layer_density_to_optical_density_rgb(values, film)
        calls = opencl_compute.status()['dye']
        actual = layer_density_to_optical_density_rgb(values, film)
        assert opencl_compute.status()['dye'] == calls+1
        assert opencl_compute.status()['failure'] is None
        np.testing.assert_allclose(actual, expected, rtol=2e-6, atol=2e-6)


def test_spectral_bad_gpu_result_falls_back_and_leaves_medium_unchanged(monkeypatch):
    material, observer = profiles(4)
    values = np.full((256, 257, 9), .2, np.float32)
    overlay = np.full((256, 257, 3), -.1, np.float32)
    medium = SpectralMedium(material, values, (0.,)*9, overlay)
    with execution_topology('reference'):
        expected = capture_spectral_medium(medium, observer)
    monkeypatch.setattr(desktop_compute, 'try_spectral', lambda *a: np.zeros_like(expected))
    reasons = []
    monkeypatch.setattr(desktop_compute, 'reject', reasons.append)
    np.testing.assert_array_equal(capture_spectral_medium(medium, observer), expected)
    assert reasons == ['Spectral kernel exceeded CPU tolerance']
    assert not values.flags.writeable and not overlay.flags.writeable
    assert np.all(values == np.float32(.2)) and np.all(overlay == np.float32(-.1))


@pytest.mark.parametrize('backend', ['cuda', 'opencl'])
@pytest.mark.parametrize('with_overlay', [False, True])
def test_real_spectral_gpu_full_bands_tiles_and_readonly_slices(monkeypatch, backend, with_overlay):
    if os.environ.get('FILM_FOUNDRY_TEST_'+backend.upper()) != '1':
        pytest.skip('Explicit real GPU test')
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', backend)
    material, observer = profiles()
    rng = np.random.default_rng(117)
    values = rng.uniform(0, 3, (519, 1030, 9)).astype(np.float32)[:, ::2]
    values[0, 0] = 0
    values[-1, -1] = 100  # density cap and dark-tail behavior
    overlay = rng.uniform(-2, 1, values.shape[:2]+(3,)).astype(np.float32) if with_overlay else None
    medium = SpectralMedium(material, values, (0.,)*9, overlay)
    with execution_topology('reference'):
        expected = capture_spectral_medium(medium, observer)
    def calls():
        return desktop_compute.status()['spectral_calls'] if backend == 'cuda' else opencl_compute.status()['spectral']
    before = calls()
    actual = capture_spectral_medium(medium, observer)
    assert calls() == before+1
    failure = desktop_compute.status()['failure'] if backend == 'cuda' else opencl_compute.status()['failure']
    assert failure is None
    np.testing.assert_allclose(actual, expected, rtol=2e-7, atol=1e-35)
    actual_slice = capture_spectral_medium(medium, observer, row_slice=slice(1, 400, 2))
    assert calls() == before+2
    np.testing.assert_allclose(actual_slice, expected[1:400:2], rtol=2e-7, atol=1e-35)
    with execution_topology('reference'):
        clear = capture_spectral_medium(medium, observer, clear_base=True)
    np.testing.assert_array_equal(capture_spectral_medium(medium, observer, clear_base=True), clear)
    assert calls() == before+2  # no GPU work for a 1-pixel clear-base observation
    assert not medium.component_amounts.flags.writeable


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA') != '1', reason='Explicit real GPU test')
def test_full_spectral_scan_keeps_pools_and_matches_cpu(monkeypatch):
    from film_foundry.core.engine import develop_negative, scan_negative
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.tools.benchmark_pipeline import synthetic_reference_image
    cfg = DarkroomConfig(random_seed=72, seed_strategy='fixed', enable_grain=False)
    cfg.film.spectral_material_profile, cfg.scanner.spectral_observation_profile = profiles()
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cpu')
    medium = develop_negative(synthetic_reference_image(512, 256), cfg)
    before = medium.spectral_medium.component_amounts.copy()
    expected = scan_negative(medium, cfg)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cuda')
    calls = desktop_compute.status()['spectral_calls']
    actual = scan_negative(medium, cfg)
    assert desktop_compute.status()['spectral_calls'] > calls
    np.testing.assert_allclose(actual.output_srgb, expected.output_srgb, rtol=3e-6, atol=3e-6)
    np.testing.assert_array_equal(medium.spectral_medium.component_amounts, before)
