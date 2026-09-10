import copy
from pathlib import Path

import numpy as np
import pytest

from film_foundry.core.development_adjacency import (
    build_development_adjacency_field,
)
from film_foundry.core.engine import develop_negative
from film_foundry.core.film_process.integration import (
    develop_color_negative_reduced,
)
from film_foundry.core.spatial_fields import StepDevelopmentRateField
from film_foundry.model.config import DarkroomConfig, DevelopRecipeConfig, FilmStockConfig


def _edge_image(height=48, width=72):
    image = np.full((height, width, 3), 0.06, dtype=np.float32)
    image[:, : width // 2] = 0.85
    return image


def test_disabled_adjacency_is_strict_no_field_identity():
    field, audit = build_development_adjacency_field(
        _edge_image(),
        FilmStockConfig(),
        DevelopRecipeConfig(development_adjacency_strength=0.0),
        "color_negative",
    )

    assert field is None
    assert audit["enabled"] is False
    assert audit["applied"] is False
    assert audit["provisional_pool_consumptions"] == 0


def test_high_acutance_bw_preset_applies_existing_formation_adjacency():
    config = DarkroomConfig.from_json(
        Path(__file__).resolve().parents[1]
        / "presets"
        / "develop"
        / "high_acutance_bw.json"
    )

    field, audit = build_development_adjacency_field(
        _edge_image(),
        config.film,
        config.chemistry,
        "bw_negative",
        work_long_edge=48,
    )

    assert field is not None
    assert audit["enabled"] is True
    assert audit["applied"] is True
    assert audit["step_scope"] == "first_development_only"
    assert audit["strength"] == config.chemistry.development_adjacency_strength


def test_uniform_provisional_reaction_skips_adjacency_rate_field():
    image = np.full((32, 48, 3), 0.3, dtype=np.float32)
    field, audit = build_development_adjacency_field(
        image,
        FilmStockConfig(),
        DevelopRecipeConfig(development_adjacency_strength=1.0),
        "color_negative",
        work_long_edge=48,
    )

    assert field is None
    assert audit["skip_reason"] == "uniform_provisional_reaction"


def test_adjacency_enhances_expected_reaction_side_and_suppresses_neighbor():
    image = _edge_image()
    field, audit = build_development_adjacency_field(
        image,
        FilmStockConfig(),
        DevelopRecipeConfig(
            development_adjacency_strength=1.0,
            development_adjacency_radius=0.04,
            agitation=1.0,
        ),
        "color_negative",
        work_long_edge=image.shape[1],
    )

    assert field is not None
    rate = field.slice_rows(0, image.shape[0])
    boundary = image.shape[1] // 2
    assert float(rate[:, boundary - 1].mean()) > 1.0
    assert float(rate[:, boundary].mean()) < 1.0
    assert abs(float(rate.mean()) - 1.0) < 2e-6
    assert audit["step_scope"] == "first_development_only"
    assert audit["formal_pool_consumptions"] == 1
    assert audit["provisional_pool_consumptions"] == 0


def test_lazy_global_rate_rows_are_exact_for_any_partition():
    image = _edge_image(53, 97)
    field, _ = build_development_adjacency_field(
        image,
        FilmStockConfig(),
        DevelopRecipeConfig(
            development_adjacency_strength=0.8,
            development_adjacency_radius=0.025,
        ),
        "color_negative",
        work_long_edge=37,
    )
    assert field is not None

    full = field.slice_rows(0, image.shape[0])
    tiled = np.concatenate(
        [field.slice_rows(start, min(start + 7, image.shape[0])) for start in range(0, image.shape[0], 7)],
        axis=0,
    )
    assert np.array_equal(tiled, full)


def test_adjacency_global_grid_is_orientation_consistent():
    image = _edge_image(47, 83)
    recipe = DevelopRecipeConfig(
        development_adjacency_strength=0.72,
        development_adjacency_radius=0.018,
    )
    landscape, _ = build_development_adjacency_field(
        image,
        FilmStockConfig(),
        recipe,
        "color_negative",
        work_long_edge=61,
    )
    portrait, _ = build_development_adjacency_field(
        np.transpose(image, (1, 0, 2)),
        FilmStockConfig(),
        recipe,
        "color_negative",
        work_long_edge=61,
    )
    assert landscape is not None and portrait is not None

    landscape_rate = landscape.slice_rows(0, image.shape[0])
    portrait_rate = portrait.slice_rows(0, image.shape[1]).T
    assert np.allclose(landscape_rate, portrait_rate, atol=3e-6, rtol=0.0)


def test_step_rate_composition_limits_adjacency_to_first_development():
    image = _edge_image(31, 45)
    field, audit = build_development_adjacency_field(
        image,
        FilmStockConfig(),
        DevelopRecipeConfig(development_adjacency_strength=0.7),
        "color_reversal",
        work_long_edge=45,
    )
    assert field is not None
    common = np.full(image.shape[:2], 0.9, dtype=np.float32)
    combined = StepDevelopmentRateField(
        image.shape[:2],
        str(audit["first_development_step"]),
        common_rate=common,
        first_step_rate=field,
    )

    first = combined.rate_for_step(str(audit["first_development_step"]), "develop_silver")
    second = combined.rate_for_step("color_development", "develop_color")
    assert np.array_equal(first, common * field.slice_rows(0, image.shape[0]))
    assert np.array_equal(second, common)


def test_adjacency_changes_conversion_but_not_frozen_latent_state():
    image = _edge_image(37, 61)
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig(
        development_adjacency_strength=0.85,
        development_adjacency_radius=0.03,
    )
    field, audit = build_development_adjacency_field(
        image,
        film,
        recipe,
        "color_negative",
        work_long_edge=43,
    )
    assert field is not None
    rates = StepDevelopmentRateField(
        image.shape[:2],
        str(audit["first_development_step"]),
        first_step_rate=field,
    )

    clean = develop_color_negative_reduced(image, film, recipe)
    adjacent = develop_color_negative_reduced(
        image,
        film,
        recipe,
        local_development_rate=rates,
    )

    assert np.array_equal(adjacent.latent_fraction, clean.latent_fraction)
    assert not np.array_equal(
        adjacent.process_result.final_medium.dye,
        clean.process_result.final_medium.dye,
    )
    adjacent.process_result.state.validate()


def test_engine_adjacency_is_exact_across_material_pool_tiles():
    image = _edge_image(41, 67)
    base = DarkroomConfig()
    base.enable_mtf = False
    base.enable_halation = False
    base.enable_grain = False
    base.chemistry.program_key = "color_negative"
    base.chemistry.development_adjacency_strength = 0.75
    base.chemistry.development_adjacency_radius = 0.025
    base.processing.adjacency_work_long_edge = 39
    untiled = copy.deepcopy(base)
    untiled.processing.material_tile_rows = 0
    tiled = copy.deepcopy(base)
    tiled.processing.material_tile_rows = 7
    tiled.processing.material_tile_threshold_megapixels = 1e-9

    full = develop_negative(image, untiled, rng=np.random.default_rng(31))
    rows = develop_negative(image, tiled, rng=np.random.default_rng(31))

    assert np.array_equal(rows.density_cmy, full.density_cmy)
    assert np.array_equal(rows.optical_density_rgb, full.optical_density_rgb)
    assert rows.metadata["development_adjacency"]["applied"] is True
    assert rows.metadata["film_process_model"]["development_adjacency"][
        "formal_pool_consumptions"
    ] == 1


def test_lower_agitation_increases_only_the_declared_adjacency_amplitude():
    image = _edge_image()
    low = DevelopRecipeConfig(
        development_adjacency_strength=0.6,
        development_adjacency_radius=0.03,
        agitation=0.25,
    )
    high = copy.deepcopy(low)
    high.agitation = 2.0

    _, low_audit = build_development_adjacency_field(
        image, FilmStockConfig(), low, "color_negative", work_long_edge=72
    )
    _, high_audit = build_development_adjacency_field(
        image, FilmStockConfig(), high, "color_negative", work_long_edge=72
    )

    assert low_audit["rate_amplitude"] > high_audit["rate_amplitude"]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("development_adjacency_strength", -0.1, "strength"),
        ("development_adjacency_strength", 1.1, "strength"),
        ("development_adjacency_radius", 0.0, "radius"),
    ),
)
def test_invalid_adjacency_settings_are_rejected(field, value, match):
    config = DarkroomConfig()
    setattr(config.chemistry, field, value)
    with pytest.raises(ValueError, match=match):
        develop_negative(_edge_image(8, 12), config, rng=np.random.default_rng(3))
