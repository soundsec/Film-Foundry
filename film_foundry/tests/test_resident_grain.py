import os
import numpy as np
import pytest
from film_foundry.core import grain_compute, desktop_compute
from film_foundry.core.density_grain import _blurred_multilayer_field
from film_foundry.core.film_process.local_grain import prepare_radius_blend


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA') != '1', reason='explicit CUDA qualification')
@pytest.mark.parametrize('local', ['none', 'constant', 'varying'])
def test_resident_grain_preserves_rng_and_local_filtering(monkeypatch, local):
    shape = (1025,1024)
    radius = plans = None
    if local != 'none':
        radius = np.ones((*shape,3),np.float32)
        if local == 'varying': radius *= np.linspace(.6,1.8,shape[1],dtype=np.float32)[None,:,None]
        plans = (prepare_radius_blend(radius.mean(axis=-1)), *(prepare_radius_blend(radius[...,c]) for c in range(3)))
    results=[]; states=[]; count=grain_compute.status()
    for backend in ('cpu','cuda'):
        monkeypatch.setenv('FILM_FOUNDRY_COMPUTE',backend)
        rng=np.random.default_rng(23)
        results.append(_blurred_multilayer_field(rng,shape,.0013,radius_response=radius,radius_plans=plans))
        states.append(rng.bit_generator.state)
    assert grain_compute.status()==count+1
    assert states[0]==states[1]
    np.testing.assert_allclose(results[0],results[1],atol=2e-5,rtol=2e-5)


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA') != '1', reason='explicit CUDA qualification')
def test_failed_device_pass_restores_rng_and_output(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    monkeypatch.setattr(desktop_compute,'_failure',None)
    rng=np.random.default_rng(4);before=rng.bit_generator.state
    out=np.full((1025,1024,3),7.,np.float32)
    def fail(cp, rng, *args):
        rng.standard_normal(100)
        raise RuntimeError('injected device failure')
    monkeypatch.setattr(grain_compute,'_run',fail)
    with pytest.warns(RuntimeWarning,match='injected device failure'):
        result=grain_compute.try_multilayer(rng,(1025,1024),1.2,.32,out,None,None)
    assert result is None and rng.bit_generator.state==before
    assert np.all(out==7.)
