from dataclasses import replace

import numpy as np
import pytest

from film_foundry.core.execution_topology import (
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.core.film_process import (
    CompatibilityProfile,
    FilmProcessAction,
    FilmProcessProgram,
    FilmProcessState,
    FilmProcessStep,
    ReducedFilmMaterial,
    apply_process_program,
    black_and_white_negative_program,
    black_and_white_reversal_program,
    color_negative_program,
    color_reversal_program,
    compose_optical_density_rgb,
    shape_positive_density_fraction,
    shape_positive_dye_chroma,
)
from film_foundry.core.film_process.legacy import final_medium_from_legacy_density
from film_foundry.core.film_process.operators import _development_rate_field
from film_foundry.core.film_process.recipe import program_from_develop_recipe
from film_foundry.core.scanner import negative_total_density_rgb
from film_foundry.model.config import DarkroomConfig, DevelopRecipeConfig, FilmStockConfig


def _historical_development_rate_field(
    state,
    step,
    compatibility,
    local=None,
):
    """Frozen expression topology used before private scratch reuse."""
    layers = state.halide.shape[-1]
    selectivity = np.ones(layers, dtype=np.float32)
    if step.layer_selectivity is not None:
        selectivity *= np.asarray(step.layer_selectivity, dtype=np.float32).reshape(layers)
    if compatibility.layer_balance is not None:
        selectivity *= np.asarray(compatibility.layer_balance, dtype=np.float32).reshape(layers)
    rate = np.clip(
        float(step.strength)
        * float(np.clip(compatibility.silver_development, 0.0, 1.0))
        * selectivity,
        0.0,
        1.0,
    ).reshape((1,) * (state.halide.ndim - 1) + (layers,))
    response_gamma = float(np.clip(step.developability_gamma, 0.25, 3.0))
    compensation = float(np.clip(step.highlight_compensation, 0.0, 0.95))
    activation = np.clip(
        np.asarray(state.developability, dtype=np.float32),
        0.0,
        1.0,
    )
    if abs(response_gamma - 1.0) > 1e-6:
        rate = rate * np.power(
            np.maximum(activation, 1e-6) / 0.5,
            response_gamma - 1.0,
        )
    if compensation > 0.0:
        gate = np.clip((activation - 0.35) / 0.65, 0.0, 1.0)
        gate = gate * gate * (3.0 - 2.0 * gate)
        rate = rate * (1.0 - 0.65 * compensation * gate)
    if local is not None:
        if local.shape == state.halide.shape[:-1]:
            local = local[..., None]
        rate = rate * local
    return np.clip(rate, 0.0, 1.0).astype(np.float32, copy=False)


@pytest.mark.parametrize(
    ("gamma", "compensation", "with_local"),
    (
        (1.75, 0.0, False),
        (1.0, 0.62, False),
        (0.68, 0.71, True),
    ),
)
def test_development_rate_scratch_reuse_is_bit_exact(
    gamma,
    compensation,
    with_local,
):
    rng = np.random.default_rng(20260729)
    shape = (11, 17, 3)
    state = FilmProcessState(
        halide=rng.uniform(0.1, 1.0, shape).astype(np.float32),
        developability=rng.uniform(0.0, 1.0, shape).astype(np.float32),
        metallic_silver=np.zeros(shape, dtype=np.float32),
    )
    step = FilmProcessStep(
        FilmProcessAction.DEVELOP_COLOR,
        strength=0.91,
        layer_selectivity=(0.83, 1.07, 0.94),
        developability_gamma=gamma,
        highlight_compensation=compensation,
    )
    compatibility = CompatibilityProfile(
        silver_development=0.88,
        layer_balance=(1.06, 0.79, 0.97),
    )
    local = (
        rng.uniform(0.72, 1.19, shape[:-1]).astype(np.float32)
        if with_local
        else None
    )
    activation_before = state.developability.copy()
    expected = _historical_development_rate_field(
        state,
        step,
        compatibility,
        local,
    )

    private_activation = _development_rate_field(
        state,
        step,
        compatibility,
        local_development_rate=local,
        developability_is_bounded=False,
    )
    bounded_state = _development_rate_field(
        state,
        step,
        compatibility,
        local_development_rate=local,
        developability_is_bounded=True,
    )
    with execution_topology(REFERENCE_TOPOLOGY):
        reference_dispatch = _development_rate_field(
            state,
            step,
            compatibility,
            local_development_rate=local,
            developability_is_bounded=True,
        )

    assert np.array_equal(private_activation, expected)
    assert np.array_equal(bounded_state, expected)
    assert np.array_equal(reference_dispatch, expected)
    assert np.array_equal(state.developability, activation_before)


def test_optical_master_is_exact_sum_of_component_contributions():
    dye_layers = np.asarray([[[0.20, 0.35, 0.50]]], dtype=np.float32)
    absorption = np.asarray(
        ((1.0, 0.12, 0.03), (0.05, 1.0, 0.14), (0.02, 0.18, 1.0)),
        dtype=np.float32,
    )
    silver = np.full((1, 1, 3), 0.11, dtype=np.float32)
    residual = np.asarray([[[0.02, 0.03, 0.04]]], dtype=np.float32)
    bleached = np.asarray([[[0.04, 0.03, 0.02]]], dtype=np.float32)
    base = np.asarray((0.18, 0.42, 0.70), dtype=np.float32)
    auxiliary = np.asarray((0.01, 0.02, 0.03), dtype=np.float32)

    master = compose_optical_density_rgb(
        dye_density_layers=dye_layers,
        dye_absorption_matrix=absorption,
        silver_density_rgb=silver,
        residual_halide_density_rgb=residual,
        bleached_halide_density_rgb=bleached,
        base_density_rgb=base,
        auxiliary_density_rgb=auxiliary,
    )
    expected = (
        np.einsum("...l,rl->...r", dye_layers, absorption)
        + silver
        + residual
        + bleached
        + base
        + auxiliary
    )

    assert np.allclose(master, expected)
    assert master.dtype == np.float32

    public_base = base.reshape(1, 1, 3).copy()
    public_before = public_base.copy()
    non_consumed = compose_optical_density_rgb(
        dye_density_layers=dye_layers,
        dye_absorption_matrix=absorption,
        silver_density_rgb=silver,
        residual_halide_density_rgb=residual,
        bleached_halide_density_rgb=bleached,
        base_density_rgb=public_base,
        auxiliary_density_rgb=auxiliary,
    )
    assert np.array_equal(non_consumed, master)
    assert np.array_equal(public_base, public_before)
    assert not np.shares_memory(non_consumed, public_base)

    private_base = base.reshape(1, 1, 3).copy()
    consumed = compose_optical_density_rgb(
        dye_density_layers=dye_layers,
        dye_absorption_matrix=absorption,
        silver_density_rgb=silver,
        residual_halide_density_rgb=residual,
        bleached_halide_density_rgb=bleached,
        base_density_rgb=private_base,
        auxiliary_density_rgb=auxiliary,
        consume_base_density=True,
    )
    assert np.array_equal(consumed, master)
    assert np.shares_memory(consumed, private_base)


def test_reduced_color_silver_rgb_is_a_zero_copy_neutral_view():
    from film_foundry.core.film_process.integration import (
        develop_color_negative_reduced,
    )

    image = np.linspace(0.08, 0.92, 9 * 13 * 3, dtype=np.float32).reshape(9, 13, 3)
    result = develop_color_negative_reduced(
        image,
        FilmStockConfig(),
        DevelopRecipeConfig(),
    )
    rgb = result.silver_density_rgb

    assert rgb.shape == (*result.silver_density.shape, 3)
    assert np.shares_memory(rgb, result.silver_density)
    assert np.array_equal(rgb[..., 0], result.silver_density)
    assert np.array_equal(rgb[..., 0], rgb[..., 1])
    assert np.array_equal(rgb[..., 1], rgb[..., 2])


def test_final_medium_component_arrays_are_read_only_and_do_not_alias_process_state():
    material = ReducedFilmMaterial(
        key="immutable_final",
        layer_count=1,
        halide_capacity=(1.0,),
        silver_density_per_layer=(1.0,),
        residual_halide_density_per_layer=(0.1,),
    )
    latent = material.expose(np.asarray([[[0.5]]], dtype=np.float32))
    result = apply_process_program(
        material,
        latent,
        black_and_white_negative_program(),
    )
    before = result.final_medium.metallic_silver.copy()

    result.state.metallic_silver.fill(0.0)

    assert np.array_equal(result.final_medium.metallic_silver, before)
    assert result.final_medium.metallic_silver.flags.writeable is False
    assert result.final_medium.residual_halide.flags.writeable is False
    with np.testing.assert_raises(ValueError):
        result.final_medium.metallic_silver[0, 0, 0] = 0.0


def test_experimental_mask_bleach_is_separate_from_standard_silver_bleach():
    material = ReducedFilmMaterial(
        key="masked_negative",
        layer_count=3,
        halide_capacity=(1.0, 1.0, 1.0),
        coupler_capacity=(1.0, 1.0, 1.0),
        dye_absorption_matrix=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        silver_density_per_layer=(1 / 3, 1 / 3, 1 / 3),
        residual_halide_density_per_layer=(0.04, 0.04, 0.04),
        base_density_rgb=(0.18, 0.55, 0.85),
        clear_support_density_rgb=(0.03, 0.03, 0.035),
        masking_coupler_density_rgb=(0.15, 0.52, 0.815),
    )
    exposure = np.full((1, 1, 3), 0.20, dtype=np.float32)
    standard_recipe = DevelopRecipeConfig(program_key="color_reversal")
    experimental_recipe = DevelopRecipeConfig(
        program_key="color_reversal",
        mask_bleach_completion=1.0,
    )

    standard = apply_process_program(
        material,
        material.expose(exposure),
        program_from_develop_recipe(standard_recipe, mode="color_reversal"),
    )
    experimental = apply_process_program(
        material,
        material.expose(exposure),
        program_from_develop_recipe(experimental_recipe, mode="color_reversal"),
    )

    assert "bleach_mask_dye" not in [step.action for step in standard.trace]
    assert "bleach_mask_dye" in [step.action for step in experimental.trace]
    assert np.allclose(standard.final_medium.base_density_rgb, material.base_density_rgb)
    assert np.allclose(experimental.final_medium.base_density_rgb, (0.03, 0.03, 0.035))
    assert float(experimental.final_medium.dye.mean()) < float(standard.final_medium.dye.mean())


def test_positive_dye_chroma_falls_off_at_both_density_endpoints():
    film = FilmStockConfig(
        positive_dye_saturation=1.2,
        positive_highlight_chroma_retention=0.10,
        positive_shadow_chroma_retention=0.15,
        positive_highlight_shoulder_width=0.25,
        positive_shadow_toe_width=0.25,
    )
    dye = np.asarray([[[0.04, 0.02, 0.00], [0.62, 0.50, 0.38], [1.00, 0.98, 0.96]]], dtype=np.float32)
    shaped = shape_positive_dye_chroma(dye, film)
    chroma = np.std(shaped, axis=-1)[0]

    assert chroma[1] > chroma[0] * 10.0
    assert chroma[1] > chroma[2] * 8.0


def test_positive_density_contrast_preserves_shadow_order_without_broad_dmax_clip():
    film = FilmStockConfig(
        positive_density_contrast=1.30,
        positive_density_bias=0.03,
        positive_latitude_compression=0.32,
        positive_midtone_density=0.24,
        positive_shadow_toe=0.18,
        positive_shadow_toe_width=0.22,
        positive_highlight_shoulder=0.32,
        positive_highlight_shoulder_width=0.20,
    )
    dense_fractions = np.linspace(0.70, 1.0, 31, dtype=np.float32).reshape(1, 31, 1)

    shaped = shape_positive_density_fraction(dense_fractions, film).reshape(-1)

    assert np.all(np.diff(shaped) > 0.0)
    assert shaped[-1] < 0.99


def _bw_material() -> ReducedFilmMaterial:
    return ReducedFilmMaterial(
        key="test_bw_film",
        layer_count=1,
        halide_capacity=(1.0,),
        silver_density_per_layer=(1.0,),
        residual_halide_density_per_layer=(0.1,),
        base_density_rgb=(0.04, 0.04, 0.045),
        color_system="silver_bw",
    )


def _color_material() -> ReducedFilmMaterial:
    return ReducedFilmMaterial(
        key="test_color_film",
        layer_count=3,
        halide_capacity=(1.0, 1.0, 1.0),
        coupler_capacity=(1.0, 1.0, 1.0),
        dye_absorption_matrix=(
            (1.0, 0.08, 0.03),
            (0.06, 1.0, 0.10),
            (0.02, 0.12, 1.0),
        ),
        silver_density_per_layer=(0.34, 0.33, 0.33),
        base_density_rgb=(0.18, 0.55, 0.85),
        color_system="color_coupler",
    )


def _program(key: str, *steps: FilmProcessStep) -> FilmProcessProgram:
    return FilmProcessProgram(
        key=key,
        steps=steps,
        output_polarity="negative",
    )


def test_process_operators_conserve_material_across_development_and_bleach():
    bw_material = _bw_material()
    bw_latent = bw_material.expose(
        np.asarray([[[0.15], [0.55], [1.10]]], dtype=np.float32)
    )
    bw_before = bw_latent.halide + bw_latent.metallic_silver
    bw_developed = apply_process_program(
        bw_material,
        bw_latent,
        _program(
            "audit_bw_develop",
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, strength=0.63),
        ),
    )
    assert np.allclose(
        bw_developed.state.halide + bw_developed.state.metallic_silver,
        bw_before,
    )

    color_material = _color_material()
    color_latent = color_material.expose(
        np.asarray([[[0.20, 0.50, 0.90]]], dtype=np.float32)
    )
    silver_before = color_latent.halide + color_latent.metallic_silver
    chromogenic_before = color_latent.coupler + color_latent.dye
    color_developed = apply_process_program(
        color_material,
        color_latent,
        _program(
            "audit_color_develop",
            FilmProcessStep(
                FilmProcessAction.DEVELOP_COLOR,
                strength=0.75,
                dye_coupling_ratio=(0.65, 0.80, 1.10),
            ),
        ),
    )
    assert np.allclose(
        color_developed.state.halide + color_developed.state.metallic_silver,
        silver_before,
    )
    assert np.allclose(
        color_developed.state.coupler + color_developed.state.dye,
        chromogenic_before,
    )

    before_bleach = color_developed.state
    bleach_inventory = before_bleach.metallic_silver.copy()
    bleached = apply_process_program(
        color_material,
        before_bleach,
        _program(
            "audit_bleach",
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=0.58),
        ),
    )
    assert np.allclose(
        bleached.state.metallic_silver + bleached.state.bleached_halide,
        bleach_inventory,
    )


