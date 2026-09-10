import copy

import numpy as np
import pytest

from film_foundry.core.emulsion_spread import (
    COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN,
    emulsion_highlight_spread_field,
)
from film_foundry.core.engine import develop_negative, scan_medium_output
from film_foundry.core.halation import halation_return_field
from film_foundry.core.spatial_fields import LayerExposureAdditionField
from film_foundry.model.config import DarkroomConfig, FilmStockConfig


def _point_highlight(height: int = 65, width: int = 81) -> np.ndarray:
    image = np.full((height, width, 3), 0.08, dtype=np.float32)
    image[height // 2, width // 2] = (1.0, 0.0, 0.0)
    return image


def test_disabled_emulsion_spread_is_strict_identity_and_declares_material_stage():
    image = _point_highlight()
    before = image.copy()
    film = FilmStockConfig(emulsion_highlight_spread_strength=0.0)

    field = emulsion_highlight_spread_field(
        image,
        film,
        layer_count=3,
        work_long_edge=41,
    )

    assert field is None
    assert np.array_equal(image, before)
    assert COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN.stage == "pre_latent_exposure"
    assert COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN.quantity == "exposure_addition"
    assert COMPACT_EMULSION_HIGHLIGHT_SPREAD_PLAN.persistent_state is False


def test_emulsion_spread_preserves_source_layer_and_remains_compact():
    image = _point_highlight()
    film = FilmStockConfig(
        layer_sensitivity_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        emulsion_highlight_spread_strength=0.45,
        emulsion_highlight_spread_radius=0.018,
        emulsion_highlight_spread_threshold=0.30,
        emulsion_highlight_spread_softness=0.08,
        emulsion_highlight_spread_layer_radius_scales=(1.0, 1.0, 1.0),
        emulsion_highlight_spread_layer_strengths=(1.0, 1.0, 1.0),
    )
    field = emulsion_highlight_spread_field(
        image,
        film,
        layer_count=3,
        work_long_edge=max(image.shape[:2]),
    )
    assert field is not None
    dense = np.zeros_like(image)
    field.add_scaled_to(dense, np.ones(3, dtype=np.float32))
    y, x = image.shape[0] // 2, image.shape[1] // 2

    assert dense[y, x, 0] > 0.0
    assert dense[y, x + 1, 0] > 0.0
    assert dense[y, x + 8, 0] < dense[y, x + 1, 0]
    assert np.count_nonzero(dense[..., 1]) == 0
    assert np.count_nonzero(dense[..., 2]) == 0
    assert np.array_equal(image, _point_highlight())


def test_lazy_emulsion_spread_is_exact_across_material_row_partitions():
    image = np.linspace(0.03, 1.20, 73 * 91 * 3, dtype=np.float32).reshape(73, 91, 3)
    film = FilmStockConfig(
        emulsion_highlight_spread_strength=0.32,
        emulsion_highlight_spread_threshold=0.48,
    )
    field = emulsion_highlight_spread_field(
        image,
        film,
        layer_count=3,
        work_long_edge=47,
    )
    assert field is not None
    assert field.work_shape[:2] == (38, 47)
    full = np.zeros_like(image)
    tiled = np.zeros_like(image)
    scales = np.asarray((0.9, 1.0, 1.1), dtype=np.float32)
    field.add_scaled_to(full, scales)
    for start in range(0, image.shape[0], 11):
        stop = min(start + 11, image.shape[0])
        field.slice_rows(start, stop).add_scaled_to(tiled[start:stop], scales)

    assert np.array_equal(full, tiled)
    assert field.work_shape[0] * field.work_shape[1] * 3 < image.size


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    (
        ("emulsion_highlight_spread_strength", -0.1, "strength"),
        ("emulsion_highlight_spread_radius", 0.0, "radius"),
        ("emulsion_highlight_spread_threshold", -0.1, "threshold"),
        ("emulsion_highlight_spread_softness", 0.0, "softness"),
        (
            "emulsion_highlight_spread_layer_radius_scales",
            (1.0, 0.0, 1.0),
            "radius_scales",
        ),
        (
            "emulsion_highlight_spread_layer_strengths",
            (1.0, -0.1, 1.0),
            "layer_strengths",
        ),
    ),
)
def test_invalid_emulsion_spread_material_settings_fail_before_formation(
    field_name,
    value,
    message,
):
    config = DarkroomConfig()
    config.enable_grain = False
    config.enable_halation = False
    setattr(config.film, field_name, value)
    if field_name != "emulsion_highlight_spread_strength":
        config.film.emulsion_highlight_spread_strength = 0.2

    with pytest.raises(ValueError, match=message):
        develop_negative(_point_highlight(17, 23), config)


def _process_config(program_key: str) -> DarkroomConfig:
    config = DarkroomConfig()
    config.chemistry.program_key = program_key
    config.enable_mtf = False
    config.enable_halation = False
    config.enable_grain = False
    config.processing.emulsion_spread_work_long_edge = 53
    config.film.emulsion_highlight_spread_strength = 0.38
    config.film.emulsion_highlight_spread_radius = 0.012
    config.film.emulsion_highlight_spread_threshold = 0.28
    config.film.emulsion_highlight_spread_softness = 0.08
    if program_key.startswith("bw_"):
        config.mode = "bw_negative"
        config.film.color_process = "monochrome"
    return config


