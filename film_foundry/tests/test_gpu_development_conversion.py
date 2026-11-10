"""Inventory/history preservation at the fused numerical backend boundary."""
from dataclasses import replace
import os
import numpy as np
import pytest

from film_foundry.core import development_compute
from film_foundry.core.film_process.model import FilmProcessState
from film_foundry.core.film_process.formation_history import BathFormationResponse
from film_foundry.core.film_process.operators import (
    _develop_with_history, CompatibilityProfile, FilmProcessStep, FilmProcessAction,
)


def fixture(shape):
    rng = np.random.default_rng(37)
    h = rng.uniform(.001, 2., shape).astype(np.float32)
    e = rng.uniform(0., 1., shape).astype(np.float32)
    e[::13] = 0.; e[::17] = 1.; h[::19] = 0.
    state = FilmProcessState(halide=h, developability=e, metallic_silver=np.zeros_like(h),
                             coupler=np.full_like(h, .25), dye=np.zeros_like(h))
    step = FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=.73,
                           developability_gamma=1.7, highlight_compensation=.43,
                           formation_response=BathFormationResponse(.08, 1.3, .7, 1.1))
    local = rng.uniform(0., 2., shape[:-1]).astype(np.float32)
    return state, step, local


def test_device_unavailable_keeps_cpu_inventory_and_callback_order(monkeypatch):
    state, step, local = fixture((8, 11, 3))
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', 'cpu')
    expected = state.copy()
    _develop_with_history(expected, step, CompatibilityProfile(), True, local)
    monkeypatch.setattr(development_compute, 'eligible', lambda _: True)
    monkeypatch.setattr(development_compute, 'try_conversion', lambda *a: None)
    actual = state.copy()
    _develop_with_history(actual, step, CompatibilityProfile(), True, local)
    for key in ('halide', 'developability', 'metallic_silver', 'coupler', 'dye'):
        np.testing.assert_array_equal(getattr(actual, key), getattr(expected, key))
    for key, value in expected.formation_history.items():
        np.testing.assert_array_equal(actual.formation_history[key], value)
    class StatefulRate:
        def __init__(self): self.calls = []
        def rate_for_step(self, *args):
            self.calls.append('image'); return local
        def fog_rate_for_step(self, *args):
            self.calls.append('fog'); return local
    provider = StatefulRate()
    monkeypatch.setattr(development_compute, 'try_conversion', lambda *a: pytest.fail('unknown callback on GPU'))
    _develop_with_history(state, step, CompatibilityProfile(), True, provider)
    assert provider.calls == ['image', 'fog']
    from film_foundry.model.config import DarkroomConfig
    from film_foundry.core.process_operations import BathContactRate
    from film_foundry.core.film_process.operators import _try_development_conversion
    wrapped = BathContactRate(DarkroomConfig(), local.shape, base=provider,
                              prepare_surface=False, prepare_inhibitor=False)
    assert _try_development_conversion(state, step, CompatibilityProfile(), wrapped) is None
    assert provider.calls == ['image', 'fog']


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA') != '1', reason='explicit CUDA qualification only')
@pytest.mark.parametrize('layers,gamma,fog,layout', [(1,.7,0.,'none'), (3,1.7,.08,'plane'), (3,1.,.4,'layers')])
def test_cuda_conversion_matches_pools_and_history(monkeypatch, layers, gamma, fog, layout):
    state, step, local = fixture((513, 521, layers))
    step = replace(step, developability_gamma=gamma, formation_response=replace(step.formation_response, fog_dose=fog))
    if layout == 'none': local = None
    if layout == 'layers': local = np.broadcast_to(local[...,None], state.halide.shape)
    results = []
    before = development_compute.status()['cuda']
    for backend in ('cpu', 'cuda'):
        monkeypatch.setenv('FILM_FOUNDRY_COMPUTE', backend)
        value = state.copy()
        # Repeated development consumes residual activation and retains real moments.
        for _ in range(2):
            _develop_with_history(value, step, CompatibilityProfile(), True, local)
        results.append(value)
    assert development_compute.status()['cuda'] == before+2
    expected, actual = results
    for key in ('halide', 'developability', 'metallic_silver', 'coupler', 'dye'):
        # H^E/H is ill-conditioned near exhausted H. Judge the transported
        # active mass below as well as its derived bounded fraction.
        atol = 5e-6 if key == 'developability' else 2e-6
        np.testing.assert_allclose(getattr(actual,key), getattr(expected,key), rtol=2e-6, atol=atol)
    np.testing.assert_allclose(actual.halide*actual.developability,
                               expected.halide*expected.developability, rtol=2e-6, atol=2e-6)
    for key, value in expected.formation_history.items():
        np.testing.assert_allclose(actual.formation_history[key], value, rtol=2e-6, atol=2e-6)
    np.testing.assert_allclose(actual.halide+actual.metallic_silver, state.halide, rtol=2e-6, atol=2e-6)
    np.testing.assert_allclose(actual.coupler+actual.dye, .25, rtol=2e-6, atol=2e-6)
    for key, value in expected.formation_created.items():
        assert actual.formation_created[key] == pytest.approx(value, rel=2e-6, abs=2e-6)