def test_complete_first_bleach_transfers_owned_silver_pool_exactly():
    material = _color_material()
    state = material.expose(
        np.linspace(0.05, 1.2, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    )
    developed = apply_process_program(
        material,
        state,
        _program(
            "develop_only_for_transfer",
            FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=0.72),
        ),
        consume_latent_state=True,
    )
    silver_pool = developed.state.metallic_silver
    expected = silver_pool.copy()

    bleached = apply_process_program(
        material,
        developed.state,
        _program(
            "complete_bleach_transfer",
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=1.0),
        ),
        consume_latent_state=True,
    )

    assert bleached.state.bleached_halide is silver_pool
    assert np.array_equal(bleached.state.bleached_halide, expected)
    assert np.array_equal(bleached.state.metallic_silver, np.zeros_like(expected))
    assert bleached.trace[0].reacted_amount == float(
        np.sum(expected, dtype=np.float64)
    )


def test_complete_fix_uses_one_exact_combined_removal_pool():
    material = _color_material()
    latent = material.expose(
        np.linspace(0.05, 1.2, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    )
    prepared = apply_process_program(
        material,
        latent,
        _program(
            "prepare_complete_fix",
            FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=0.72),
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=0.65),
        ),
        consume_latent_state=True,
    ).state
    expected_removed = np.add(
        prepared.halide,
        prepared.bleached_halide,
        dtype=np.float32,
    )

    fixed = apply_process_program(
        material,
        prepared,
        _program(
            "complete_fix",
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, strength=1.0),
        ),
        consume_latent_state=True,
    )

    assert np.array_equal(fixed.state.halide, np.zeros_like(fixed.state.halide))
    assert fixed.state.bleached_halide is not None
    assert np.array_equal(
        fixed.state.bleached_halide,
        np.zeros_like(fixed.state.bleached_halide),
    )
    assert fixed.trace[0].reacted_amount == float(
        np.sum(expected_removed, dtype=np.float64)
    )