def test_emulsion_spread_and_adjacency_are_exact_across_material_pool_tiling():
    image = np.linspace(0.03, 1.0, 61 * 79 * 3, dtype=np.float32).reshape(61, 79, 3)
    untiled = _process_config("color_negative")
    untiled.processing.emulsion_spread_work_long_edge = 43
    untiled.processing.adjacency_work_long_edge = 31
    untiled.chemistry.development_adjacency_strength = 0.35
    untiled.chemistry.development_adjacency_radius = 0.035
    untiled.processing.material_tile_rows = 0
    tiled = copy.deepcopy(untiled)
    tiled.processing.material_tile_rows = 9
    tiled.processing.material_tile_threshold_megapixels = 0.0001

    whole = develop_negative(image, untiled)
    partitioned = develop_negative(image, tiled)

    assert np.array_equal(whole.density_cmy, partitioned.density_cmy)
    assert np.array_equal(whole.optical_density_rgb, partitioned.optical_density_rgb)


def test_emulsion_spread_and_halation_retain_distinct_spatial_and_layer_roles():
    height, width = 257, 385
    image = np.full((height, width, 3), 0.04, dtype=np.float32)
    image[62:65, 94:97] = (1.0, 0.0, 0.0)
    image[62:65, 288:291] = (0.0, 1.0, 1.0)
    image[120:220, 120:270] = 0.92
    film = FilmStockConfig(
        layer_sensitivity_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        emulsion_highlight_spread_strength=0.35,
        emulsion_highlight_spread_radius=0.006,
        emulsion_highlight_spread_threshold=0.45,
        emulsion_highlight_spread_softness=0.08,
        emulsion_highlight_spread_layer_radius_scales=(1.0, 1.0, 1.0),
        emulsion_highlight_spread_layer_strengths=(1.0, 1.0, 1.0),
        halation_strength=0.20,
        halation_threshold=0.45,
        halation_softness=0.08,
        halation_peak_threshold=0.01,
        halation_peak_softness=0.04,
        halation_area_suppression=0.90,
        halation_return_model="layer_selective",
        halation_layer_return_weights=(1.0, 0.42, 0.16),
        halation_spread_scale_weights=(0.0, 1.0, 0.0),
    )
    emulsion_field = emulsion_highlight_spread_field(
        image,
        film,
        layer_count=3,
        work_long_edge=width,
    )
    assert emulsion_field is not None
    emulsion = np.zeros_like(image)
    emulsion_field.add_scaled_to(emulsion, np.ones(3, dtype=np.float32))
    halation_scalar = halation_return_field(
        image,
        film,
        work_long_edge=width,
        spread_scale_weights=film.halation_spread_scale_weights,
    )
    halation = np.zeros_like(image)
    LayerExposureAdditionField(
        np.maximum(halation_scalar, 0.0),
        film.halation_layer_return_weights,
        film.halation_strength,
    ).add_scaled_to(halation, np.ones(3, dtype=np.float32))

    # Internal spread retains source-layer colour; support return uses one
    # scalar leaked-energy source and the material's fixed depth weights.
    assert emulsion[63, 95, 0] > 0.0
    assert emulsion[63, 95, 1] == 0.0
    assert emulsion[63, 289, 0] == 0.0
    assert emulsion[63, 289, 1] == emulsion[63, 289, 2] > 0.0
    assert np.isclose(halation[63, 289, 1] / halation[63, 289, 0], 0.42)
    assert np.isclose(halation[63, 289, 2] / halation[63, 289, 0], 0.16)

    # Compact spread has vanished ten pixels from the cyan core while the
    # base/backing-return PSF still carries a measurable tail.
    assert emulsion[63, 300, 1] == 0.0
    assert halation[63, 300, 0] > 0.0
    # Broad highlights still create local emulsion exposure; halation's peak
    # and area gates keep the broad-centre return much weaker.
    assert emulsion[170, 195, 0] > 20.0 * halation[170, 195, 0]


@pytest.mark.parametrize(
    "program_key",
    ("bw_negative", "bw_reversal", "color_negative", "color_reversal"),
)
def test_emulsion_spread_enters_all_unified_processes_and_scan_is_read_only(program_key):
    image = np.full((37, 53, 3), 0.10, dtype=np.float32)
    image[15:22, 23:30] = (1.0, 0.72, 0.42)
    enabled = _process_config(program_key)
    disabled = copy.deepcopy(enabled)
    disabled.film.emulsion_highlight_spread_strength = 0.0

    spread_medium = develop_negative(image, enabled)
    clean_medium = develop_negative(image, disabled)
    contract = spread_medium.metadata["film_process_model"][
        "emulsion_highlight_spread_formation"
    ]

    assert np.array_equal(spread_medium.after_halation, clean_medium.after_halation)
    assert not np.allclose(
        spread_medium.optical_density_rgb,
        clean_medium.optical_density_rgb,
    )
    assert contract["applied"] is True
    assert contract["stage"] == "pre_latent_exposure"
    assert contract["support_or_backing_return"] is False
    assert contract["scanner_owned"] is False
    optical_before = spread_medium.optical_density_rgb.copy()
    scan_medium_output(spread_medium, enabled, "auto")
    assert np.array_equal(spread_medium.optical_density_rgb, optical_before)
