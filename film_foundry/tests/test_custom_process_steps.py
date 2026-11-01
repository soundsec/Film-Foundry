import dataclasses

import numpy as np
import pytest

from film_foundry.contracts.process_steps import validate_process_steps


def test_bath_specific_developer_character_is_shared_and_changes_material_kinetics():
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    from film_foundry.model.config import DevelopRecipeConfig
    from film_foundry.contracts.config_share import encode_config_share, decode_config_share
    rows = ({"action": "develop_silver", "developer_type": "fine_grain", "developer_exhaustion": .1}, {"action": "fix_halide", "fixer_type": "rapid"})
    recipe = DevelopRecipeConfig(process_steps=validate_process_steps(rows))
    fine = program_from_develop_recipe(recipe)
    hard = program_from_develop_recipe(dataclasses.replace(recipe, process_steps=({**rows[0], "developer_type": "high_contrast"}, rows[1])))
    assert fine.steps[0].developability_gamma != hard.steps[0].developability_gamma
    payload = decode_config_share(encode_config_share({"develop": {"process_steps": list(rows)}}))
    assert payload["develop"]["process_steps"][0]["developer_type"] == "fine_grain"
    from film_foundry.model.config import DarkroomConfig
    assert DarkroomConfig.from_dict({"film": {"image_polarity": "positive"}, "develop": {"chemistry": "cross"}}).develop.program_key == "color_negative"
    assert DarkroomConfig.from_dict({"film": {"image_polarity": "negative", "color_process": "monochrome"}, "develop": {"chemistry": "cross"}}).develop.program_key == "bw_reversal"
from film_foundry.contracts.config_share import encode_config_share, decode_config_share
from film_foundry.core.development import build_effective_development
from film_foundry.core.film_process.model import FilmProcessState, ReducedFilmMaterial
from film_foundry.core.film_process.operators import apply_process_program
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.model.config import DarkroomConfig, DevelopRecipeConfig


def _material():
    return ReducedFilmMaterial(key="synthetic", layer_count=3, halide_capacity=(1., 1., 1.),
        coupler_capacity=(1., 1., 1.), dye_absorption_matrix=((1., 0., 0.), (0., 1., 0.), (0., 0., 1.)))


def _latent():
    activation = np.repeat(np.array([.01, .5, .99], np.float32)[None, :, None], 3, axis=2)
    return FilmProcessState(halide=np.ones_like(activation), developability=activation,
        metallic_silver=np.zeros_like(activation), coupler=np.ones_like(activation), dye=np.zeros_like(activation))


def test_custom_baths_reorder_material_operators_without_mutating_latent():
    rows = ({"action": "develop_color", "strength": 1.}, {"action": "bleach_silver"}, {"action": "fix_halide"})
    recipe = DevelopRecipeConfig(process_steps=rows)
    latent = _latent()
    normal = apply_process_program(_material(), latent, program_from_develop_recipe(recipe))
    fixed_first = dataclasses.replace(recipe, process_steps=({"action": "fix_halide"}, *rows))
    other = apply_process_program(_material(), latent, program_from_develop_recipe(fixed_first))
    assert np.any(normal.state.dye != other.state.dye)
    np.testing.assert_array_equal(latent.halide, 1.)
    np.testing.assert_array_equal(latent.dye, 0.)


def test_custom_reversal_polarity_and_step_conditions_are_executable():
    rows = ({"action": "develop_silver"}, {"action": "activate_remaining_halide"},
            {"action": "develop_color"}, {"action": "bleach_silver"}, {"action": "fix_halide"})
    config = DarkroomConfig.from_dict({"develop": {"program_key": "custom", "process_steps": list(rows)}})
    program = program_from_develop_recipe(config.develop)
    assert program.output_polarity == "positive"
    result = apply_process_program(_material(), _latent(), program)
    assert float(result.state.dye[0, 0, 0]) > float(result.state.dye[0, 2, 0])
    cold = program_from_develop_recipe(dataclasses.replace(config.develop, temperature_c=10.))
    assert cold.steps[0].strength < program.steps[0].strength
    quiet = build_effective_development(DevelopRecipeConfig(agitation_time_s=0.))
    moving = build_effective_development(DevelopRecipeConfig(agitation_time_s=480.))
    assert quiet.activity < moving.activity
    from film_foundry.core.film_process.integration import _compatibility_for_program
    from film_foundry.model.config import FilmStockConfig
    stock = FilmStockConfig(image_polarity="positive")
    matching = _compatibility_for_program(stock, "custom", 3, program=program)
    assert matching.silver_development == 1.0
    payload = decode_config_share(encode_config_share({"develop": {"program_key": "custom", "chemistry": "native", "process_steps": list(rows), "agitation_time_s": 45.}}))
    restored = DarkroomConfig.from_dict(payload)
    assert restored.develop.agitation_time_s == 45.
    assert restored.develop.process_steps == rows
    disabled = tuple(dict(row, strength=0.) if row["action"] == "activate_remaining_halide" else row for row in rows)
    assert program_from_develop_recipe(dataclasses.replace(config.develop, process_steps=disabled)).output_polarity == "negative"


def test_invalid_process_steps_and_agitation_duration_fail_before_execution():
    for raw in ([], [{"action": "unknown"}], [{"action": "fix_halide", "strength": -1}],
                [{"action": "develop_silver", "time_min": 1, "agitation_time_s": 61}]):
        with pytest.raises(ValueError):
            validate_process_steps(raw)
    with pytest.raises(ValueError):
        build_effective_development(DevelopRecipeConfig(time_min=1., agitation_time_s=61.))


def test_default_kinetics_and_built_in_program_are_unchanged_when_new_fields_absent():
    base = DevelopRecipeConfig()
    assert build_effective_development(base) == build_effective_development(dataclasses.replace(base, agitation_time_s=None))
    a = program_from_develop_recipe(base)
    b = program_from_develop_recipe(DarkroomConfig.from_dict({"develop": {}}).develop)
    assert a == b


def test_desktop_public_formation_and_scan_honor_custom_positive_material():
    from film_foundry.core.engine import develop_negative, scan_negative
    rows = ({"action": "develop_silver"}, {"action": "activate_remaining_halide"},
            {"action": "develop_color"}, {"action": "bleach_silver"}, {"action": "fix_halide"})
    config = DarkroomConfig()
    config.enable_grain = config.enable_mtf = config.enable_halation = False
    config.develop.process_steps = rows
    image = np.linspace(.01, .95, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    medium = develop_negative(image, config)
    assert medium.image_polarity == "positive"
    master = medium.optical_density_rgb.copy()
    scan = scan_negative(medium)
    assert scan.interpreter_key == "positive_transparency_scan"
    np.testing.assert_array_equal(master, medium.optical_density_rgb)
    assert scan.output_srgb.shape == image.shape