def test_fix_scope_and_empty_pool_reversal_cannot_create_material():
    material = _color_material()
    latent = material.expose(np.asarray([[[0.2, 0.5, 0.9]]], dtype=np.float32))
    developed_and_bleached = apply_process_program(
        material,
        latent,
        _program(
            "audit_prepare_fix",
            FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=0.8),
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=0.5),
        ),
    ).state
    silver_before = developed_and_bleached.metallic_silver.copy()
    dye_before = developed_and_bleached.dye.copy()
    fixable_before = (
        developed_and_bleached.halide + developed_and_bleached.bleached_halide
    )
    fixed = apply_process_program(
        material,
        developed_and_bleached,
        _program(
            "audit_partial_fix",
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, strength=0.6),
        ),
    ).state
    assert np.array_equal(fixed.metallic_silver, silver_before)
    assert np.array_equal(fixed.dye, dye_before)
    assert np.allclose(
        fixed.halide + fixed.bleached_halide,
        fixable_before * np.float32(0.4),
    )

    empty = apply_process_program(
        material,
        latent,
        _program(
            "audit_empty_halide",
            FilmProcessStep(FilmProcessAction.FIX_HALIDE, strength=1.0),
        ),
    ).state
    attempted_reversal = apply_process_program(
        material,
        empty,
        _program(
            "audit_no_creation",
            FilmProcessStep(FilmProcessAction.ACTIVATE_REMAINING_HALIDE, strength=1.0),
            FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=1.0),
        ),
    ).state
    assert np.allclose(attempted_reversal.halide, 0.0)
    assert np.allclose(attempted_reversal.metallic_silver, 0.0)
    assert np.allclose(attempted_reversal.dye, 0.0)
    assert np.array_equal(attempted_reversal.coupler, empty.coupler)


def test_process_step_order_changes_only_available_reaction_path():
    material = _bw_material()
    latent = material.expose(np.asarray([[[0.25], [0.95]]], dtype=np.float32))
    develop_then_bleach = apply_process_program(
        material,
        latent,
        _program(
            "audit_develop_then_bleach",
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, strength=1.0),
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=1.0),
        ),
    )
    bleach_then_develop = apply_process_program(
        material,
        latent,
        _program(
            "audit_bleach_then_develop",
            FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=1.0),
            FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, strength=1.0),
        ),
    )

    assert np.allclose(develop_then_bleach.state.metallic_silver, 0.0)
    assert float(develop_then_bleach.state.bleached_halide.sum()) > 0.0
    assert np.allclose(bleach_then_develop.state.bleached_halide, 0.0)
    assert float(bleach_then_develop.state.metallic_silver.sum()) > 0.0


def test_bw_negative_and_reversal_emerge_from_operator_order():
    material = _bw_material()
    exposure = np.asarray([[[0.02], [1.20]]], dtype=np.float32)
    latent = material.expose(exposure)

    assert np.allclose(
        latent.developable_halide() + latent.inactive_halide(),
        latent.halide,
    )

    negative = apply_process_program(material, latent, black_and_white_negative_program())
    reversal = apply_process_program(material, latent, black_and_white_reversal_program())

    negative_density = negative.final_medium.optical_density_rgb()[0, :, 0]
    reversal_density = reversal.final_medium.optical_density_rgb()[0, :, 0]
    assert negative_density[1] > negative_density[0]
    assert reversal_density[1] < reversal_density[0]
    assert negative.final_medium.image_polarity == "negative"
    assert reversal.final_medium.image_polarity == "positive"
    assert latent.metallic_silver.sum() == 0.0
    assert latent.halide.sum() == 2.0


def test_development_consumes_the_live_activated_halide_fraction():
    material = _bw_material()
    latent = material.expose(np.asarray([[[0.18]]], dtype=np.float32))
    assert np.allclose(latent.developability, 0.5)
    twice = _program(
        "repeat_development",
        FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, label="first"),
        FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, label="second"),
    )

    result = apply_process_program(material, latent, twice)

    assert np.allclose(result.state.halide, 0.5)
    assert np.allclose(result.state.metallic_silver, 0.5)
    assert np.allclose(result.state.developability, 0.0)
    assert np.allclose(result.state.developable_halide(), 0.0)
    assert result.trace[0].reacted_amount == 0.5
    assert result.trace[1].reacted_amount == 0.0


def test_partial_development_and_reversal_activation_preserve_pool_meaning():
    material = _bw_material()
    latent = material.expose(np.asarray([[[0.18]]], dtype=np.float32))
    program = _program(
        "partial_reversal_audit",
        FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, strength=0.5, label="first"),
        FilmProcessStep(
            FilmProcessAction.ACTIVATE_REMAINING_HALIDE,
            strength=0.5,
            label="activation",
        ),
        FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, strength=1.0, label="second"),
    )

    result = apply_process_program(material, latent, program)

    # First development: H=0.75, H^E=0.25, e=1/3.  Half activation changes
    # e to 2/3; the second development consumes exactly H^E=0.5.
    assert np.allclose(result.state.halide, 0.25)
    assert np.allclose(result.state.metallic_silver, 0.75)
    assert np.allclose(result.state.developability, 0.0)
    assert np.isclose(result.trace[0].reacted_amount, 0.25)
    assert np.isclose(result.trace[1].reacted_amount, 0.25)
    assert np.isclose(result.trace[2].reacted_amount, 0.50)
    assert np.allclose(result.state.halide + result.state.metallic_silver, 1.0)


def test_activation_audit_buffer_reuse_preserves_state_and_exact_report():
    rng = np.random.default_rng(20260730)
    shape = (9, 13, 3)
    halide = rng.uniform(0.05, 1.0, shape).astype(np.float32)
    developability = rng.uniform(0.0, 1.0, shape).astype(np.float32)
    latent = FilmProcessState(
        halide=halide.copy(),
        developability=developability.copy(),
        metallic_silver=np.zeros(shape, dtype=np.float32),
    )
    strength = 0.63
    delta = np.subtract(1.0, developability, dtype=np.float32)
    rate = np.clip(
        float(strength)
        * 1.0
        * np.asarray((0.81, 0.94, 0.72), dtype=np.float32),
        0.0,
        1.0,
    ).reshape(1, 1, 3)
    delta *= rate
    expected_developability = developability + delta
    np.clip(
        expected_developability,
        0.0,
        1.0,
        out=expected_developability,
    )
    expected_reacted = float(np.sum(delta * halide, dtype=np.float64))
    material = ReducedFilmMaterial(
        key="activation_audit",
        layer_count=3,
        halide_capacity=(1.0, 1.0, 1.0),
    )
    program = _program(
        "activation_audit",
        FilmProcessStep(
            FilmProcessAction.ACTIVATE_REMAINING_HALIDE,
            strength=strength,
            layer_selectivity=(0.81, 0.94, 0.72),
            label="activation",
        ),
    )

    result = apply_process_program(material, latent, program)

    assert np.array_equal(result.state.halide, halide)
    assert np.array_equal(result.state.developability, expected_developability)
    assert result.trace[0].reacted_amount == expected_reacted


