import copy
from dataclasses import replace

import numpy as np
import pytest

from film_foundry.core.engine import _develop_reduced_material_tiled, develop_negative
from film_foundry.core.film_process.integration import (
    develop_color_negative_reduced,
    develop_color_reversal_reduced,
)
from film_foundry.model.config import DarkroomConfig, DevelopRecipeConfig, FilmStockConfig


def _exposure_ramp(log_start=-2.5, log_stop=1.0, count=257):
    exposure = np.power(
        10.0,
        np.linspace(log_start, log_stop, count, dtype=np.float32),
    )
    return np.repeat(exposure.reshape(1, count, 1), 3, axis=-1).astype(np.float32)


def test_ordinary_material_overexposure_remains_shoulder_to_dmax():
    film = FilmStockConfig(extreme_exposure_reversal_strength=0.0)
    result = develop_color_negative_reduced(
        _exposure_ramp(),
        film,
        DevelopRecipeConfig(program_key="color_negative"),
    )
    latent = result.latent_fraction[0, :, 0]

    assert np.all(np.diff(latent) >= -1e-7)
    assert float(latent[-1]) >= float(latent[-20])


def test_experimental_tail_joins_shoulder_continuously_then_reduces_activation():
    image = _exposure_ramp(log_start=0.35, log_stop=0.85, count=501)
    baseline_film = FilmStockConfig(extreme_exposure_reversal_strength=0.0)
    tail_film = copy.deepcopy(baseline_film)
    tail_film.extreme_exposure_reversal_strength = 0.75
    tail_film.extreme_exposure_reversal_start_loge = (0.50, 0.50, 0.50)
    tail_film.extreme_exposure_reversal_width = 0.20
    recipe = DevelopRecipeConfig(program_key="color_negative")

    baseline = develop_color_negative_reduced(image, baseline_film, recipe)
    tailed = develop_color_negative_reduced(image, tail_film, recipe)
    log_values = np.linspace(0.35, 0.85, 501, dtype=np.float32)
    before = log_values <= 0.50
    at_start = int(np.argmin(np.abs(log_values - 0.50)))

    assert np.array_equal(
        tailed.latent_fraction[:, before],
        baseline.latent_fraction[:, before],
    )
    assert np.array_equal(
        tailed.latent_fraction[:, at_start],
        baseline.latent_fraction[:, at_start],
    )
    assert float(tailed.latent_fraction[0, -1, 0]) < (
        float(baseline.latent_fraction[0, -1, 0]) * 0.35
    )
    # The first sample above the join must not contain a hard step.
    assert abs(
        float(tailed.latent_fraction[0, at_start + 1, 0])
        - float(baseline.latent_fraction[0, at_start + 1, 0])
    ) < 1e-4


def test_extreme_tail_is_one_material_latent_state_shared_by_negative_and_reversal():
    film = FilmStockConfig(
        extreme_exposure_reversal_strength=0.65,
        extreme_exposure_reversal_start_loge=(0.48, 0.52, 0.56),
        extreme_exposure_reversal_width=0.18,
    )
    image = _exposure_ramp(log_start=0.3, log_stop=0.9, count=91)
    recipe = DevelopRecipeConfig()

    negative = develop_color_negative_reduced(image, film, recipe)
    reversal = develop_color_reversal_reduced(image, film, recipe)

    assert np.array_equal(negative.latent_fraction, reversal.latent_fraction)
    assert np.all(np.isfinite(negative.process_result.final_medium.metallic_silver))
    assert np.all(np.isfinite(reversal.process_result.final_medium.dye))
    assert np.all(negative.process_result.final_medium.metallic_silver >= 0.0)
    assert np.all(reversal.process_result.final_medium.dye >= 0.0)


def test_layer_specific_tail_thresholds_apply_independently_of_baseline_layer_response():
    film = FilmStockConfig(
        extreme_exposure_reversal_strength=0.8,
        extreme_exposure_reversal_start_loge=(0.42, 0.58, 0.74),
        extreme_exposure_reversal_width=0.12,
    )
    baseline_film = replace(film, extreme_exposure_reversal_strength=0.0)
    image = np.full((5, 7, 3), 10**0.72, dtype=np.float32)
    result = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(program_key="color_negative"),
    )
    baseline = develop_color_negative_reduced(
        image,
        baseline_film,
        DevelopRecipeConfig(program_key="color_negative"),
    )
    means = result.latent_fraction.mean(axis=(0, 1))
    baseline_means = baseline.latent_fraction.mean(axis=(0, 1))
    retained_fraction = means / baseline_means

    assert float(retained_fraction[0]) < float(retained_fraction[1]) < float(retained_fraction[2])


def test_extreme_tail_material_pool_tiling_is_exact():
    rng = np.random.default_rng(20260726)
    image = np.power(10.0, rng.uniform(-0.2, 0.9, (19, 31, 3))).astype(np.float32)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_negative"
    config.film.extreme_exposure_reversal_strength = 0.7
    config.film.extreme_exposure_reversal_start_loge = (0.46, 0.52, 0.58)
    config.film.extreme_exposure_reversal_width = 0.16
    full = develop_color_negative_reduced(image, config.film, config.chemistry)
    tiled_density, tiled_optical, _, _ = _develop_reduced_material_tiled(
        image,
        config,
        "color_negative",
        4,
    )

    assert np.array_equal(tiled_density, full.density_cmy)
    assert np.array_equal(tiled_optical, full.optical_density_rgb)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("extreme_exposure_reversal_strength", -0.1, "strength"),
        ("extreme_exposure_reversal_strength", 1.1, "strength"),
        ("extreme_exposure_reversal_width", 0.0, "width"),
        ("extreme_exposure_reversal_start_loge", (0.4, 0.5), "shape"),
    ),
)
def test_invalid_extreme_tail_material_settings_are_rejected(field, value, match):
    config = DarkroomConfig()
    setattr(config.film, field, value)
    with pytest.raises(ValueError, match=match):
        develop_negative(
            np.full((5, 7, 3), 0.3, dtype=np.float32),
            config,
            rng=np.random.default_rng(2),
        )
