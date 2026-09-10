import copy

import numpy as np
import pytest

from film_foundry.core.engine import develop_negative, scan_negative
from film_foundry.core.light_piping import (
    REDUCED_LIGHT_PIPING_PLAN,
    light_piping_exposure_field,
)
from film_foundry.core.spatial_fields import (
    CompositeLayerExposureAdditionField,
    EdgeExposureAdditionField,
    LayerExposureAdditionField,
)
from film_foundry.model.config import DarkroomConfig, FilmStockConfig


def test_light_piping_declares_material_exposure_without_scene_source():
    payload = REDUCED_LIGHT_PIPING_PLAN.as_dict()
    assert payload["stage"] == "pre_latent_exposure"
    assert payload["quantity"] == "exposure_addition"
    assert payload["requires_global_source"] is False
    assert payload["random_field_policy"] == "none"
    assert payload["persistent_state"] is False


def test_declared_left_edge_is_monotonic_and_does_not_create_other_edges():
    field = EdgeExposureAdditionField(
        full_shape=(19, 41),
        layer_weights=(1.0, 0.5, 0.2),
        edge_weights=(0.0, 0.0, 0.0, 1.0),
        strength=0.3,
        depth_scale=0.12,
    )
    scalar = field._scalar_rows(0, 19)

    assert np.all(np.diff(scalar[9]) <= 0.0)
    assert float(scalar[9, 0]) == 1.0
    assert float(scalar[9, -1]) < 0.001
    assert np.array_equal(scalar[0], scalar[-1])


def test_long_edge_mode_follows_frame_geometry_not_scene_content():
    film = FilmStockConfig(
        light_piping_strength=0.25,
        light_piping_edge_mode="long_edges",
    )
    landscape = light_piping_exposure_field((20, 60, 3), film, layer_count=3)
    portrait = light_piping_exposure_field((60, 20, 3), film, layer_count=3)

    assert landscape is not None and landscape.edge_weights == (1.0, 0.0, 1.0, 0.0)
    assert portrait is not None and portrait.edge_weights == (0.0, 1.0, 0.0, 1.0)


def test_edge_exposure_and_composite_are_exact_across_material_rows():
    shape = (31, 43, 3)
    scalar = np.linspace(0.0, 0.2, shape[0] * shape[1], dtype=np.float32).reshape(shape[:2])
    halation = LayerExposureAdditionField(scalar, (1.0, 0.4, 0.1), 0.3)
    piping = EdgeExposureAdditionField(
        full_shape=shape[:2],
        layer_weights=(1.0, 0.6, 0.2),
        edge_weights=(0.0, 1.0, 0.0, 0.0),
        strength=0.2,
        depth_scale=0.08,
        row_chunk=7,
    )
    composite = CompositeLayerExposureAdditionField((halation, piping))
    dense = np.zeros(shape, dtype=np.float32)
    composite.add_scaled_to(dense, np.ones(3, dtype=np.float32))
    tiled = np.zeros_like(dense)
    for start in range(0, shape[0], 6):
        stop = min(start + 6, shape[0])
        composite.slice_rows(start, stop).add_scaled_to(
            tiled[start:stop],
            np.ones(3, dtype=np.float32),
        )

    assert np.array_equal(tiled, dense)


def test_light_piping_enters_latent_formation_and_scan_remains_read_only():
    image = np.full((29, 47, 3), 0.22, dtype=np.float32)
    baseline = DarkroomConfig()
    baseline.enable_mtf = False
    baseline.enable_halation = False
    baseline.enable_grain = False
    baseline.chemistry.program_key = "color_negative"
    piped = copy.deepcopy(baseline)
    piped.film.light_piping_strength = 0.22
    piped.film.light_piping_depth = 0.10
    piped.film.light_piping_edge_mode = "left"
    piped.film.light_piping_layer_weights = (1.0, 0.45, 0.18)

    plain_medium = develop_negative(image, baseline, rng=np.random.default_rng(8))
    piped_medium = develop_negative(image, piped, rng=np.random.default_rng(8))
    density_delta = piped_medium.density_cmy - plain_medium.density_cmy
    assert float(np.mean(density_delta[:, :3])) > float(np.mean(density_delta[:, -3:]))
    assert float(np.max(density_delta)) > 0.0

    optical_before = piped_medium.optical_density_rgb.copy()
    density_before = piped_medium.density_cmy.copy()
    scan_negative(piped_medium, piped)
    assert np.array_equal(piped_medium.optical_density_rgb, optical_before)
    assert np.array_equal(piped_medium.density_cmy, density_before)

    formation = piped_medium.metadata["film_process_model"]["light_piping_formation"]
    assert formation["applied"] is True
    assert formation["source_policy"] == "declared_frame_edges_only"
    assert formation["reads_scene_pixels"] is False
    assert formation["scanner_owned"] is False