def test_internal_state_consumption_matches_public_non_mutating_program():
    material = _color_material()
    exposure = np.asarray([[[0.2, 0.5, 1.0], [0.8, 0.1, 0.4]]], dtype=np.float32)
    public_state = material.expose(exposure)
    consumed_state = public_state.copy()
    public_halide_before = public_state.halide.copy()
    program = color_negative_program()

    public_result = apply_process_program(material, public_state, program)
    consumed_result = apply_process_program(
        material,
        consumed_state,
        program,
        consume_latent_state=True,
    )

    assert np.array_equal(public_state.halide, public_halide_before)
    assert consumed_result.state is consumed_state
    assert not np.array_equal(consumed_state.halide, public_halide_before)
    assert np.array_equal(
        public_result.final_medium.metallic_silver,
        consumed_result.final_medium.metallic_silver,
    )
    assert np.array_equal(public_result.final_medium.dye, consumed_result.final_medium.dye)
    assert public_result.trace == consumed_result.trace


def test_internal_boundary_validation_matches_per_step_program_exactly():
    material = _color_material()
    exposure = np.linspace(0.0, 1.0, 9 * 13 * 3, dtype=np.float32).reshape(9, 13, 3)
    program = color_negative_program()

    checked = apply_process_program(material, material.expose(exposure), program)
    boundary_checked = apply_process_program(
        material,
        material.expose(exposure),
        program,
        validate_each_step=False,
    )

    assert np.array_equal(checked.state.halide, boundary_checked.state.halide)
    assert np.array_equal(
        checked.state.developability,
        boundary_checked.state.developability,
    )
    assert np.array_equal(
        checked.state.metallic_silver,
        boundary_checked.state.metallic_silver,
    )
    assert np.array_equal(checked.state.coupler, boundary_checked.state.coupler)
    assert np.array_equal(checked.state.dye, boundary_checked.state.dye)
    assert checked.trace == boundary_checked.trace


def test_internal_boundary_validation_still_rejects_invalid_terminal_state():
    material = _color_material()
    invalid_program = FilmProcessProgram(
        key="invalid_strength",
        output_polarity="negative",
        steps=(
            FilmProcessStep(
                FilmProcessAction.DEVELOP_SILVER,
                strength=float("nan"),
            ),
        ),
    )

    with pytest.raises(ValueError, match="contains invalid pool values"):
        apply_process_program(
            material,
            material.expose(np.full((2, 3, 3), 0.5, dtype=np.float32)),
            invalid_program,
            validate_each_step=False,
        )


def test_internal_exposure_consumption_matches_public_material_curve():
    material = _color_material()
    exposure = np.linspace(-0.1, 1.4, 7 * 11 * 3, dtype=np.float32).reshape(7, 11, 3)
    original = exposure.copy()

    public_state = material.expose(
        exposure,
        preserve_original_developability=False,
    )
    private_input = exposure.copy()
    consumed_state = material.expose(
        private_input,
        preserve_original_developability=False,
        consume_layer_exposure=True,
    )

    assert np.array_equal(public_state.developability, consumed_state.developability)
    assert np.array_equal(public_state.halide, consumed_state.halide)
    assert np.array_equal(exposure, original)
    assert np.shares_memory(consumed_state.developability, private_input)


def test_internal_final_pool_ownership_transfer_is_explicit_and_read_only():
    material = _color_material()
    exposure = np.asarray([[[0.2, 0.5, 1.0], [0.8, 0.1, 0.4]]], dtype=np.float32)
    state = material.expose(exposure)

    with pytest.raises(ValueError, match="requires consume_latent_state"):
        apply_process_program(
            material,
            state,
            color_negative_program(),
            transfer_final_pool_ownership=True,
        )

    state = material.expose(exposure)
    result = apply_process_program(
        material,
        state,
        color_negative_program(),
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
    )

    assert result.state is state
    assert np.shares_memory(result.final_medium.metallic_silver, state.metallic_silver)
    assert np.shares_memory(result.final_medium.residual_halide, state.halide)
    assert result.final_medium.dye is not None
    assert state.dye is not None
    assert np.shares_memory(result.final_medium.dye, state.dye)
    assert not state.metallic_silver.flags.writeable
    assert not state.halide.flags.writeable
    assert not state.dye.flags.writeable


@pytest.mark.parametrize(
    ("pool_name", "invalid_value"),
    (
        ("halide", np.nan),
        ("metallic_silver", np.inf),
        ("coupler", -0.01),
        ("developability", 1.1),
    ),
)
def test_material_state_scalar_reduction_validation_keeps_full_range_contract(
    pool_name,
    invalid_value,
):
    material = _color_material()
    state = material.expose(np.full((2, 3, 3), 0.4, dtype=np.float32))
    pool = getattr(state, pool_name)
    assert pool is not None
    pool[0, 0, 0] = invalid_value

    with pytest.raises(ValueError, match="invalid pool values"):
        state.validate()


def test_color_negative_finishes_as_dye_and_bleach_bypass_retains_silver():
    material = _color_material()
    exposure = np.asarray([[[0.2, 0.5, 1.0]]], dtype=np.float32)
    latent = material.expose(exposure)

    normal = apply_process_program(material, latent, color_negative_program())
    bypass = apply_process_program(material, latent, color_negative_program(bleach_strength=0.0))

    assert normal.final_medium.dye is not None
    assert float(normal.final_medium.dye.sum()) > 0.0
    assert np.allclose(normal.final_medium.metallic_silver, 0.0)
    assert float(bypass.final_medium.metallic_silver.sum()) > 0.0
    assert np.all(
        bypass.final_medium.optical_density_rgb()
        >= normal.final_medium.optical_density_rgb()
    )


def test_bleached_silver_salt_and_original_halide_remain_distinct_until_fixing():
    material = _color_material()
    latent = material.expose(np.asarray([[[0.25, 0.50, 0.85]]], dtype=np.float32))
    fixed_program = program_from_develop_recipe(
        DevelopRecipeConfig(program_key="color_negative"),
        layer_count=3,
    )
    retained_program = program_from_develop_recipe(
        DevelopRecipeConfig(
            program_key="color_negative",
            halide_fixing_completion=0.0,
        ),
        layer_count=3,
    )

    fixed = apply_process_program(material, latent, fixed_program)
    retained = apply_process_program(material, latent, retained_program)

    assert retained.final_medium.bleached_halide is not None
    assert float(retained.final_medium.bleached_halide.sum()) > 0.0
    assert float(retained.final_medium.residual_halide.sum()) > 0.0
    assert np.allclose(
        retained.final_medium.total_fixable_halide(),
        retained.final_medium.residual_halide + retained.final_medium.bleached_halide,
    )
    assert np.array_equal(
        retained.final_medium.mean_total_fixable_halide(),
        np.mean(
            retained.final_medium.total_fixable_halide(),
            axis=-1,
            dtype=np.float32,
        ),
    )
    assert fixed.final_medium.bleached_halide is not None
    assert np.allclose(fixed.final_medium.bleached_halide, 0.0)
    assert np.allclose(fixed.final_medium.residual_halide, 0.0)
    # Retained silver salts use a reduced blue-density weighting, consistent
    # with the yellow D-min / increased blue density reported for poor fixing.
    added_density = (
        retained.final_medium.optical_density_rgb()
        - fixed.final_medium.optical_density_rgb()
    )[0, 0]
    assert added_density[2] > added_density[1] > added_density[0] > 0.0

    components = retained.final_medium.contract()["components"]
    assert components["residual_halide"] is True
    assert components["bleached_halide"] is True


