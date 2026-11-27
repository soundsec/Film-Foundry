from copy import deepcopy

import numpy as np
import pytest

from film_foundry.app.workflow import config_from_share_code, encode_recipe_share
from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
from film_foundry.core.engine import expose_linear_to_latent, develop_negative_from_latent
from film_foundry.core.latent_io import load_exposed_latent, save_exposed_latent
from film_foundry.model.config import DarkroomConfig


@pytest.mark.parametrize("spectral", [False, True])
def test_rear_exposure_frozen_at_capture(tmp_path, spectral):
    front = DarkroomConfig(seed_strategy='fixed', random_seed=7)
    if spectral:
        front.film.spectral_exposure_profile = make_smits_exposure_profile()
    rear = deepcopy(front)
    rear.exposure.film_side = 'base'
    image = np.full((24, 24, 3), .18, np.float32)
    normal = expose_linear_to_latent(image, front)
    latent = expose_linear_to_latent(image, rear)
    ratio = latent.developability.mean(axis=(0, 1)) / normal.developability.mean(axis=(0, 1))
    assert 1 > ratio[0] > ratio[1] > ratio[2] > 0
    assert latent.material_snapshot == normal.material_snapshot
    assert config_from_share_code(encode_recipe_share(rear)).exposure.film_side == 'base'
    archive = tmp_path / 'frame.darkroom_latent.npz'
    save_exposed_latent(archive, latent, identity={})
    restored, meta = load_exposed_latent(archive)
    assert meta['film_side'] == restored.film_side == 'base'
    a, b = (develop_negative_from_latent(restored.copy(), c, np.random.default_rng(11)) for c in (front, rear))
    assert a.metadata['film_process_model']['film_side'] == 'base'
    np.testing.assert_array_equal(a.optical_density_rgb, b.optical_density_rgb)


def test_reverse_geometry_and_factory_wedge(tmp_path):
    from film_foundry.core.rear_exposure import expose_from_base
    from film_foundry.contracts.film_exposure import rear_optics
    assert (rear_optics()['auto_refocus'],rear_optics()['mirror']) == (False,'horizontal')
    cfg = DarkroomConfig()
    cfg.exposure.film_side = 'base'
    opt = cfg.exposure.rear_optics = dict(profile='harman_red_125', mirror='none', thickness_um=300,
                                           index=1.5, f_number=1., focus_shift_um=200.)
    scene = np.zeros((64,512,3), np.float32)
    scene[4:30,40:80] = .18
    def expose():
        return expose_from_base(scene.copy(),cfg,monochrome=False,frame_config=cfg.chemistry)
    focused = expose()
    opt['mirror'] = 'vertical'
    np.testing.assert_array_equal(expose(),focused[::-1])
    opt['mirror'] = 'horizontal'
    np.testing.assert_array_equal(expose(),focused[:,::-1])
    opt['focus_shift_um'] = 0.
    blurred = expose()
    assert 0 < blurred[12,432,0] < focused[12,79,0]
    opt['auto_refocus'] = True
    np.testing.assert_array_equal(expose(),focused[:,::-1])
    portable = config_from_share_code(encode_recipe_share(cfg))
    assert portable.exposure.rear_optics == opt
    latent = expose_linear_to_latent(scene,cfg)
    archive = tmp_path/'rear.darkroom_latent.npz'
    save_exposed_latent(archive,latent,identity={})
    restored, _ = load_exposed_latent(archive)
    assert restored.rear_optics == opt
    assert develop_negative_from_latent(restored,DarkroomConfig()).metadata['film_process_model']['rear_optics'] == opt


def test_matched_c41_master_on_intermediate_factory_wedge_steps():
    from pathlib import Path
    from film_foundry.core.engine import develop_negative
    cfg = DarkroomConfig.from_json(Path(__file__).resolve().parents[1]/'presets/harman_red_125_reference.json')
    cfg.enable_grain = cfg.enable_halation = cfg.enable_mtf = False
    cfg.exposure.rear_optics.update(mirror='none',auto_refocus=True)
    h = np.array([2.45,2.95,3.55,3.75])
    dose = np.r_[0.,.18*10.**(h-2.643)]
    image = np.broadcast_to(dose[None,:,None],(8,5,3)).astype(np.float32)
    d = develop_negative(image,cfg,np.random.default_rng(37),input_encoding='linear_rgb').optical_density_rgb.mean(axis=0)
    np.testing.assert_allclose(d[0],[.442,.652,.470],atol=.005)
    np.testing.assert_allclose(d[1:]-d[0],[[.228,.063,.009],[1.106,.331,.065],[1.932,1.167,.237],
                                           [1.998,1.431,.317]],atol=.05)


def test_reverse_light_uses_the_material_support_and_excludes_the_mask():
    from film_foundry.core.rear_exposure import transmit_from_base
    stock = DarkroomConfig().film
    clear = (0., 0., 0.)
    tint = (.14, .055, .025)
    image = np.eye(3, dtype=np.float32).reshape(1,3,3)
    def transmit(total, floor):
        stock.film_base_density_rgb = total
        stock.clear_support_density_rgb = floor
        result = image.copy()
        transmit_from_base(result, stock, monochrome=stock.color_process == 'monochrome')
        return result
    for stock.image_polarity, stock.color_process in [('positive','color'), ('negative','monochrome'), ('negative','color')]:
        ref = transmit(clear, clear)
        # The native color negative carries a separate processed integral mask.
        if stock.image_polarity == 'negative' and stock.color_process == 'color':
            np.testing.assert_array_equal(transmit((.25,.65,.90), clear), ref)
            mixed = transmit((.25,.65,.90), tint)
            np.testing.assert_array_equal(transmit(clear, tint), ref)
        else:
            mixed = transmit(tint, clear)
        np.testing.assert_allclose(mixed, ref*np.power(10.,-np.array(tint))[:,None],rtol=1e-6)
