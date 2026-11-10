import numpy as np
import pytest


def test_gpu_dither_keeps_coordinates_wrap_and_final_quantization(monkeypatch):
    from film_foundry.core.antibanding import _coordinate_noise_region
    from film_foundry.core.io_utils import _quantize_rgb_rows
    from film_foundry.core import desktop_compute as gpu
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cpu')
    reference=[_coordinate_noise_region(a,b,1025,271) for a,b in ((0,128),(128,271))]
    yy,xx=np.mgrid[:271,:1025]
    image=np.stack([xx/1024,yy/270,(xx+yy)/1294],axis=-1).astype(np.float32)
    expected=_quantize_rgb_rows(image,bit_depth=8,tile_rows=128,anti_banding_strength=.7)
    monkeypatch.setenv('FILM_FOUNDRY_COMPUTE','cuda')
    for source,(a,b) in zip(reference,((0,128),(128,271))):
        np.testing.assert_array_equal(source,_coordinate_noise_region(a,b,1025,271))
    if gpu.status()['failure']:pytest.skip(gpu.status()['failure'])
    assert gpu.status()['noise_calls']>=2
    actual=_quantize_rgb_rows(image,bit_depth=8,tile_rows=128,anti_banding_strength=.7)
    np.testing.assert_array_equal(expected,actual)
    monkeypatch.setattr(gpu,'_failure','test unavailable')
    np.testing.assert_array_equal(reference[0],_coordinate_noise_region(0,128,1025,271))