def test_residual_and_bleached_halide_have_independent_optical_coefficients():
    shared = _color_material()
    distinct = replace(
        shared,
        bleached_halide_density_per_layer=(0.20, 0.20, 0.20),
        bleached_halide_density_rgb=(1.0, 0.15, 0.10),
    )
    exposure = np.asarray([[[0.25, 0.50, 0.85]]], dtype=np.float32)
    retain_salts = _program(
        "retain_both_salt_pools",
        FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=0.8),
        FilmProcessStep(FilmProcessAction.BLEACH_SILVER, strength=0.7),
    )

    shared_result = apply_process_program(shared, shared.expose(exposure), retain_salts)
    distinct_result = apply_process_program(distinct, distinct.expose(exposure), retain_salts)

    assert np.array_equal(
        shared_result.final_medium.residual_halide,
        distinct_result.final_medium.residual_halide,
    )
    assert np.array_equal(
        shared_result.final_medium.bleached_halide,
        distinct_result.final_medium.bleached_halide,
    )
    assert not np.allclose(
        shared_result.final_medium.optical_density_rgb(),
        distinct_result.final_medium.optical_density_rgb(),
    )
    optics = distinct_result.final_medium.contract()["optical_observation"]
    assert optics["bleached_halide_density_rgb"] == [1.0, 0.15, 0.1]

def test_complete_fix_removes_bleached_salt_but_not_unbleached_metallic_silver():
    material = _color_material()
    latent = material.expose(np.asarray([[[0.25, 0.50, 0.85]]], dtype=np.float32))
    program = program_from_develop_recipe(
        DevelopRecipeConfig(
            program_key="color_negative_bleach_bypass",
            silver_bleach_completion=0.4,
            halide_fixing_completion=1.0,
        ),
        layer_count=3,
    )

    result = apply_process_program(material, latent, program)

    assert float(result.final_medium.metallic_silver.sum()) > 0.0
    assert result.final_medium.bleached_halide is not None
    assert np.allclose(result.final_medium.bleached_halide, 0.0)
    assert np.allclose(result.final_medium.residual_halide, 0.0)


def test_color_reversal_uses_remaining_halide_for_positive_dye_image():
    material = _color_material()
    dark_and_bright = np.asarray(
        [[
            [0.02, 0.02, 0.02],
            [1.20, 1.20, 1.20],
        ]],
        dtype=np.float32,
    )
    result = apply_process_program(
        material,
        material.expose(dark_and_bright),
        color_reversal_program(),
    )

    density = result.final_medium.optical_density_rgb().mean(axis=-1)[0]
    assert density[0] > density[1]
    assert result.final_medium.contract()["compatible_interpreters"] == [
        "positive_transparency_scan"
    ]
    assert np.allclose(result.final_medium.metallic_silver, 0.0)
    assert [step.action for step in result.trace] == [
        "develop_silver",
        "activate_remaining_halide",
        "develop_color",
        "bleach_silver",
        "fix_halide",
        "destroy_dye",
        "remove_auxiliary",
    ]


def test_material_process_compatibility_changes_reaction_not_program_topology():
    material = _color_material()
    latent = material.expose(np.full((2, 2, 3), 0.5, dtype=np.float32))
    program = color_negative_program()

    compatible = apply_process_program(material, latent, program)
    mismatch = apply_process_program(
        material,
        latent,
        program,
        CompatibilityProfile(
            dye_coupling=0.25,
            dye_stability=0.60,
            auxiliary_removal=0.25,
            layer_balance=(1.0, 0.7, 0.4),
        ),
    )

    assert [step.action for step in compatible.trace] == [step.action for step in mismatch.trace]
    assert float(mismatch.final_medium.dye.sum()) < float(compatible.final_medium.dye.sum())
    assert mismatch.final_medium.auxiliary_remaining > compatible.final_medium.auxiliary_remaining
    assert not np.allclose(
        mismatch.final_medium.optical_density_rgb(),
        compatible.final_medium.optical_density_rgb(),
    )


def test_explicit_dye_damage_is_a_distinct_material_pool_operator():
    material = _color_material()
    latent = material.expose(np.full((2, 2, 3), 0.5, dtype=np.float32))
    clean_program = program_from_develop_recipe(
        DevelopRecipeConfig(program_key="color_negative"),
        layer_count=3,
    )
    damaged_program = program_from_develop_recipe(
        DevelopRecipeConfig(
            program_key="color_negative",
            dye_damage_strength=0.35,
        ),
        layer_count=3,
    )

    clean = apply_process_program(material, latent, clean_program)
    damaged = apply_process_program(material, latent, damaged_program)
    damage_reports = [
        report
        for report in damaged.trace
        if report.action == FilmProcessAction.DESTROY_DYE.value
    ]

    assert not any(
        step.action == FilmProcessAction.DESTROY_DYE
        and step.dye_destruction_basis == "explicit"
        for step in clean_program.steps
    )
    assert len(damaged_program.steps) == len(clean_program.steps) + 1
    assert [report.dye_destruction_basis for report in damage_reports] == [
        "compatibility",
        "explicit",
    ]
    assert float(damaged.final_medium.dye.sum()) < float(clean.final_medium.dye.sum())
    assert np.array_equal(
        damaged.final_medium.metallic_silver,
        clean.final_medium.metallic_silver,
    )
    assert np.array_equal(
        damaged.final_medium.residual_halide,
        clean.final_medium.residual_halide,
    )
    assert np.array_equal(
        damaged.final_medium.bleached_halide,
        clean.final_medium.bleached_halide,
    )
    assert damaged.final_medium.base_density_rgb == clean.final_medium.base_density_rgb


@pytest.mark.parametrize(
    "program_key",
    ("color_negative", "color_negative_bleach_bypass", "color_reversal"),
)
def test_explicit_dye_damage_is_available_to_all_unified_color_programs(program_key):
    program = program_from_develop_recipe(
        DevelopRecipeConfig(
            program_key=program_key,
            dye_damage_strength=0.2,
        ),
        layer_count=3,
    )
    explicit_index = next(
        index
        for index, step in enumerate(program.steps)
        if step.action == FilmProcessAction.DESTROY_DYE
        and step.dye_destruction_basis == "explicit"
    )
    auxiliary_index = next(
        index
        for index, step in enumerate(program.steps)
        if step.action == FilmProcessAction.REMOVE_AUXILIARY
    )

    assert explicit_index == auxiliary_index - 1


def test_explicit_and_compatibility_dye_losses_compose_on_remaining_dye():
    material = _color_material()
    latent = material.expose(np.full((1, 2, 3), 0.5, dtype=np.float32))
    compatibility = CompatibilityProfile(dye_stability=0.60)
    compatible_only = apply_process_program(
        material,
        latent,
        program_from_develop_recipe(
            DevelopRecipeConfig(program_key="color_negative"),
            layer_count=3,
        ),
        compatibility,
    )
    combined = apply_process_program(
        material,
        latent,
        program_from_develop_recipe(
            DevelopRecipeConfig(
                program_key="color_negative",
                dye_damage_strength=0.25,
            ),
            layer_count=3,
        ),
        compatibility,
    )

    assert np.allclose(
        combined.final_medium.dye,
        compatible_only.final_medium.dye * np.float32(0.75),
        atol=2e-7,
        rtol=0.0,
    )


