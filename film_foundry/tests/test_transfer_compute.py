"""Targeted device parity and atomic fallback of finite transfer chemistry."""
import os
import numpy as np
import pytest
from film_foundry.core import peel_apart as peel, integral_instant as instant
from film_foundry.core import transfer_compute as gpu,desktop_compute as compute
from film_foundry.tests.test_transfer_inventory import contact,sealed,same


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='explicit CUDA qualification only')
@pytest.mark.parametrize('kind',['peel','silver','integral','dark'])
def test_cuda_transfer_preserves_inventory_and_partition(monkeypatch,kind):
    pytest.importorskip('cupy')
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    monkeypatch.setattr(gpu,'MIN_PIXELS',0)
    if kind=='integral':
        model=instant
        base=sealed(instant.InstantMaterial(neutralization_delay_seconds=40,neutralization_seconds=50),
            reagent_leak=.3,spread_unevenness=.5,temperature_c=32,light_protection=.1,ambient_light=3,spread_seconds=5)
    else:
        model=peel
        base=contact(peel.PeelMaterial(transfer='silver_compound' if kind=='silver' else 'dye',
            neutralizer_capacity=.4 if kind=='silver' else 0.,developer_capacity=.3),
            reagent_load=.8,spread_seconds=8,coverage=.9,contact=.75,reagent_leak=.2)
        if kind!='dark':base=peel.environment(base,light_protection=.2,ambient_light=2,temperature_c=30)
    before={k:v.copy() for k,v in base.fields.items()}
    expected=model.advance(base,seconds=160.25)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    calls=gpu.status()['cuda']
    actual=model.advance(base,seconds=160.25)
    assert gpu.status()['cuda']==calls+1,compute.status()
    same(expected,actual,4e-7)
    for key in before:np.testing.assert_array_equal(before[key],base.fields[key])
    split=model.advance(model.advance(base,seconds=17.125),seconds=143.125)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    cpu_split=model.advance(model.advance(base,seconds=17.125),seconds=143.125)
    same(cpu_split,split,4e-7)
    # The same finite inventories underpin both final bodies, not an RGB overlay.
    if kind=='integral':
        expected=instant.finish(expected);actual=instant.finish(actual)
    else:
        expected=peel.peel(expected);actual=peel.peel(actual)
    from film_foundry.app.material_families import observe
    from film_foundry.core.material_family import ViewingConditions
    for a,b in zip(model.products(expected).bodies,model.products(actual).bodies):
        np.testing.assert_allclose(observe(a,ViewingConditions()),observe(b,ViewingConditions()),atol=1e-6,rtol=1e-6)


def test_failed_device_inventory_restarts_from_immutable_input(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    base=contact();expected=peel.advance(base,seconds=100)
    bad={k:v.copy() for k,v in expected.fields.items() if k in gpu.CHANGED}
    bad['developer_used'][:]=8
    monkeypatch.setattr(gpu,'try_advance',lambda *a:bad)
    rejected=[];monkeypatch.setattr(compute,'reject',rejected.append)
    same(expected,peel.advance(base,seconds=100),0)
    assert len(rejected)==1


def test_opencl_without_fp64_keeps_transfer_on_cpu_without_disabling_rgb(monkeypatch):
    from film_foundry.core import opencl_compute
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    base=contact();expected=peel.advance(base,seconds=100)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','opencl')
    monkeypatch.setattr(gpu,'MIN_PIXELS',0)
    monkeypatch.setattr(opencl_compute,'available',lambda:True)
    monkeypatch.setattr(opencl_compute,'_device',{'fp64':False})
    monkeypatch.setattr(gpu,'_execute',lambda *a:pytest.fail('unsupported FP64 dispatched'))
    monkeypatch.setattr(opencl_compute,'reject',lambda *a:pytest.fail('RGB backend unnecessarily disabled'))
    same(expected,peel.advance(base,seconds=100),0)
