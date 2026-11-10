import numpy as np
import pytest
from film_foundry.core import desktop_compute
from film_foundry.core.scanner import layer_density_to_optical_density_rgb
from film_foundry.core.execution_topology import execution_topology
from film_foundry.model.config import FilmStockConfig


def test_cpu_cache_tiles_keep_reference_pixels_and_input(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    data=np.random.default_rng(2).uniform(-.2,8,(513,529,3)).astype('float32');data.setflags(write=False)
    for strength in (0.,.8):
        film=FilmStockConfig(base_dye_interaction_strength=strength)
        with execution_topology('reference'):expected=layer_density_to_optical_density_rgb(data,film)
        np.testing.assert_array_equal(layer_density_to_optical_density_rgb(data,film),expected)


def test_failure_latches_and_cpu_fallback(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    monkeypatch.setattr(desktop_compute,'_failure','test unavailable')
    data=np.ones((512,512,3),np.float32)
    actual=layer_density_to_optical_density_rgb(data,FilmStockConfig())
    with execution_topology('reference'):expected=layer_density_to_optical_density_rgb(data,FilmStockConfig())
    np.testing.assert_array_equal(actual,expected)


@pytest.mark.skipif(__import__('os').environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='Explicit real GPU test')
def test_cuda_matches_cpu_and_sliced_input(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    data=np.random.default_rng(4).uniform(-.2,9,(513,1030,3)).astype('float32')[:,::2];data.setflags(write=False)
    for strength in (0.,.8):
        film=FilmStockConfig(base_dye_interaction_strength=strength)
        with execution_topology('reference'):expected=layer_density_to_optical_density_rgb(data,film)
        before=desktop_compute.status()['calls']
        actual=layer_density_to_optical_density_rgb(data,film)
        assert desktop_compute.status()['calls']>before
        assert desktop_compute.status()['failure'] is None
        np.testing.assert_allclose(actual,expected,rtol=2e-6,atol=2e-6)


@pytest.mark.skipif(__import__('os').environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='Explicit real GPU test')
def test_full_pipeline_gpu_preserves_image_and_final_pools(monkeypatch):
    from film_foundry.tools.benchmark_pipeline import synthetic_reference_image
    from film_foundry.core.engine import develop_negative,scan_negative
    from film_foundry.model.config import DarkroomConfig
    cfg=DarkroomConfig(random_seed=123,seed_strategy='fixed')
    image=synthetic_reference_image(768,512)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    cpu=develop_negative(image,cfg);expected=scan_negative(cpu,cfg)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    gpu=develop_negative(image,cfg);actual=scan_negative(gpu,cfg)
    np.testing.assert_allclose(gpu.optical_density_rgb,cpu.optical_density_rgb,rtol=2e-6,atol=2e-6)
    np.testing.assert_allclose(actual.output_srgb,expected.output_srgb,rtol=3e-5,atol=3e-5)