def test_explicit_dye_damage_is_inert_without_a_dye_pool_and_rejects_unknown_basis():
    material = _bw_material()
    latent = material.expose(np.asarray([[[0.5]]], dtype=np.float32))
    explicit = _program(
        "bw_explicit_dye_damage",
        FilmProcessStep(
            FilmProcessAction.DESTROY_DYE,
            strength=0.8,
            dye_destruction_basis="explicit",
        ),
    )

    result = apply_process_program(material, latent, explicit)

    assert result.trace[0].reacted_amount == 0.0
    assert result.final_medium.dye is None
    assert np.array_equal(result.state.halide, latent.halide)
    assert np.array_equal(result.state.metallic_silver, latent.metallic_silver)

    color = _color_material()
    color_latent = color.expose(np.full((1, 1, 3), 0.5, dtype=np.float32))
    invalid = _program(
        "invalid_dye_damage_basis",
        FilmProcessStep(FilmProcessAction.DEVELOP_COLOR),
        FilmProcessStep(
            FilmProcessAction.DESTROY_DYE,
            strength=0.5,
            dye_destruction_basis="label_magic",
        ),
    )
    with pytest.raises(ValueError, match="unsupported dye destruction basis"):
        apply_process_program(color, color_latent, invalid)


def test_rate_compatibility_is_an_efficiency_not_an_overdevelopment_multiplier():
    material = _bw_material()
    latent = material.expose(np.asarray([[[0.18]]], dtype=np.float32))
    program = _program(
        "compatibility_efficiency",
        FilmProcessStep(FilmProcessAction.DEVELOP_SILVER, strength=0.60),
    )

    standard = apply_process_program(
        material,
        latent,
        program,
        CompatibilityProfile(silver_development=1.0),
    )
    oversized = apply_process_program(
        material,
        latent,
        program,
        CompatibilityProfile(silver_development=2.0),
    )

    assert np.array_equal(oversized.state.metallic_silver, standard.state.metallic_silver)
    assert np.array_equal(oversized.state.halide, standard.state.halide)


def test_program_and_cross_process_layer_balances_are_distinct_multipliers():
    material = _color_material()
    latent = material.expose(np.full((1, 1, 3), 0.18, dtype=np.float32))
    program = _program(
        "layer_balance_contract",
        FilmProcessStep(
            FilmProcessAction.DEVELOP_COLOR,
            strength=0.5,
            layer_selectivity=(1.0, 0.8, 0.6),
        ),
    )
    result = apply_process_program(
        material,
        latent,
        program,
        CompatibilityProfile(layer_balance=(1.0, 0.5, 0.25)),
    )

    # At e=0.5 and below the rate ceiling, developed silver is proportional
    # to program selectivity times the additional material/process mismatch.
    silver = result.state.metallic_silver[0, 0]
    assert np.allclose(silver / silver[0], (1.0, 0.4, 0.15), atol=1e-6)


def test_dye_coupling_ratio_may_exceed_one_but_remains_capacity_bounded():
    material = _color_material()
    latent = material.expose(np.full((1, 1, 3), 0.18, dtype=np.float32))
    program = _program(
        "dye_coupling_contract",
        FilmProcessStep(FilmProcessAction.DEVELOP_COLOR, strength=0.4),
    )

    standard = apply_process_program(
        material,
        latent,
        program,
        CompatibilityProfile(dye_coupling=1.0),
    )
    enhanced = apply_process_program(
        material,
        latent,
        program,
        CompatibilityProfile(dye_coupling=1.5),
    )

    assert np.all(enhanced.state.dye > standard.state.dye)
    assert np.all(enhanced.state.dye <= 1.0)


def test_legacy_density_adapter_preserves_existing_scanner_density_math():
    film = FilmStockConfig(base_dye_interaction_strength=0.0)
    density = np.linspace(0.05, 1.5, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32)
    expected = np.einsum("...l,rl->...r", density, absorption)
    expected += np.asarray(film.film_base_density_rgb, dtype=np.float32)

    medium = final_medium_from_legacy_density(density, film)
    assert np.allclose(medium.optical_density_rgb(), expected)
    assert np.allclose(negative_total_density_rgb(density, film), expected)


def test_current_engine_exports_portable_final_medium_contract():
    from film_foundry.core.engine import develop_negative
    from film_foundry.core.states import developed_medium_metadata
    from film_foundry.model.config import DarkroomConfig

    config = DarkroomConfig()
    config.chemistry.program_key = "legacy_density"
    config.enable_grain = False
    medium = develop_negative(np.full((3, 4, 3), 0.4, dtype=np.float32), config)
    payload = developed_medium_metadata(medium)

    model = payload["film_process_model"]
    assert model["representation"] == "legacy_density_adapter"
    assert model["image_polarity"] == "negative"
    assert model["compatible_interpreters"] == ["negative_scan"]
    assert model["components"]["dye"] is True
    assert model["process_program"]["key"] == "legacy_density"
    assert model["process_program"]["steps"] == []
    semantics = payload["array_semantics"]
    assert payload["array_semantics_version"] == 1
    assert semantics["density_cmy"]["role"] == "legacy_formed_layer_density"
    assert semantics["density_grain"]["role"] == "legacy_composite_layer_density"
    assert semantics["density_grain"]["is_pure_grain_delta"] is False
    assert semantics["optical_density_rgb"]["authoritative_for_scan"] is (
        medium.optical_density_rgb is not None
    )
    assert medium.legacy_formed_layer_density is medium.density_cmy
    assert medium.legacy_composite_layer_density is medium.density_grain


def test_auto_program_uses_authoritative_pool_path_by_default():
    from film_foundry.core.engine import develop_negative
    from film_foundry.model.config import DarkroomConfig

    config = DarkroomConfig()
    config.enable_grain = False
    medium = develop_negative(np.full((3, 4, 3), 0.4, dtype=np.float32), config)
    model = medium.metadata["film_process_model"]

    assert config.chemistry.program_key == "auto"
    assert model["representation"] == "reduced_color_coupler_pool_v1"
    assert model["process_program"]["key"] == "color_negative"
    assert model["process_trace"]


def test_monochrome_stock_color_program_keeps_zero_coupler_capacity():
    from film_foundry.core.film_process.integration import (
        _color_material,
        develop_color_negative_reduced,
    )

    film = FilmStockConfig()
    film.color_process = "monochrome"
    material = _color_material(film)
    assert material.layer_count == 1
    assert material.coupler_capacity == (0.0,)
    assert _color_material(film, 3).coupler_capacity == (0.0, 0.0, 0.0)
    assert material.color_system == "silver_bw"

    image = np.full((4, 5, 3), 0.62, dtype=np.float32)
    standard = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(program_key="color_negative"),
    )
    bypass = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(
            program_key="color_negative_bleach_bypass",
            silver_bleach_completion=0.35,
        ),
    )
    dye = standard.process_result.final_medium.dye
    assert dye is not None
    assert float(np.max(dye)) < 1e-6
    assert standard.process_result.final_medium.contract()["components"]["dye"] is False
    assert float(np.max(bypass.process_result.final_medium.metallic_silver)) > float(
        np.max(standard.process_result.final_medium.metallic_silver)
    )
    from film_foundry.core.engine import develop_negative
    from film_foundry.model.config import DarkroomConfig

    config = DarkroomConfig()
    config.enable_grain = False
    medium = develop_negative(np.full((3, 4, 3), 0.4, dtype=np.float32), config)
    model = medium.metadata["film_process_model"]

    assert config.chemistry.program_key == "auto"
    assert model["representation"] == "reduced_color_coupler_pool_v1"
    assert model["process_program"]["key"] == "color_negative"
    assert model["process_trace"]