def test_disabled_light_piping_is_strict_engine_identity():
    image = np.random.default_rng(909).uniform(0.01, 0.95, (17, 23, 3)).astype(np.float32)
    baseline = DarkroomConfig()
    baseline.enable_grain = False
    baseline.enable_halation = False
    baseline.enable_mtf = False
    explicit_off = copy.deepcopy(baseline)
    explicit_off.film.light_piping_strength = 0.0
    explicit_off.film.light_piping_edge_mode = "all_edges"

    first = develop_negative(image, baseline, rng=np.random.default_rng(5))
    second = develop_negative(image, explicit_off, rng=np.random.default_rng(5))
    assert np.array_equal(first.density_cmy, second.density_cmy)
    assert np.array_equal(first.optical_density_rgb, second.optical_density_rgb)


def test_light_piping_and_halation_composite_is_exact_across_material_pool_tiles():
    from film_foundry.core.engine import _develop_reduced_material_tiled
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    rng = np.random.default_rng(20260725)
    image = rng.uniform(0.02, 0.9, (21, 35, 3)).astype(np.float32)
    config = DarkroomConfig()
    config.chemistry.program_key = "color_negative"
    halation = LayerExposureAdditionField(
        rng.uniform(0.0, 0.12, image.shape[:2]).astype(np.float32),
        (1.0, 0.42, 0.16),
        0.18,
    )
    piping = EdgeExposureAdditionField(
        full_shape=image.shape[:2],
        layer_weights=(1.0, 0.45, 0.18),
        edge_weights=(1.0, 0.0, 0.0, 0.0),
        strength=0.2,
        depth_scale=0.08,
    )
    addition = CompositeLayerExposureAdditionField((halation, piping))

    full = develop_color_negative_reduced(
        image,
        config.film,
        config.chemistry,
        latent_layer_exposure_addition=addition,
    )
    tiled_density, tiled_optical, _, _ = _develop_reduced_material_tiled(
        image,
        config,
        "color_negative",
        5,
        latent_layer_exposure_addition=addition,
    )

    assert np.array_equal(tiled_density, full.density_cmy)
    assert np.array_equal(tiled_optical, full.optical_density_rgb)


def test_light_piping_follows_reversal_material_competition_not_posthoc_brightening():
    image = np.full((31, 51, 3), 0.25, dtype=np.float32)
    baseline = DarkroomConfig()
    baseline.mode = "color_reversal"
    baseline.film.medium_process = "slide"
    baseline.film.image_polarity = "positive"
    baseline.chemistry.program_key = "color_reversal"
    baseline.enable_mtf = False
    baseline.enable_halation = False
    baseline.enable_grain = False
    piped = copy.deepcopy(baseline)
    piped.film.light_piping_strength = 0.2
    piped.film.light_piping_depth = 0.1
    piped.film.light_piping_edge_mode = "left"

    plain = develop_negative(image, baseline, rng=np.random.default_rng(4))
    with_piping = develop_negative(image, piped, rng=np.random.default_rng(4))
    delta = with_piping.density_cmy - plain.density_cmy

    # Extra first-exposure activation consumes material that would otherwise
    # form the second-development positive image.
    assert float(np.mean(delta[:, :3])) < -0.05
    assert abs(float(np.mean(delta[:, -3:]))) < 1e-5
    assert with_piping.image_polarity == "positive"


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("light_piping_edge_mode", "diagonal", "edge mode"),
        ("light_piping_depth", 0.0, "depth"),
        ("light_piping_strength", -0.1, "strength"),
        ("light_piping_layer_weights", (1.0, -0.1, 0.2), "layer_weights"),
    ),
)
def test_invalid_light_piping_material_settings_are_rejected(field, value, match):
    config = DarkroomConfig()
    setattr(config.film, field, value)
    with pytest.raises(ValueError, match=match):
        develop_negative(
            np.full((5, 7, 3), 0.3, dtype=np.float32),
            config,
            rng=np.random.default_rng(1),
        )
