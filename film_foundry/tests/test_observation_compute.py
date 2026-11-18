"""Native optical models and common encoding at the device boundary."""
import os
from dataclasses import replace
import numpy as np
import pytest
from film_foundry.core import observation_compute as gpu,desktop_compute as compute
from film_foundry.core.material_family import ViewingConditions
from film_foundry.tests.test_material_recipes import formed
from film_foundry.app.material_families import observe


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='explicit CUDA qualification')
@pytest.mark.parametrize('kind',['peel','integral','plate','paper','wet','cyanotype','platinum'])
def test_native_cuda_observation_preserves_material_and_view(monkeypatch,kind):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    monkeypatch.setenv('FILM_FOUNDRY_EXPERIMENTAL_PEEL_OBSERVER','1')
    session=formed(kind)
    monkeypatch.setattr(gpu,'MIN_PIXELS',0)
    views=[ViewingConditions(),ViewingConditions(illuminant_rgb=(.7,1.,.4),exposure_ev=-1.,
              light_direction=(-.5,0.,1.),view_direction=(.7,.1,1.),include_border=True)]
    for body in session.group.bodies:
        before={k:v.copy() for k,v in body.fields.items()}
        for view in views:
            monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu');expected=observe(body,view)
            monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda');calls=sum(gpu.status().values())
            actual=observe(body,view)
            assert sum(gpu.status().values())>calls,compute.status()
            np.testing.assert_allclose(actual,expected,rtol=3e-6,atol=1e-6,err_msg=kind)
        for k,v in before.items():np.testing.assert_array_equal(v,body.fields[k])


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='explicit CUDA qualification')
def test_shared_cuda_encoding_preserves_linear_input_and_toe(monkeypatch):
    from film_foundry.core.color import linear_to_srgb
    from film_foundry.core.scanner import encode_scan_output
    from film_foundry.model.config import ScannerConfig
    monkeypatch.setattr(gpu,'MIN_PIXELS',0)
    values=np.random.default_rng(24).uniform(-.1,2.,(37,43,3)).astype(np.float32)
    values[:3]=np.array([0.,.0031308,1.],np.float32)
    original=values.copy()
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu');expected=encode_scan_output(values,ScannerConfig())
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda');before=sum(gpu.status().values());actual=encode_scan_output(values,ScannerConfig())
    assert sum(gpu.status().values())>before,compute.status()
    np.testing.assert_allclose(actual,expected,rtol=0,atol=3e-7)
    np.testing.assert_array_equal(values,original)
    invalid=values.copy();invalid[0,0,0]=np.nan
    encoded=linear_to_srgb(invalid)
    assert np.isnan(encoded[0,0,0]) and compute.status()['failure'] is None


def test_unavailable_optical_device_does_not_consume_material(monkeypatch):
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu');session=formed('paper');body=session.group.bodies[0]
    expected=observe(body)
    monkeypatch.setattr(gpu,'run',lambda *a,**kw:None)
    np.testing.assert_array_equal(observe(body),expected)


@pytest.mark.skipif(os.environ.get('FILM_FOUNDRY_TEST_CUDA')!='1',reason='explicit CUDA qualification')
@pytest.mark.parametrize('kind',['paper','peel','integral'])
def test_color_paper_and_single_channel_transfer_observers(monkeypatch,kind):
    from film_foundry.app.material_lab_session import MaterialLabSession
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    monkeypatch.setenv('FILM_FOUNDRY_EXPERIMENTAL_PEEL_OBSERVER','1')
    monkeypatch.setattr(gpu,'MIN_PIXELS',0)
    session=MaterialLabSession(kind);session.image=np.random.default_rng(28).random((17,19,3),dtype=np.float32)
    if kind=='paper':session.run_standard({'kind':'color'})
    else:
        session.process('expose',material={'transfer':'silver_compound'})
        if kind=='integral':session.process('eject')
        session.process('spread')
        if kind=='integral':session.process('stabilize')
        else:session.process('advance',{'seconds':180});session.process('peel')
    for body in session.group.bodies:
        monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu');expected=observe(body)
        monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda');calls=sum(gpu.status().values());actual=observe(body)
        assert sum(gpu.status().values())>calls,compute.status()
        np.testing.assert_allclose(actual,expected,atol=1e-6,rtol=3e-6)
