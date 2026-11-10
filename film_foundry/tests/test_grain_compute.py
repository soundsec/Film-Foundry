import os
import numpy as np
import pytest
import cv2
from film_foundry.core.density_grain import _normalize_unit_field,_blurred_multilayer_field
from film_foundry.core.execution_topology import execution_topology
from film_foundry.core import desktop_compute


def test_center_once_preserves_reference_bits():
    for shape in ((513,529),(127,129,3)):
        data=np.random.default_rng(11).standard_normal(shape).astype('float32')
        with execution_topology('reference'):expected=_normalize_unit_field(data.copy())
        np.testing.assert_array_equal(_normalize_unit_field(data.copy()),expected)


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='Explicit GPU check')
@pytest.mark.parametrize('sigma',[2.5,3.,8.,20.,56.,80.])
def test_blur_preserves_full_frame_and_tile_seams(monkeypatch,sigma):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    monkeypatch.setenv('FILM_FOUNDRY_EXPERIMENTAL_GRAIN_CUDA','1')
    source=np.random.default_rng(4).standard_normal((2101,2103) if sigma>16 else (1049,1051)).astype('float32');source.setflags(write=False)
    result=desktop_compute.try_grain_blur(source,sigma)
    assert result is not None,desktop_compute.status()
    np.testing.assert_allclose(result,cv2.GaussianBlur(source,(0,0),sigma),atol=2e-6,rtol=2e-6)


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='Explicit GPU check')
def test_grain_keeps_random_stream_and_layer_field(monkeypatch):
    shape=(1049,1051);a=np.random.default_rng(18);b=np.random.default_rng(18)
    with execution_topology('reference'):expected=_blurred_multilayer_field(a,shape,.006)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    monkeypatch.setenv('FILM_FOUNDRY_EXPERIMENTAL_GRAIN_CUDA','1')
    before=desktop_compute.status()['blur_calls']
    actual=_blurred_multilayer_field(b,shape,.006)
    assert desktop_compute.status()['blur_calls']==before+4
    np.testing.assert_array_equal(a.random(10),b.random(10))
    np.testing.assert_allclose(actual,expected,atol=4e-6,rtol=4e-6)


def test_pipelined_grain_is_bitwise_identical_and_preserves_rng(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    a=np.random.default_rng(18);b=np.random.default_rng(18)
    with execution_topology('reference'):expected=_blurred_multilayer_field(a,(1049,1051),.003)
    actual=_blurred_multilayer_field(b,(1049,1051),.003)
    np.testing.assert_array_equal(actual,expected)
    np.testing.assert_array_equal(a.random(10),b.random(10))


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='Explicit GPU check')
def test_large_field_default_auto_and_explicit_opt_out(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','auto')
    monkeypatch.delenv('FILM_FOUNDRY_EXPERIMENTAL_GRAIN_CUDA',raising=False)
    monkeypatch.delenv('FILM_FOUNDRY_GRAIN_CUDA',raising=False)
    source=np.random.default_rng(15).standard_normal((2901,2903)).astype('float32')
    before=desktop_compute.status()['blur_calls']
    result=desktop_compute.try_grain_blur(source,20.)
    assert desktop_compute.status()['blur_calls']==before+1
    np.testing.assert_allclose(result,cv2.GaussianBlur(source,(0,0),20.),atol=2e-6,rtol=2e-6)
    monkeypatch.setenv('FILM_FOUNDRY_GRAIN_CUDA','0')
    assert desktop_compute.try_grain_blur(source,20.) is None
    monkeypatch.delenv('FILM_FOUNDRY_GRAIN_CUDA')
    assert desktop_compute.try_grain_blur(source[:100],20.) is None
