import copy
from dataclasses import asdict

import numpy as np
import pytest

from film_foundry.core.dir_interimage import build_dir_material_response_field
from film_foundry.core.engine import develop_negative, scan_negative
from film_foundry.core.film_process.integration import (
    _color_material,
    _compatibility_for_program,
    _material_latent_state,
)
from film_foundry.core.film_process.operators import (
    provisional_formed_dye_for_program,
)
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.model.config import (
    DarkroomConfig,
    DevelopRecipeConfig,
    FilmStockConfig,
)


ZERO_DIR_MATRIX = (
    (0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0),
    (0.0, 0.0, 0.0),
)


def _single_record_edge(height=45, width=73):
    image = np.full((height, width, 3), 0.025, dtype=np.float32)
    image[:, : width // 2, 0] = 0.85
    return image


def _dir_film(**overrides):
    values = dict(
        layer_sensitivity_matrix=(
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
        ),
        dir_diffusion_radius=0.025,
        # Source layer zero inhibits only target layer one. Values are the
        # effective material response, not decomposed release/strength factors.
        dir_material_response_matrix=(
            (0.0, 0.40, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ),
    )
    values.update(overrides)
    return FilmStockConfig(**values)


def _color_config(matrix=None, *, reversal=False):
    config = DarkroomConfig()
    config.mode = "color_reversal" if reversal else "color_negative"
    config.chemistry.program_key = config.mode
    config.chemistry.dir_process_effectiveness = 1.0
    config.enable_mtf = False
    config.enable_halation = False
    config.enable_grain = False
    config.film = _dir_film(
        dir_material_response_matrix=(
            ZERO_DIR_MATRIX if matrix is None else matrix
        )
    )
    if reversal:
        config.film.medium_process = "positive"
        config.film.image_polarity = "positive"
    return config


def test_zero_material_response_is_a_strict_no_field_identity():
    field, audit = build_dir_material_response_field(
        _single_record_edge(),
        FilmStockConfig(),
        DevelopRecipeConfig(),
        "color_negative",
        work_long_edge=41,
    )

    assert field is None
    assert audit["material_dir_present"] is False
    assert audit["enabled"] is False
    assert audit["applied"] is False
    assert audit["skip_reason"] == "no_material_dir_response"
    assert audit["provisional_authoritative_pool_consumptions"] == 0


def test_process_gate_scales_material_response_and_suppresses_standard_reversal():
    image = _single_record_edge(31, 47)
    film = _dir_film()
    full, full_audit = build_dir_material_response_field(
        image,
        film,
        DevelopRecipeConfig(dir_process_effectiveness=1.0),
        "color_negative",
        work_long_edge=43,
    )
    half, half_audit = build_dir_material_response_field(
        image,
        film,
        DevelopRecipeConfig(dir_process_effectiveness=0.5),
        "color_negative",
        work_long_edge=43,
    )
    reversal, reversal_audit = build_dir_material_response_field(
        image,
        film,
        DevelopRecipeConfig(program_key="color_reversal"),
        "color_reversal",
        work_long_edge=43,
    )

    assert full is not None and half is not None
    full_rate = full.slice_rows(0, image.shape[0])
    half_rate = half.slice_rows(0, image.shape[0])
    assert full_audit["source_mean_per_layer"] == half_audit["source_mean_per_layer"]
    assert np.allclose(1.0 - half_rate, 0.5 * (1.0 - full_rate), atol=1e-7)
    assert reversal is None
    assert reversal_audit["skip_reason"] == "process_suppresses_dir_inhibitor"


def test_source_target_matrix_remains_a_layer_material_response():
    image = _single_record_edge()
    original = image.copy()
    field, audit = build_dir_material_response_field(
        image,
        _dir_film(),
        DevelopRecipeConfig(program_key="color_negative"),
        "color_negative",
        work_long_edge=53,
    )

    assert field is not None
    rates = field.slice_rows(0, image.shape[0])
    assert np.array_equal(rates[..., 0], np.ones(image.shape[:2], dtype=np.float32))
    assert np.array_equal(rates[..., 2], np.ones(image.shape[:2], dtype=np.float32))
    assert float(np.min(rates[..., 1])) < 1.0
    assert float(rates[:, : image.shape[1] // 2, 1].mean()) < float(
        rates[:, image.shape[1] // 2 :, 1].mean()
    )
    assert audit["plan"]["key"] == "dir_material_response"
    assert audit["active_source_layers"] == (0,)
    assert audit["step_scope"] == "declared_color_development_only"
    assert audit["authoritative_pool_consumptions"] == 1
    assert np.array_equal(image, original)


def test_uniform_material_reaction_does_not_invent_edge_texture():
    image = np.full((39, 61, 3), 0.42, dtype=np.float32)
    field, _ = build_dir_material_response_field(
        image,
        _dir_film(
            dir_material_response_matrix=(
                (0.28, 0.05, 0.02),
                (0.05, 0.28, 0.05),
                (0.02, 0.05, 0.28),
            )
        ),
        DevelopRecipeConfig(program_key="color_negative"),
        "color_negative",
        work_long_edge=47,
    )

    assert field is not None
    rates = field.slice_rows(0, image.shape[0])
    assert float(np.max(np.ptp(rates, axis=(0, 1)))) < 2e-6
    assert float(np.min(rates)) >= 0.0
    assert float(np.max(rates)) <= 1.0


def test_intralayer_response_changes_formation_edge_not_scanner_rgb():
    image = _single_record_edge()
    matrix = (
        (0.40, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    )
    config = _color_config(matrix)
    config.processing.dir_work_long_edge = image.shape[1]

    medium = develop_negative(image, config, rng=np.random.default_rng(67))
    row = medium.density_cmy[image.shape[0] // 2, :, 0]
    boundary = image.shape[1] // 2
    high_plateau = float(np.mean(row[10:25]))
    high_edge = float(np.mean(row[boundary - 2 : boundary]))
    optical_before_scan = medium.optical_density_rgb.copy()
    scan_negative(medium, config)

    assert high_edge > high_plateau + 0.02
    assert np.array_equal(medium.optical_density_rgb, optical_before_scan)
    assert medium.metadata["dir_material_response"]["applied"] is True


def test_provisional_reversal_source_never_consumes_the_authoritative_state():
    image = _single_record_edge(21, 33)
    film = _dir_film()
    recipe = DevelopRecipeConfig(program_key="color_reversal")
    material = _color_material(film)
    state = _material_latent_state(image, film, material)
    before = state.copy()
    program = program_from_develop_recipe(
        recipe,
        mode="color_reversal",
        material_process="reversal",
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)

    provisional, step = provisional_formed_dye_for_program(
        state,
        program,
        compatibility,
    )

    assert step.action.value == "develop_color"
    assert float(np.max(provisional)) > 0.0
    for name in ("halide", "developability", "metallic_silver", "coupler", "dye"):
        assert np.array_equal(getattr(state, name), getattr(before, name))


@pytest.mark.parametrize("program_kind", ("color_negative", "color_reversal"))
def test_material_response_is_exact_across_material_pool_tiles(program_kind):
    image = _single_record_edge(43, 71)
    base = _color_config(
        (
            (0.0, 0.40, 0.0),
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0),
        ),
        reversal=program_kind == "color_reversal",
    )
    base.processing.dir_work_long_edge = 47
    untiled = copy.deepcopy(base)
    untiled.processing.material_tile_rows = 0
    tiled = copy.deepcopy(base)
    tiled.processing.material_tile_rows = 7
    tiled.processing.material_tile_threshold_megapixels = 1e-9

    full = develop_negative(image, untiled, rng=np.random.default_rng(71))
    rows = develop_negative(image, tiled, rng=np.random.default_rng(71))

    assert np.array_equal(rows.density_cmy, full.density_cmy)
    assert np.array_equal(rows.optical_density_rgb, full.optical_density_rgb)
    audit = rows.metadata["dir_material_response"]
    assert audit["applied"] is True
    assert audit["representation"] == "lightweight_dir_material_response_v2"


def test_nonzero_material_response_rejects_black_and_white_programs():
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.chemistry.program_key = "bw_negative"
    config.film.dir_material_response_matrix = (
        (0.25, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    )

    with pytest.raises(ValueError, match="DIR material response requires"):
        develop_negative(_single_record_edge(9, 13), config, rng=np.random.default_rng(79))


def test_nonzero_dir_rejects_monochrome_material_even_in_a_color_program():
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.film.color_process = "monochrome"
    config.chemistry.program_key = "color_negative"
    config.film.dir_material_response_matrix = (
        (0.25, 0.0, 0.0),
        (0.0, 0.0, 0.0),
        (0.0, 0.0, 0.0),
    )

    with pytest.raises(ValueError, match="color coupler material"):
        develop_negative(_single_record_edge(9, 13), config, rng=np.random.default_rng(79))


def test_material_response_round_trip_and_legacy_factorization_migration():
    config = DarkroomConfig()
    config.film.dir_diffusion_radius = 0.0042
    config.film.dir_material_response_matrix = (
        (0.22, 0.04, 0.01),
        (0.04, 0.21, 0.04),
        (0.01, 0.04, 0.20),
    )
    config.processing.dir_work_long_edge = 1450
    config.chemistry.dir_process_effectiveness = 0.65
    restored = DarkroomConfig.from_dict(asdict(config))

    assert restored.film.dir_material_response_matrix == config.film.dir_material_response_matrix
    assert restored.film.dir_diffusion_radius == 0.0042
    assert restored.processing.dir_work_long_edge == 1450
    assert restored.chemistry.dir_process_effectiveness == 0.65

    legacy = DarkroomConfig.from_dict(
        {
            "film": {
                "dir_inhibition_strength": 0.5,
                "dir_release_per_layer": [0.8, 0.6, 0.4],
                "dir_layer_coupling_matrix": [
                    [0.5, 0.2, 0.0],
                    [0.1, 0.5, 0.1],
                    [0.0, 0.2, 0.5],
                ],
            }
        }
    )
    assert np.allclose(
        legacy.film.dir_material_response_matrix,
        (
            (0.20, 0.08, 0.0),
            (0.03, 0.15, 0.03),
            (0.0, 0.04, 0.10),
        ),
    )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    (
        ("dir_diffusion_radius", 0.0, "radius"),
        (
            "dir_material_response_matrix",
            ((0.7, 0.5, 0.0), (0.4, 0.5, 0.0), (0.0, 0.0, 0.1)),
            "matrix",
        ),
    ),
)
def test_invalid_dir_material_settings_fail_before_formation(field, value, match):
    config = _color_config(
        ((0.2, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    )
    setattr(config.film, field, value)

    with pytest.raises(ValueError, match=match):
        develop_negative(_single_record_edge(9, 13), config, rng=np.random.default_rng(83))


@pytest.mark.parametrize("value", (-0.1, 1.1, float("nan")))
def test_invalid_dir_process_effectiveness_fails_before_formation(value):
    config = _color_config(
        ((0.2, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    )
    config.chemistry.dir_process_effectiveness = value

    with pytest.raises(ValueError, match="dir_process_effectiveness"):
        develop_negative(_single_record_edge(9, 13), config, rng=np.random.default_rng(89))