def test_process_pools_remain_nonnegative_after_partial_cross_process():
    material = _color_material()
    latent = material.expose(np.linspace(0.0, 1.0, 18, dtype=np.float32).reshape(2, 3, 3))
    result = apply_process_program(
        material,
        latent,
        color_negative_program(bleach_strength=0.45),
        CompatibilityProfile(
            silver_development=0.72,
            dye_coupling=0.63,
            silver_bleach=0.58,
            halide_fixing=0.84,
            layer_balance=(1.0, 0.82, 0.61),
        ),
    )

    result.state.validate()
    for total in result.state.totals().values():
        assert total >= 0.0
    assert np.all(np.isfinite(result.final_medium.optical_density_rgb()))


def test_develop_recipe_maps_to_executable_negative_and_reversal_programs():
    from film_foundry.model.config import DevelopRecipeConfig

    negative_recipe = DevelopRecipeConfig(program_key="color_negative_bleach_bypass")
    negative_recipe.silver_bleach_completion = 0.35
    negative_program = program_from_develop_recipe(negative_recipe)
    bleach_step = next(
        step for step in negative_program.steps if step.action.value == "bleach_silver"
    )
    assert negative_program.key == "color_negative_bleach_bypass"
    assert bleach_step.strength == 0.35

    reversal_recipe = DevelopRecipeConfig(program_key="bw_reversal")
    reversal_recipe.reversal_activation = 0.72
    reversal_recipe.first_development_completion = 0.40
    reversal_recipe.second_development_completion = 0.85
    reversal_program = program_from_develop_recipe(
        reversal_recipe,
        mode="bw_negative",
        material_process="reversal",
        layer_count=1,
    )
    activation = next(
        step for step in reversal_program.steps if step.action.value == "activate_remaining_halide"
    )
    assert reversal_program.output_polarity == "positive"
    assert activation.strength == 0.72
    first_development = next(step for step in reversal_program.steps if step.label == "first_development")
    second_development = next(step for step in reversal_program.steps if step.label == "second_development")
    assert first_development.strength == 0.40
    assert second_development.strength == 0.85
    assert all(step.layer_selectivity in {None, (1.0,)} for step in reversal_program.steps)


def test_partial_retained_silver_resolves_color_negative_as_bleach_bypass():
    recipe = DevelopRecipeConfig(
        program_key="color_negative",
        silver_retention=0.35,
    )
    program = program_from_develop_recipe(recipe)
    bleach = next(step for step in program.steps if step.action.value == "bleach_silver")

    assert program.key == "color_negative_bleach_bypass"
    assert bleach.strength == 0.65


def test_bleach_retention_changes_image_silver_pool_but_surface_plating_does_not():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    image = np.full((4, 5, 3), 0.55, dtype=np.float32)
    film = FilmStockConfig()
    clean = develop_color_negative_reduced(image, film, DevelopRecipeConfig())
    retained = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(silver_retention=0.8),
    )
    plated = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(silver_plating=0.8),
    )

    assert float(retained.process_result.final_medium.metallic_silver.sum()) > 0.0
    assert np.array_equal(
        plated.process_result.final_medium.metallic_silver,
        clean.process_result.final_medium.metallic_silver,
    )


def test_material_degradation_reduces_latent_speed_and_adds_stock_specific_fog():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    image = np.full((4, 5, 3), 0.35, dtype=np.float32)
    fresh_film = FilmStockConfig(material_degradation=0.0)
    aged_film = FilmStockConfig(
        material_degradation=1.0,
        degradation_speed_loss_stops=1.0,
        degradation_fog_density_rgb=(0.18, 0.12, 0.08),
        degradation_layer_balance=(0.70, 0.90, 1.00),
    )
    recipe = DevelopRecipeConfig()
    fresh = develop_color_negative_reduced(image, fresh_film, recipe)
    aged = develop_color_negative_reduced(image, aged_film, recipe)

    assert float(np.mean(aged.latent_fraction)) < float(np.mean(fresh.latent_fraction))
    assert not np.allclose(aged.latent_fraction[..., 0], aged.latent_fraction[..., 2])

    black = np.zeros_like(image)
    fresh_black = develop_color_negative_reduced(black, fresh_film, recipe)
    aged_black = develop_color_negative_reduced(black, aged_film, recipe)
    assert float(np.mean(aged_black.density_cmy)) > float(np.mean(fresh_black.density_cmy))


def test_authoritative_pool_path_freezes_latent_state_before_process_kinetics():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    ramp = np.geomspace(0.01, 2.0, 64, dtype=np.float32)
    image = np.repeat(ramp[None, :, None], 3, axis=2)
    film = FilmStockConfig()

    normal = develop_color_negative_reduced(image, film, DevelopRecipeConfig())
    compensated = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(compensation=1.0),
    )
    pull = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(push_stops=-1.0),
    )
    push = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(push_stops=1.0),
    )
    local_rate = np.linspace(0.72, 1.18, image.shape[1], dtype=np.float32)[None, :]
    uneven = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(),
        local_development_rate=local_rate,
    )

    assert np.array_equal(compensated.latent_fraction, normal.latent_fraction)
    assert np.array_equal(pull.latent_fraction, normal.latent_fraction)
    assert np.array_equal(push.latent_fraction, normal.latent_fraction)
    assert np.array_equal(uneven.latent_fraction, normal.latent_fraction)
    assert not np.allclose(
        uneven.process_result.final_medium.dye,
        normal.process_result.final_medium.dye,
    )
    assert compensated.process_result.trace[0].highlight_compensation > 0.0
    assert push.process_result.trace[0].developability_gamma > 1.0
    assert pull.process_result.trace[0].developability_gamma < 1.0
    assert float(np.mean(compensated.process_result.final_medium.dye[:, -8:])) < float(
        np.mean(normal.process_result.final_medium.dye[:, -8:])
    )
    assert float(pull.process_result.final_medium.dye.mean()) < float(
        normal.process_result.final_medium.dye.mean()
    )
    normal_dye = normal.process_result.final_medium.dye
    push_dye = push.process_result.final_medium.dye
    normal_span = float(np.mean(normal_dye[:, -8:]) - np.mean(normal_dye[:, :8]))
    push_span = float(np.mean(push_dye[:, -8:]) - np.mean(push_dye[:, :8]))
    assert push_span > normal_span


def test_latent_layer_exposure_addition_defaults_to_exact_identity():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    rng = np.random.default_rng(20260719)
    image = rng.uniform(0.01, 1.2, (7, 11, 3)).astype(np.float32)
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig()

    implicit = develop_color_negative_reduced(image, film, recipe)
    explicit = develop_color_negative_reduced(
        image,
        film,
        recipe,
        latent_layer_exposure_addition=None,
    )

    assert np.array_equal(explicit.latent_fraction, implicit.latent_fraction)
    assert np.array_equal(explicit.density_cmy, implicit.density_cmy)
    assert np.array_equal(explicit.optical_density_rgb, implicit.optical_density_rgb)


def test_internal_latent_diagnostic_discard_preserves_formed_medium():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    rng = np.random.default_rng(20260721)
    image = rng.uniform(0.01, 1.2, (9, 13, 3)).astype(np.float32)
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig()

    retained = develop_color_negative_reduced(image, film, recipe)
    discarded = develop_color_negative_reduced(
        image,
        film,
        recipe,
        retain_latent_fraction=False,
    )

    assert retained.latent_fraction.shape == image.shape
    assert discarded.latent_fraction.shape == (0, 0, 3)
    assert not np.shares_memory(
        retained.latent_fraction,
        retained.process_result.state.developability,
    )
    assert np.array_equal(discarded.density_cmy, retained.density_cmy)
    assert np.array_equal(discarded.optical_density_rgb, retained.optical_density_rgb)
    assert discarded.process_result.trace == retained.process_result.trace


