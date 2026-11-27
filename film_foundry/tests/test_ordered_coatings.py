from dataclasses import replace

import numpy as np
import pytest

from film_foundry.contracts.coating_stack import MODEL, example_stack, validate_stack
from film_foundry.core.engine import develop_negative, develop_negative_from_latent, expose_linear_to_latent
from film_foundry.core.latent_io import latent_identity_from_film, load_exposed_latent, save_exposed_latent
from film_foundry.core.film_process.continuation import FilmProcessPaused, FilmProcessRun
from film_foundry.model.config import DarkroomConfig, MONO_LEGACY_EXPOSURE


def test_independent_coating_grain_changes_optics_only_and_survives_tiling():
    from film_foundry.app.workflow import encode_recipe_share, config_from_share_code
    cfg = DarkroomConfig(enable_halation=False, enable_mtf=False, enable_grain=True)
    cfg.film = replace(cfg.film, coating_stack=example_stack())
    image = np.random.default_rng(4).uniform(.07,.7,(52,63,3)).astype(np.float32)
    def render():
        return develop_negative(image,cfg,np.random.default_rng(91),input_encoding="linear_rgb")
    old = render()
    cfg.processing.independent_coating_grain = True
    new = render()
    np.testing.assert_array_equal(old.density_cmy, new.density_cmy)
    assert not np.array_equal(old.optical_density_rgb,new.optical_density_rgb)
    restored = config_from_share_code(encode_recipe_share(cfg))
    assert restored.processing.independent_coating_grain
    assert restored.film.coating_stack == cfg.film.coating_stack
    cfg.processing.material_tile_threshold_megapixels = .0001
    cfg.processing.material_tile_rows = 12
    tiled = render()
    assert tiled.metadata["film_process_model"]["execution"]["tile_count"] > 1
    np.testing.assert_allclose(tiled.optical_density_rgb,new.optical_density_rgb,atol=3e-6,rtol=0)
    cfg.enable_grain = False
    bypass = render()
    cfg.processing.independent_coating_grain = False
    np.testing.assert_array_equal(bypass.optical_density_rgb,render().optical_density_rgb)


def test_double_coating_uses_support_once_and_routes_violet_to_selected_dye():
    from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
    from film_foundry.core.film_process.coatings import optical_path, received_light
    from film_foundry.core.rear_exposure import transmit_from_base
    from film_foundry.core.lens_filters import filter_loss_ev
    cfg = DarkroomConfig(enable_grain=False, enable_halation=False, enable_mtf=False)
    layer = dict(kind="emulsion", record="R", band_nm=[400, 430], amount=.5)
    stack = dict(model=MODEL, layers=[dict(layer, id="front"), dict(kind="support", id="base"), dict(layer, id="rear")])
    cfg.film = replace(cfg.film, coating_stack=stack, spectral_exposure_profile=make_smits_exposure_profile(3), film_base_density_rgb=(.3,)*3,
                       clear_support_density_rgb=(.3,)*3)
    for side in ("emulsion", "base"):
        cfg.exposure.film_side = side
        gains = list(optical_path(cfg.film, side))
        np.testing.assert_allclose(gains[0][1], 1.)
        np.testing.assert_allclose(gains[1][1], 10**-.3)
        sample = np.ones((1, 1, 3), np.float32)
        transmit_from_base(sample, cfg.film, monochrome=False)
        np.testing.assert_allclose(sample, 1.)
        assert np.isfinite(filter_loss_ev(cfg.film, "yellow", side))
        state = expose_linear_to_latent(np.full((8, 10, 3), .25, np.float32), cfg)
        front, rear = state.developability.mean(axis=(0, 1))
        assert (front > rear) == (side == "emulsion")
    doses = received_light(np.eye(3, dtype=np.float32), cfg.film, layer, 3, 0)
    assert doses[2] > 10 * doses[0]
    assert np.isfinite(develop_negative_from_latent(state, cfg).density_cmy).all()


def test_filter_only_attenuates_coatings_after_it_in_each_direction():
    # Two identical receptors separated by one optical density of neutral filter.
    c = DarkroomConfig(enable_mtf=False, enable_grain=False, enable_halation=False)
    layer = dict(kind="emulsion", record="G", sensitivity_rgb=[0,1,0], amount=.5)
    a, b = dict(layer, id="a"), dict(layer, id="b")
    f = dict(id="filter", kind="filter", density_rgb=[1,1,1])
    c.film = replace(c.film, coating_stack=dict(model=MODEL, layers=[a, f, b]))
    x = np.full((9, 13, 3), .25, np.float32)
    for side, brighter in (("emulsion", 0), ("base", 1)):
        c.exposure.film_side = side
        s = expose_linear_to_latent(x, c)
        assert s.halide.shape[-1] == 2
        np.testing.assert_array_equal(s.halide, .5)
        assert np.all(s.developability[...,brighter] > s.developability[...,1-brighter])


