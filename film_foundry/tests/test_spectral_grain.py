import copy
import json
from pathlib import Path

import numpy as np
import pytest

from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile
from film_foundry.core.engine import (
    develop_negative, develop_negative_from_latent, expose_image_to_latent,
    process_array, scan_medium_output,
)
from film_foundry.core.spectral_optics import capture_spectral_medium

from film_foundry.model.config import DarkroomConfig


def configuration(process="color_negative", independent=False):
    sample = json.loads((Path(__file__).resolve().parents[2] /
        "android/kernel/src/test/resources/spectral_optics_sample.json").read_text(encoding="utf-8"))
    cfg = DarkroomConfig(enable_mtf=False, enable_halation=False, enable_grain=True)
    cfg.film.spectral_material_profile = SpectralMaterialProfile.from_value(sample["material"])
    cfg.scanner.spectral_observation_profile = SpectralObservationProfile.from_value(sample["observer"])
    cfg.mode = process
    cfg.chemistry.program_key = process
    if process.startswith("bw_"):
        cfg.film.color_process = "bw"
        cfg.enable_subtractive = False
    if process.endswith("reversal"):
        cfg.film.image_polarity = "positive"
    if independent:
        cfg.film.silver_grain_strength = .08
    cfg.processing.material_tile_rows = 7
    cfg.processing.material_tile_threshold_megapixels = .0001
    return cfg


def scene():
    return np.linspace(.03, .97, 31*47*3, dtype=np.float32).reshape(31, 47, 3)


def develop(cfg, *, source=None, rng=None):
    return develop_negative(scene() if source is None else source, cfg,
                            rng=np.random.default_rng(71) if rng is None else rng)


@pytest.mark.parametrize("process", ["color_negative", "color_reversal", "bw_negative", "bw_reversal"])
@pytest.mark.parametrize("independent", [False, True])
def test_grain_conserves_actual_components_and_keeps_rgb_compatibility(process, independent):
    cfg = configuration(process, independent)
    # Retained silver gives the colour case both kinds of imaging material.
    if process.startswith("color_"):
        cfg.chemistry.silver_retention = .4
    formed_cfg = copy.deepcopy(cfg)
    formed_cfg.enable_grain = False
    formed = develop(formed_cfg)
    spectral_rng = np.random.default_rng(71)
    grained = develop(cfg, rng=spectral_rng)
    before = formed.spectral_medium.component_amounts
    after = grained.spectral_medium.component_amounts
    np.testing.assert_allclose(after[..., :4].sum((0, 1), dtype=np.float64),
                               before[..., :4].sum((0, 1), dtype=np.float64), rtol=2e-7, atol=1e-7)
    np.testing.assert_array_equal(after[..., 4:], before[..., 4:])
    assert grained.spectral_medium.base_component_amounts == formed.spectral_medium.base_component_amounts
    assert np.all(after >= 0) and np.isfinite(after).all()
    assert np.all(after[before == 0] == 0)
    assert np.max(np.abs(after[..., :4] - before[..., :4])) > 1e-5
    assert np.max(np.abs(capture_spectral_medium(grained.spectral_medium, cfg.scanner.spectral_observation_profile) -
                         capture_spectral_medium(formed.spectral_medium, cfg.scanner.spectral_observation_profile))) > 1e-6

    # Adding the optional spectral capability must not alter any old master
    # or advance the existing RNG differently.
    legacy_cfg = copy.deepcopy(cfg)
    legacy_cfg.film.spectral_material_profile = None
    legacy_cfg.scanner.spectral_observation_profile = None
    legacy_rng = np.random.default_rng(71)
    legacy = develop(legacy_cfg, rng=legacy_rng)
    for field in ("density_cmy", "density_grain", "optical_density_rgb"):
        np.testing.assert_array_equal(getattr(grained, field), getattr(legacy, field))
    np.testing.assert_array_equal(spectral_rng.integers(0, 2**32, 8), legacy_rng.integers(0, 2**32, 8))
    assert not after.flags.writeable
    snapshot = after.copy()
    scan_medium_output(grained, cfg)
    np.testing.assert_array_equal(after, snapshot)


