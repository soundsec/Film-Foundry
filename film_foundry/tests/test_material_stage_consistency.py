"""Stage regressions: stock topology, deferred baths and read-only estimates."""

import copy

import numpy as np
import pytest

from film_foundry.core.color import srgb_to_linear
from film_foundry.core.engine import (
    expose_image_to_latent, develop_negative_from_latent, develop_negative, scan_medium_output,
)
from film_foundry.core.film_process.integration import _color_material, _material_latent_state
from film_foundry.core.film_process.model import FilmProcessState
from film_foundry.core.film_process.operators import apply_process_program
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.material_latent_grid import MaterialLatentSource
from film_foundry.model.config import DarkroomConfig


PROGRAMS = ("bw_negative", "bw_reversal", "color_negative", "color_reversal")


def config(mono=False, positive=False):
    cfg = DarkroomConfig()
    cfg.enable_mtf = cfg.enable_halation = cfg.enable_grain = False
    cfg.film.color_process = "monochrome" if mono else "subtractive"
    cfg.film.image_polarity = "positive" if positive else "negative"
    cfg.film.dir_material_response_matrix = ((0., 0., 0.),) * 3
    cfg.film.layer_sensitivity_matrix = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    return cfg


def scene(height=9, width=15):
    image = np.full((height, width, 3), .04, np.float32)
    image[:, :width//2] = (.85, .16, .08)
    image[height//2:] *= .55
    return image


@pytest.mark.parametrize("mono", [False, True])
def test_exposure_topology_and_coupler_inventory_do_not_depend_on_preselected_baths(mono):
    states = []
    for program in PROGRAMS:
        cfg = config(mono)
        cfg.mode = cfg.chemistry.program_key = program
        states.append(expose_image_to_latent(scene(), cfg, rng=np.random.default_rng(23)))
    for state in states:
        assert state.halide.shape[-1] == (1 if mono else 3)
        for name in MaterialLatentSource.POOLS:
            a, b = getattr(states[0], name), getattr(state, name)
            if a is None:
                assert b is None
            else:
                np.testing.assert_array_equal(a, b)
        assert (state.coupler is None) == mono
        if not mono:
            assert np.all(state.coupler == 1.)


@pytest.mark.parametrize("mono,positive", [(False, False), (False, True), (True, False), (True, True)])
def test_frozen_material_can_choose_any_standard_bath_after_exposure(mono, positive):
    cfg = config(mono, positive)
    cfg.mode = cfg.chemistry.program_key = "bw_negative" if mono else "color_negative"
    image = scene()
    latent = expose_image_to_latent(image, cfg, rng=np.random.default_rng(23))
    for program in PROGRAMS:
        choice = copy.deepcopy(cfg)
        choice.mode = choice.chemistry.program_key = program
        full = develop_negative(image, choice, rng=np.random.default_rng(23))
        deferred = develop_negative_from_latent(latent.copy(), choice, rng=np.random.default_rng(23))
        np.testing.assert_allclose(deferred.optical_density_rgb, full.optical_density_rgb, rtol=0, atol=2e-6)
        assert np.isfinite(deferred.optical_density_rgb).all()
        if mono and program.startswith("color_"):
            # Complete colour chemistry clears the silver; it cannot make dye.
            np.testing.assert_allclose(deferred.optical_density_rgb,
                np.broadcast_to(deferred.optical_density_rgb[:1, :1], deferred.optical_density_rgb.shape),
                rtol=0, atol=1e-6)


def test_legacy_three_layer_monochrome_pools_are_not_duplicated_or_reexposed():
    cfg = config(True)
    legacy = _material_latent_state(scene(), cfg.film, _color_material(cfg.film, 3))
    # Keep the old independent layer values as authored, not their mean.
    legacy.developability[..., 1] *= .4
    before = legacy.developability.copy()
    for program in PROGRAMS:
        cfg.mode = cfg.chemistry.program_key = program
        supplied = legacy.copy()
        result = develop_negative_from_latent(supplied, cfg, rng=np.random.default_rng(23))
        assert result.optical_density_rgb.shape == scene().shape
        assert supplied.halide.shape[-1] == 3
    np.testing.assert_array_equal(legacy.developability, before)


@pytest.mark.parametrize("effect", ["uneven", "adjacency", "dir", "combined"])
@pytest.mark.parametrize("edge", [11, 1800])
def test_deferred_local_bath_effects_match_full_formation_on_reduced_global_grid(effect, edge, monkeypatch):
    cfg = config()
    image = scene(31, 47)
    if effect in {"dir", "combined"}:
        # DIR belongs to the loaded material. Only the bath's effectiveness
        # changes after exposure, not the material response matrix.
        cfg.film.dir_material_response_matrix = ((0., .4, 0.), (0., 0., 0.), (0., 0., 0.))
        cfg.chemistry.dir_process_effectiveness = 0.
    latent = expose_image_to_latent(image, cfg, rng=np.random.default_rng(23))
    baseline = develop_negative_from_latent(latent.copy(), cfg, rng=np.random.default_rng(23))
    if effect in {"uneven", "combined"}:
        cfg.chemistry.uneven_development = .8
    if effect in {"adjacency", "combined"}:
        cfg.chemistry.development_adjacency_strength = .8
        cfg.chemistry.development_adjacency_radius = .04
    if effect in {"dir", "combined"}:
        cfg.chemistry.dir_process_effectiveness = 1.
    monkeypatch.setattr("film_foundry.core.engine._adjacency_work_long_edge", lambda _: edge)
    monkeypatch.setattr("film_foundry.core.engine._dir_work_long_edge", lambda _: edge)
    full = develop_negative(image, cfg, rng=np.random.default_rng(23))
    deferred = develop_negative_from_latent(latent.copy(), cfg, rng=np.random.default_rng(23))
    assert np.max(np.abs(deferred.optical_density_rgb-baseline.optical_density_rgb)) > 1e-5
    np.testing.assert_allclose(deferred.optical_density_rgb, full.optical_density_rgb, rtol=0, atol=2e-6)


@pytest.mark.parametrize("edge", [1, 11])
def test_material_grid_resamples_after_nonlinear_exposure_and_borrows_source_read_only(edge, monkeypatch):
    cfg = config()
    image = scene(31, 47)
    latent = expose_image_to_latent(image, cfg)
    before = {name: None if getattr(latent, name) is None else getattr(latent, name).copy()
              for name in MaterialLatentSource.POOLS}
    # Force a small row budget, including a one-cell grid covering the whole frame.
    monkeypatch.setattr(MaterialLatentSource, "ROW_PIXELS", image.shape[1]*2)
    direct, grid = MaterialLatentSource(cfg.film, image_linear=srgb_to_linear(image)).working_state(edge)
    frozen, _ = MaterialLatentSource(cfg.film, exposed_latent=latent).working_state(edge)
    for name in MaterialLatentSource.POOLS:
        value = getattr(latent, name)
        if value is not None:
            # Independent double-precision area integral; OpenCV's long float
            # accumulation is not an exact oracle for a one-cell reduction.
            def area_weights(full, work):
                cells = np.arange(full, dtype=np.float64)
                boundaries = np.linspace(0, full, work+1)
                return np.maximum(0., np.minimum(cells[None, :]+1, boundaries[1:, None])
                                  - np.maximum(cells[None, :], boundaries[:-1, None])) / (full/work)
            wy = area_weights(image.shape[0], grid.work_shape[0])
            wx = area_weights(image.shape[1], grid.work_shape[1])
            reference = np.einsum("ah,hwl,bw->abl", wy, value.astype(np.float64), wx)
            np.testing.assert_allclose(getattr(direct, name), reference, rtol=0, atol=3e-7)
            np.testing.assert_array_equal(getattr(direct, name), getattr(frozen, name))
            np.testing.assert_array_equal(value, before[name])


@pytest.mark.parametrize("mono", [False, True])
def test_layer_exposure_addition_keeps_stock_topology_with_local_baths_and_row_tiles(mono, monkeypatch):
    cfg = config(mono)
    cfg.chemistry.program_key = "color_negative_bleach_bypass"
    cfg.chemistry.silver_bleach_completion = .4
    cfg.chemistry.development_adjacency_strength = .4
    cfg.film.light_piping_strength = .2
    cfg.film.light_piping_depth = .1
    image = scene(31, 47)
    monkeypatch.setattr("film_foundry.core.engine._adjacency_work_long_edge", lambda _: 11)
    monkeypatch.setattr("film_foundry.core.engine._material_pool_tile_rows", lambda *_: 3)
    latent = expose_image_to_latent(image, cfg, rng=np.random.default_rng(23))
    full = develop_negative(image, cfg, rng=np.random.default_rng(23))
    deferred = develop_negative_from_latent(latent.copy(), cfg, rng=np.random.default_rng(23))
    np.testing.assert_allclose(deferred.optical_density_rgb, full.optical_density_rgb, rtol=0, atol=2e-6)
    assert latent.halide.shape[-1] == (1 if mono else 3)


def test_missing_color_capacity_or_unpaired_pools_are_rejected_before_consuming_input():
    cfg = config()
    material = _color_material(cfg.film)
    program = program_from_develop_recipe(cfg.chemistry)
    for half_pair in (False, True):
        state = FilmProcessState(np.ones((1, 3, 3), np.float32),
                                 np.full((1, 3, 3), .5, np.float32),
                                 np.zeros((1, 3, 3), np.float32),
                                 dye=np.zeros((1, 3, 3), np.float32) if half_pair else None)
        before = state.halide.copy()
        with pytest.raises(ValueError, match="paired|coupler-bearing"):
            apply_process_program(material, state, program, consume_latent_state=True)
        np.testing.assert_array_equal(before, state.halide)
        assert not np.any(state.metallic_silver)


@pytest.mark.parametrize("program", ["color_negative", "color_reversal"])
def test_single_layer_color_processed_medium_keeps_existing_read_only_scan_contract(program):
    cfg = config(True)
    latent = expose_image_to_latent(scene(), cfg)
    cfg.mode = cfg.chemistry.program_key = program
    medium = develop_negative_from_latent(latent, cfg)
    before = medium.optical_density_rgb.copy()
    output = scan_medium_output(medium, cfg)
    assert np.isfinite(output.output_srgb).all()
    assert output.output_srgb.shape == before.shape
    np.testing.assert_array_equal(medium.optical_density_rgb, before)