def test_custom_filter_multiplies_only_downstream_spectral_response():
    from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
    from film_foundry.core.film_process.coatings import optical_path, received_light
    from film_foundry.contracts.lens_filters import lens_filter, validate_filter
    from film_foundry.core.lens_filters import filter_gain
    from film_foundry.app.workflow import encode_recipe_share, config_from_share_code
    response = [[380,0], [413,0], [414,1], [415,0], [780,0]]
    em = dict(kind="emulsion", record="B", spectrum=response)
    filt = dict(id="filter", kind="filter", spectrum=[[380,.2],[780,.2]])
    c = DarkroomConfig()
    c.film = replace(c.film, coating_stack=dict(model=MODEL, layers=[dict(em,id="a"),filt,dict(em,id="b")]))
    white = np.ones((1,1,3), np.float32)
    def signals(side):
        return {r["id"]:received_light(white * gain,c.film,r,3,2).item() for r,gain in optical_path(c.film,side)}
    off = signals("emulsion")
    assert off["a"] == off["b"]
    c.film = replace(c.film, spectral_exposure_profile=make_smits_exposure_profile())
    assert signals("emulsion") == pytest.approx({"a":1., "b":.2})
    assert signals("base") == pytest.approx({"a":.2, "b":1.})
    from film_foundry.core.film_process.coatings import receptor_coefficients
    rising = ((380,0),(410,0),(415,1),(780,1))
    falling = ((380,1),(410,1),(415,0),(780,0))
    overlap = receptor_coefficients(c.film.spectral_exposure_profile, 0, (410,415), (), (rising,falling))
    assert overlap[0,0] == pytest.approx(1/6)
    c.exposure.lens_filter = lens_filter("custom", True, [.5,.5,.5])
    np.testing.assert_allclose(filter_gain(c.film,c.exposure.lens_filter),1.,atol=1e-6)
    restored = config_from_share_code(encode_recipe_share(c))
    assert restored.film.coating_stack == c.film.coating_stack
    assert restored.exposure.lens_filter == c.exposure.lens_filter
    with pytest.raises(ValueError):
        validate_filter({**c.exposure.lens_filter, "transmission":[1.1,0,0]})


def test_layers_survive_freeze_and_fast_mode_retains_recipe(tmp_path):
    c = DarkroomConfig(enable_grain=False, enable_halation=False, enable_mtf=False)
    x = np.random.default_rng(17).uniform(.02,.8,(12,18,3)).astype(np.float32)
    def direct():
        return develop_negative(x,c,np.random.default_rng(43),input_encoding="linear_rgb").density_cmy
    baseline = direct()
    c.film = replace(c.film, coating_stack=example_stack())
    ordered = direct()
    state = expose_linear_to_latent(x,c,np.random.default_rng(43))
    assert state.developability.shape == (12,18,6)
    assert np.any(state.developability[...,0] != state.developability[...,1])
    path = save_exposed_latent(tmp_path/"frame.darkroom_latent.npz", state, identity=latent_identity_from_film(c.film))
    frozen, _ = load_exposed_latent(path)
    np.testing.assert_array_equal(frozen.developability, state.developability)
    c.processing.layered_response = False
    np.testing.assert_array_equal(direct(), baseline)
    developed = develop_negative_from_latent(frozen,c,np.random.default_rng(43))
    np.testing.assert_array_equal(developed.density_cmy, ordered)
    assert not np.array_equal(ordered, baseline)

    # Reprocess the same exposed layers with a different grain policy.
    c.enable_grain = True
    shared = develop_negative_from_latent(state.copy(), c, np.random.default_rng(43))
    c.processing.independent_coating_grain = True
    separate = develop_negative_from_latent(state.copy(), c, np.random.default_rng(43))
    np.testing.assert_array_equal(shared.density_cmy, separate.density_cmy)
    assert not np.array_equal(shared.optical_density_rgb, separate.optical_density_rgb)


def test_same_latent_activation_reacts_at_each_coatings_rate():
    c = DarkroomConfig(enable_grain=False)
    row = dict(kind="emulsion", record="G", amount=.5)
    c.film = replace(c.film, coating_stack=dict(model=MODEL, layers=[
        dict(row,id="first"), dict(row,id="second",development_rate=.5,coupler_fraction=0.)]))
    state = expose_linear_to_latent(np.full((12,13,3),.3,np.float32), c)
    np.testing.assert_array_equal(state.developability[...,0],state.developability[...,1])
    with pytest.raises(FilmProcessPaused) as stopped:
        develop_negative_from_latent(state.copy(), c, process_run=FilmProcessRun(stop_after=1))
    after = stopped.value.boundary.state
    assert np.all(after.metallic_silver[...,0] > after.metallic_silver[...,1])
    assert np.all(after.metallic_silver[...,1] > 0)
    assert np.all(after.dye[...,0] > 0)
    np.testing.assert_array_equal(after.dye[...,1],0.)
    resumed = develop_negative_from_latent(state.copy(), c, process_run=FilmProcessRun(resume=stopped.value.boundary))
    np.testing.assert_array_equal(resumed.density_cmy,develop_negative_from_latent(state,c).density_cmy)


def test_legacy_silver_needs_layer_data_before_layered_exposure():
    c = DarkroomConfig(enable_grain=False, enable_halation=False, enable_mtf=False)
    c.film = replace(c.film, color_process="monochrome", monochrome_exposure_model=MONO_LEGACY_EXPOSURE)
    image = np.zeros((12, 20, 3), np.float32)
    image[:, :10, 0] = .4
    image[:, 10:, 2] = .4
    original = expose_linear_to_latent(image, c)
    assert not c.film.supports_ordered_coatings
    with pytest.raises(ValueError, match="separate coating"):
        c.film.require_separated_coatings()
    row = dict(kind="emulsion", record="G", amount=.5)
    draft = validate_stack(dict(model=MODEL, exposure_basis="stock",
                                layers=[dict(row, id="Ag"), dict(row, id="Ag-2")]))
    c.film = replace(c.film, coating_stack=draft)
    np.testing.assert_array_equal(expose_linear_to_latent(image, c).developability, original.developability)
    draft["layers"][1]["sensitivity_rgb"] = [0., 0., 1.]
    c.film = replace(c.film, coating_stack=draft)
    edited = expose_linear_to_latent(image, c)
    assert edited.developability[6, 5, 0] > edited.developability[6, 15, 0]
    assert edited.developability[6, 5, 1] < edited.developability[6, 15, 1]