def test_private_color_formation_can_release_consumed_state_after_freezing_totals():
    from film_foundry.core.film_process.integration import (
        develop_color_negative_reduced,
    )

    image = np.linspace(0.03, 1.1, 11 * 17 * 3, dtype=np.float32).reshape(11, 17, 3)
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig()
    retained = develop_color_negative_reduced(image, film, recipe)
    compact = develop_color_negative_reduced(
        image,
        film,
        recipe,
        retain_latent_fraction=False,
        retain_process_state=False,
    )

    assert compact.process_totals == retained.process_result.state.totals()
    assert compact.process_result.state.developability.shape == (0, 0, 3)
    assert compact.process_result.state.coupler is None
    assert np.array_equal(compact.density_cmy, retained.density_cmy)
    assert np.array_equal(compact.optical_density_rgb, retained.optical_density_rgb)
    assert compact.process_result.trace == retained.process_result.trace


def test_latent_layer_exposure_addition_targets_material_layers_before_process():
    from film_foundry.core.film_process.integration import (
        develop_color_negative_reduced,
        develop_color_reversal_reduced,
    )

    image = np.full((5, 8, 3), 0.08, dtype=np.float32)
    addition = np.zeros_like(image)
    addition[..., 1] = 0.55
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig()

    baseline = develop_color_negative_reduced(image, film, recipe)
    negative = develop_color_negative_reduced(
        image,
        film,
        recipe,
        latent_layer_exposure_addition=addition,
    )
    reversal = develop_color_reversal_reduced(
        image,
        film,
        recipe,
        latent_layer_exposure_addition=addition,
    )

    assert np.array_equal(negative.latent_fraction[..., 0], baseline.latent_fraction[..., 0])
    assert np.array_equal(negative.latent_fraction[..., 2], baseline.latent_fraction[..., 2])
    assert np.all(negative.latent_fraction[..., 1] > baseline.latent_fraction[..., 1])
    assert np.array_equal(reversal.latent_fraction, negative.latent_fraction)
    assert not np.array_equal(reversal.density_cmy, negative.density_cmy)


def test_latent_layer_exposure_addition_rejects_wrong_or_nonphysical_fields():
    from film_foundry.core.film_process.integration import (
        develop_bw_negative_reduced,
        develop_color_negative_reduced,
    )

    image = np.full((4, 6, 3), 0.2, dtype=np.float32)
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig()
    invalid_fields = (
        np.zeros((4, 6, 1), dtype=np.float32),
        np.full((4, 6, 3), -0.01, dtype=np.float32),
        np.full((4, 6, 3), np.nan, dtype=np.float32),
    )
    for field in invalid_fields:
        with pytest.raises(ValueError, match="latent_layer_exposure_addition"):
            develop_color_negative_reduced(
                image,
                film,
                recipe,
                latent_layer_exposure_addition=field,
            )

    monochrome = FilmStockConfig(color_process="bw")
    one_layer = np.full((4, 6, 1), 0.15, dtype=np.float32)
    accepted = develop_bw_negative_reduced(
        image,
        monochrome,
        recipe,
        latent_layer_exposure_addition=one_layer,
    )
    assert accepted.latent_fraction.shape == (4, 6, 1)
    with pytest.raises(ValueError, match="formed layer exposure shape"):
        develop_bw_negative_reduced(
            image,
            monochrome,
            recipe,
            latent_layer_exposure_addition=np.zeros_like(image),
        )


def test_latent_layer_exposure_addition_is_exact_across_material_tiles():
    from film_foundry.core.engine import _develop_reduced_material_tiled
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    rng = np.random.default_rng(20260720)
    image = rng.uniform(0.01, 1.1, (9, 13, 3)).astype(np.float32)
    addition = rng.uniform(0.0, 0.18, image.shape).astype(np.float32)
    config = DarkroomConfig()

    full = develop_color_negative_reduced(
        image,
        config.film,
        config.chemistry,
        latent_layer_exposure_addition=addition,
    )
    tiled_density, tiled_optical, _, audit = _develop_reduced_material_tiled(
        image,
        config,
        "color_negative",
        3,
        latent_layer_exposure_addition=addition,
    )

    assert np.array_equal(tiled_density, full.density_cmy)
    assert np.array_equal(tiled_optical, full.optical_density_rgb)
    assert audit["execution"]["tile_count"] == 3

    with pytest.raises(ValueError, match="full material layer shape"):
        _develop_reduced_material_tiled(
            image,
            config,
            "color_negative",
            3,
            latent_layer_exposure_addition=addition[:-1],
        )


def test_tiled_material_input_can_become_exact_density_output_when_privately_owned():
    from film_foundry.core.engine import _develop_reduced_material_tiled

    image = np.random.default_rng(20260729).uniform(
        0.01,
        1.1,
        (17, 23, 3),
    ).astype(np.float32)
    config = DarkroomConfig()
    ordinary_density, ordinary_optical, ordinary_base, ordinary_audit = (
        _develop_reduced_material_tiled(
            image,
            config,
            "color_negative",
            5,
        )
    )
    consumed = image.copy()
    consumed_density, consumed_optical, consumed_base, consumed_audit = (
        _develop_reduced_material_tiled(
            consumed,
            config,
            "color_negative",
            5,
            consume_input_as_density=True,
        )
    )

    assert consumed_density is consumed
    assert np.array_equal(consumed_density, ordinary_density)
    assert np.array_equal(consumed_optical, ordinary_optical)
    assert np.array_equal(consumed_base, ordinary_base)
    assert consumed_audit == ordinary_audit


def test_lazy_scalar_layer_exposure_matches_dense_field_for_negative_and_reversal():
    from film_foundry.core.film_process.integration import (
        develop_color_negative_reduced,
        develop_color_reversal_reduced,
    )
    from film_foundry.core.spatial_fields import LayerExposureAdditionField

    rng = np.random.default_rng(20260721)
    image = rng.uniform(0.02, 0.9, (8, 12, 3)).astype(np.float32)
    scalar = rng.uniform(0.0, 0.22, image.shape[:2]).astype(np.float32)
    weights = np.asarray((1.0, 0.35, 0.08), dtype=np.float32)
    strength = 0.31
    dense = scalar[..., None] * weights.reshape(1, 1, 3) * strength
    lazy = LayerExposureAdditionField(
        scalar,
        tuple(float(value) for value in weights),
        strength,
    )
    film = FilmStockConfig()
    recipe = DevelopRecipeConfig()

    for develop in (develop_color_negative_reduced, develop_color_reversal_reduced):
        dense_result = develop(
            image,
            film,
            recipe,
            latent_layer_exposure_addition=dense,
        )
        lazy_result = develop(
            image,
            film,
            recipe,
            latent_layer_exposure_addition=lazy,
        )
        assert np.array_equal(lazy_result.latent_fraction, dense_result.latent_fraction)
        assert np.array_equal(lazy_result.density_cmy, dense_result.density_cmy)
        assert np.array_equal(
            lazy_result.optical_density_rgb,
            dense_result.optical_density_rgb,
        )


def test_incomplete_auxiliary_layer_removal_changes_final_material_density():
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    image = np.full((5, 7, 3), 0.40, dtype=np.float32)
    film = FilmStockConfig(
        auxiliary_layer_amount=1.0,
        auxiliary_layer_density_rgb=(0.18, 0.12, 0.08),
    )
    clean = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(auxiliary_removal=1.0),
    )
    retained = develop_color_negative_reduced(
        image,
        film,
        DevelopRecipeConfig(auxiliary_removal=0.25),
    )

    assert clean.process_result.final_medium.auxiliary_remaining == 0.0
    assert retained.process_result.final_medium.auxiliary_remaining == 0.75
    assert retained.process_result.state.totals()["auxiliary_remaining"] == 0.75
    assert float(np.mean(retained.density_cmy)) > float(np.mean(clean.density_cmy))