@pytest.mark.parametrize("fast", [False, True])
def test_whole_tiled_frozen_latent_and_output_only_use_the_same_grain(fast):
    cfg = configuration(independent=True)
    cfg.fast_mode = fast
    cfg.chemistry.silver_retention = .3
    tiled = develop(cfg)
    whole_cfg = copy.deepcopy(cfg)
    whole_cfg.processing.material_tile_rows = 0
    whole = develop(whole_cfg)
    latent = expose_image_to_latent(scene(), cfg, rng=np.random.default_rng(71))
    latent_before = latent.developability.copy()
    from_latent = develop_negative_from_latent(latent.copy(), cfg, rng=np.random.default_rng(71))
    np.testing.assert_array_equal(latent.developability, latent_before)
    for medium in (whole, from_latent):
        np.testing.assert_allclose(medium.spectral_medium.component_amounts,
                                   tiled.spectral_medium.component_amounts, rtol=1e-6, atol=1e-7)
    output = process_array(scene(), cfg, rng=np.random.default_rng(71))
    np.testing.assert_array_equal(output, scan_medium_output(tiled, cfg).output_srgb)


def test_zero_pools_noop_and_independent_silver_keeps_the_coordinate_identity():
    from film_foundry.core.spectral_grain import (
        apply_spectral_emulsion_grain, apply_spectral_silver_grain, conserve_spectral_grain_totals,
    )
    from film_foundry.core.silver_grain import SilverGrainPlan
    amounts = np.zeros((137, 19, 9), dtype=np.float32)
    amounts[..., 6:] = 1
    before = amounts.copy()
    apply_spectral_emulsion_grain(amounts, np.full((137, 19, 3), -20, dtype=np.float32), (2, 2, 2), independent_silver=False)
    plan = SilverGrainPlan(full_shape=(137, 19), seed=13, strength=.08, radius=.006, clump_mix=.2)
    assert not apply_spectral_silver_grain(amounts, plan)
    conserve_spectral_grain_totals(amounts, (0, 0, 0, 0))
    np.testing.assert_array_equal(amounts, before)
    amounts[..., 3] = .4
    first = amounts.copy()
    second = amounts.copy()
    assert apply_spectral_silver_grain(first, plan)
    assert apply_spectral_silver_grain(second, plan)
    np.testing.assert_array_equal(first, second)
    conserve_spectral_grain_totals(first, amounts[..., :4].sum((0, 1), dtype=np.float64))
    assert np.std(first[..., 3]) > .001
    np.testing.assert_allclose(first[..., 3].sum(dtype=np.float64), amounts[..., 3].sum(dtype=np.float64), rtol=2e-7)


def test_grained_headless_archive_retains_frozen_material_and_observation_is_readonly(tmp_path):
    from PIL import Image
    from film_foundry.app.api import WorkflowRequest, run_request
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    cfg = configuration(independent=True)
    source = tmp_path / "source.png"
    Image.fromarray(np.rint(scene()*255).astype(np.uint8)).save(source)
    latent = tmp_path / "frame.darkroom_latent.npz"
    formed = tmp_path / "frame.darkroom_negative.npz"
    run_request(WorkflowRequest("expose", source, latent, cfg))
    cfg.film.spectral_material_profile = None
    run_request(WorkflowRequest("develop_latent", latent, formed, cfg))
    medium = load_developed_medium_for_scan(formed)
    before = medium.spectral_medium.component_amounts.copy()
    record = medium.metadata["film_process_model"]["spectral_grain_formation"]
    assert record["model"] == "conservative_normalized_pool_grain_v1"
    assert record["enabled"] and record["normalization_scope"] == "full_frame_per_component"
    run_request(WorkflowRequest("scan", formed, tmp_path / "scan.png", cfg))
    reloaded = load_developed_medium_for_scan(formed)
    np.testing.assert_array_equal(before, reloaded.spectral_medium.component_amounts)
