import numpy as np
import cv2
import pytest

from film_foundry.core import accidents

from film_foundry.core.accidents import (
    LightLeakPatternSpec,
    apply_density_accidents,
    apply_density_accident_components,
    apply_exposure_occlusion_to_linear,
    apply_light_leak_to_exposure,
    compose_density_accident_master,
    resolve_light_leak_pattern,
    sample_light_leak_pattern,
    uneven_development_rate_field,
)
from film_foundry.model.config import ChemistryConfig, FilmStockConfig


def test_chemical_stain_map_has_ragged_mid_frequency_detail():
    density = np.ones((128, 128, 3), dtype=np.float32) * 0.8
    recipe = ChemistryConfig(chemical_stain=1.0)

    _, maps = apply_density_accidents(density, recipe, rng=np.random.default_rng(8))
    stain = maps["chemical_stain"]
    smooth = cv2.GaussianBlur(stain, (0, 0), sigmaX=4.0, sigmaY=4.0)

    assert stain.shape == density.shape[:2]
    assert float(np.std(stain - smooth)) > 0.01


def test_light_leak_map_has_ragged_edge_detail():
    image = np.zeros((128, 160, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=1.0)

    _, leak = apply_light_leak_to_exposure(image, recipe, rng=np.random.default_rng(3))
    assert leak is not None
    smooth = cv2.GaussianBlur(leak, (0, 0), sigmaX=5.0, sigmaY=5.0)

    assert leak.shape == image.shape[:2]
    assert float(np.std(leak - smooth)) > 0.015


def test_light_leak_pattern_identity_can_be_reused_independently_of_frame_rng():
    image = np.zeros((93, 137, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=0.72)
    pattern = sample_light_leak_pattern(np.random.default_rng(81))

    output_a, field_a = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(1),
        pattern=pattern,
    )
    output_b, field_b = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(999),
        pattern=pattern,
    )

    assert field_a is not None and field_b is not None
    assert np.array_equal(field_a, field_b)
    assert np.array_equal(output_a, output_b)
    assert np.array_equal(image, np.zeros_like(image))


def test_default_light_leak_sampler_remains_the_explicit_local_edge_sampler():
    default = sample_light_leak_pattern(np.random.default_rng(83))
    explicit = sample_light_leak_pattern(
        np.random.default_rng(83),
        template_key="local_edge",
    )

    assert default == explicit


def test_direct_light_leak_component_resolves_the_recipe_template():
    image = np.zeros((81, 113, 3), dtype=np.float32)
    recipe = ChemistryConfig(
        light_leak_template="cone",
        light_leak_strength=0.7,
    )
    direct, direct_field = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(85),
    )
    explicit_pattern = sample_light_leak_pattern(
        np.random.default_rng(85),
        template_key="cone",
    )
    explicit, explicit_field = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(999),
        pattern=explicit_pattern,
    )

    assert direct_field is not None and explicit_field is not None
    assert np.array_equal(direct_field, explicit_field)
    assert np.array_equal(direct, explicit)
    assert np.array_equal(image, np.zeros_like(image))


def test_auto_and_explicit_local_edge_keep_identical_legacy_output():
    image = np.zeros((83, 117, 3), dtype=np.float32)
    auto, auto_field = apply_light_leak_to_exposure(
        image,
        ChemistryConfig(light_leak_template="auto", light_leak_strength=0.63),
        rng=np.random.default_rng(86),
    )
    local, local_field = apply_light_leak_to_exposure(
        image,
        ChemistryConfig(light_leak_template="local_edge", light_leak_strength=0.63),
        rng=np.random.default_rng(86),
    )

    assert auto_field is not None and local_field is not None
    assert np.array_equal(auto_field, local_field)
    assert np.array_equal(auto, local)


@pytest.mark.parametrize("template_key", ("perimeter", "half_frame", "cone"))
def test_explicit_template_strength_scales_peak_without_equalizing_coverage(template_key):
    image = np.zeros((121, 173, 3), dtype=np.float32)
    full_recipe = ChemistryConfig(
        light_leak_template=template_key,
        light_leak_strength=1.0,
    )
    partial_recipe = ChemistryConfig(
        light_leak_template=template_key,
        light_leak_strength=0.37,
    )
    _, full_field = apply_light_leak_to_exposure(
        image,
        full_recipe,
        rng=np.random.default_rng(88),
    )
    _, partial_field = apply_light_leak_to_exposure(
        image,
        partial_recipe,
        rng=np.random.default_rng(88),
    )

    assert full_field is not None and partial_field is not None
    assert np.isclose(float(np.max(full_field)), 1.0, atol=2e-6)
    assert np.isclose(float(np.max(partial_field)), 0.37, atol=2e-6)
    assert np.allclose(partial_field, full_field * np.float32(0.37), atol=2e-7)


def test_perimeter_template_activates_all_edges_but_not_the_frame_center():
    image = np.zeros((181, 241, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=0.8)
    pattern = sample_light_leak_pattern(
        np.random.default_rng(87),
        template_key="perimeter",
    )

    _, field = apply_light_leak_to_exposure(image, recipe, pattern=pattern)

    assert field is not None
    edge_means = (
        float(np.mean(field[:, :12])),
        float(np.mean(field[:, -12:])),
        float(np.mean(field[:12, :])),
        float(np.mean(field[-12:, :])),
    )
    center_mean = float(np.mean(field[72:109, 96:145]))
    assert min(edge_means) > center_mean * 1.8


def test_half_frame_and_cone_templates_have_distinct_directional_geometry():
    image = np.zeros((201, 241, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=0.9)
    common = {
        "side_weights": (1.0, 0.0, 0.0, 0.0),
        "edge_widths": (0.08, 0.08, 0.08, 0.08),
        "base_side_mix": 0.65,
        "texture_seed": 12345,
        "center_xy": (0.0, 0.5),
        "rotation_degrees": 0.0,
        "falloff": 0.62,
    }
    half_pattern = LightLeakPatternSpec(
        template_key="half_frame",
        breadth=0.90,
        **common,
    )
    cone_pattern = LightLeakPatternSpec(
        template_key="cone",
        breadth=0.30,
        **common,
    )

    _, half = apply_light_leak_to_exposure(image, recipe, pattern=half_pattern)
    _, cone = apply_light_leak_to_exposure(image, recipe, pattern=cone_pattern)

    assert half is not None and cone is not None
    assert float(np.mean(half[:, :48])) > float(np.mean(half[:, -48:])) * 2.0
    cone_center = float(np.mean(cone[86:115, 12:145]))
    cone_outer = 0.5 * (
        float(np.mean(cone[:35, 12:145]))
        + float(np.mean(cone[-35:, 12:145]))
    )
    assert cone_center > cone_outer * 3.0
    assert not np.allclose(half, cone)
    assert np.array_equal(image, np.zeros_like(image))


def test_light_leak_template_validation_rejects_unknown_or_invalid_geometry():
    image = np.zeros((31, 43, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=0.8)
    valid = sample_light_leak_pattern(
        np.random.default_rng(89),
        template_key="cone",
    )

    with np.testing.assert_raises_regex(ValueError, "Unsupported light-leak template"):
        sample_light_leak_pattern(np.random.default_rng(89), template_key="unknown")
    invalid = LightLeakPatternSpec(
        template_key=valid.template_key,
        side_weights=valid.side_weights,
        edge_widths=valid.edge_widths,
        base_side_mix=valid.base_side_mix,
        texture_seed=valid.texture_seed,
        center_xy=(1.2, 0.5),
        rotation_degrees=valid.rotation_degrees,
        breadth=valid.breadth,
        falloff=valid.falloff,
    )
    with np.testing.assert_raises_regex(ValueError, "center"):
        apply_light_leak_to_exposure(image, recipe, pattern=invalid)


def test_sampled_light_leak_defaults_remain_bitwise_identical_to_legacy_single_template():
    image = np.zeros((51, 73, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_template="auto", light_leak_strength=0.71)
    output_a, field_a = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(17),
    )
    output_b, field_b = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(17),
    )
    assert field_a is not None and field_b is not None
    assert np.array_equal(field_a, field_b)
    assert np.array_equal(output_a, output_b)


def test_manual_placement_moves_cone_entry_without_resampling_texture():
    image = np.zeros((81, 121, 3), dtype=np.float32)
    sampled = sample_light_leak_pattern(np.random.default_rng(41), template_key="cone")
    manual = ChemistryConfig(
        light_leak_strength=0.9,
        light_leak_template="cone",
        light_leak_placement="manual",
        light_leak_entry_side="right",
        light_leak_center_y=0.5,
        light_leak_rotation_degrees=0.0,
    )
    resolved = resolve_light_leak_pattern(sampled, manual)
    sampled_recipe = ChemistryConfig(
        light_leak_strength=0.9,
        light_leak_template="cone",
        light_leak_placement="sampled",
    )
    _, expected = apply_light_leak_to_exposure(
        image,
        sampled_recipe,
        pattern=resolved,
    )
    _, got = apply_light_leak_to_exposure(
        image,
        manual,
        pattern=sampled,
    )
    left_manual = ChemistryConfig(
        light_leak_strength=0.9,
        light_leak_template="cone",
        light_leak_placement="manual",
        light_leak_entry_side="left",
        light_leak_center_y=0.5,
        light_leak_rotation_degrees=0.0,
    )
    _, left_field = apply_light_leak_to_exposure(
        image,
        left_manual,
        pattern=sampled,
    )

    assert resolved.side_weights[1] == 1.0
    assert resolved.center_xy == (1.0, 0.5)
    assert resolved.rotation_degrees == 180.0
    assert sampled.texture_seed == resolved.texture_seed
    assert expected is not None and got is not None and left_field is not None
    assert np.array_equal(expected, got)
    assert not np.allclose(got, left_field)


def test_manual_local_edge_entry_side_keeps_the_declared_seal():
    image = np.zeros((64, 96, 3), dtype=np.float32)
    sampled = sample_light_leak_pattern(
        np.random.default_rng(22),
        template_key="local_edge",
    )
    recipe = ChemistryConfig(
        light_leak_strength=1.0,
        light_leak_template="local_edge",
        light_leak_placement="manual",
        light_leak_entry_side="left",
    )
    resolved = resolve_light_leak_pattern(sampled, recipe)
    _, field = apply_light_leak_to_exposure(image, recipe, pattern=sampled)

    assert resolved.side_weights == (1.0, 0.0, 0.0, 0.0)
    assert field is not None
    assert float(np.mean(field[:, :12])) > float(np.mean(field[:, -12:])) * 1.8
    assert sampled.texture_seed == resolved.texture_seed


def test_secondary_template_max_mix_covers_more_than_primary_alone():
    image = np.zeros((72, 96, 3), dtype=np.float32)
    primary = sample_light_leak_pattern(np.random.default_rng(12), template_key="cone")
    secondary = sample_light_leak_pattern(np.random.default_rng(99), template_key="perimeter")
    recipe = ChemistryConfig(
        light_leak_strength=1.0,
        light_leak_template="cone",
        light_leak_secondary_template="perimeter",
        light_leak_mix="max",
    )
    _, combined = apply_light_leak_to_exposure(
        image,
        recipe,
        pattern=primary,
        secondary_pattern=secondary,
    )
    _, cone_only = apply_light_leak_to_exposure(
        image,
        ChemistryConfig(light_leak_strength=1.0, light_leak_template="cone"),
        pattern=primary,
    )
    assert combined is not None and cone_only is not None
    assert float(np.mean(combined > 0.08)) > float(np.mean(cone_only > 0.08))


def test_external_mask_replace_uses_mask_coverage_not_template_geometry():
    image = np.zeros((48, 64, 3), dtype=np.float32)
    mask = np.zeros((48, 64), dtype=np.float32)
    mask[6:18, 42:60] = 1.0
    recipe = ChemistryConfig(
        light_leak_strength=1.0,
        light_leak_template="cone",
        light_leak_mix="replace",
    )
    _, field = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(5),
        mask=mask,
    )
    assert field is not None
    marked = float(np.mean(field[6:18, 42:60]))
    elsewhere = float(np.mean(field[28:44, 4:22]))
    assert marked > elsewhere * 4.0


def test_replace_mix_without_mask_is_rejected():
    image = np.zeros((24, 32, 3), dtype=np.float32)
    recipe = ChemistryConfig(
        light_leak_strength=0.8,
        light_leak_mix="replace",
    )
    with np.testing.assert_raises_regex(ValueError, "replace"):
        apply_light_leak_to_exposure(image, recipe, rng=np.random.default_rng(2))


def test_identity_seed_reuses_pattern_across_frame_rngs():
    image = np.zeros((36, 52, 3), dtype=np.float32)
    recipe = ChemistryConfig(
        light_leak_strength=0.8,
        light_leak_template="half_frame",
        light_leak_identity_seed=404,
    )
    _, field_a = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(1),
    )
    _, field_b = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(999),
    )
    assert field_a is not None and field_b is not None
    assert np.array_equal(field_a, field_b)


def test_large_light_leak_uses_bounded_global_control_field_at_full_output_size(monkeypatch):
    image = np.zeros((19, 1703, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=0.8)
    observed_shapes: list[tuple[int, int]] = []
    original = accidents._edge_leak_mask

    def capture_shape(shape, *args, **kwargs):
        observed_shapes.append(tuple(int(value) for value in shape))
        return original(shape, *args, **kwargs)

    monkeypatch.setattr(accidents, "_edge_leak_mask", capture_shape)
    output, field = apply_light_leak_to_exposure(
        image,
        recipe,
        rng=np.random.default_rng(13),
    )

    assert observed_shapes and max(observed_shapes[0]) == 1600
    assert output.shape == image.shape
    assert field is not None and field.shape == image.shape[:2]
    assert np.array_equal(image, np.zeros_like(image))


def test_density_accident_component_optimization_preserves_component_formula():
    density = np.linspace(0.1, 1.2, 41 * 57 * 3, dtype=np.float32).reshape(41, 57, 3)
    original = density.copy()
    recipe = ChemistryConfig(chemical_stain=0.63, silver_plating=0.44)
    film = FilmStockConfig()

    components = apply_density_accident_components(
        density,
        recipe,
        rng=np.random.default_rng(37),
        film=film,
    )

    expected = original.copy()
    stain = components.maps["chemical_stain"]
    stain_scale = np.asarray(components.chemical_stain_layer_scale, dtype=np.float32)
    expected += stain[..., None] * stain_scale.reshape(1, 1, 3)
    plating = components.maps["silver_plating"]
    neutral_rgb = np.repeat(
        (plating * components.surface_silver_density_scale)[..., None],
        3,
        axis=-1,
    ).astype(np.float32)
    inverse = np.linalg.pinv(
        np.asarray(film.dye_absorption_matrix, dtype=np.float32),
    ).astype(np.float32)
    expected += np.clip(
        np.einsum("...r,lr->...l", neutral_rgb, inverse),
        0.0,
        None,
    ).astype(np.float32)

    assert np.allclose(components.density_cmy, expected, atol=2e-7, rtol=0.0)
    assert np.array_equal(density, original)


def test_deferred_accident_master_is_exactly_the_eager_compatibility_master():
    density = np.linspace(0.08, 1.35, 53 * 71 * 3, dtype=np.float32).reshape(53, 71, 3)
    original = density.copy()
    recipe = ChemistryConfig(
        chemical_stain=0.71,
        silver_plating=0.58,
        reticulation_strength=0.66,
    )
    film = FilmStockConfig()

    eager = apply_density_accident_components(
        density,
        recipe,
        rng=np.random.default_rng(43),
        film=film,
    )
    deferred = apply_density_accident_components(
        density,
        recipe,
        rng=np.random.default_rng(43),
        film=film,
        defer_compatibility_density=True,
    )
    reconstructed = compose_density_accident_master(
        deferred.density_cmy,
        deferred.maps,
        chemical_stain_layer_scale=deferred.chemical_stain_layer_scale,
        surface_silver_layer_scale=deferred.surface_silver_layer_scale,
        reticulation_layer_scale=deferred.reticulation_layer_scale,
    )

    assert deferred.compatibility_density_deferred is True
    assert np.shares_memory(deferred.density_cmy, density)
    assert np.array_equal(deferred.maps["chemical_stain"], eager.maps["chemical_stain"])
    assert np.array_equal(deferred.maps["silver_plating"], eager.maps["silver_plating"])
    assert np.array_equal(deferred.maps["reticulation"], eager.maps["reticulation"])
    assert np.array_equal(reconstructed, eager.density_cmy)
    assert np.array_equal(density, original)


def test_uneven_development_map_can_have_directional_streaks():
    exposure = np.ones((128, 160, 3), dtype=np.float32) * 0.4
    recipe = ChemistryConfig(uneven_development=1.0, agitation=0.0)

    uneven = uneven_development_rate_field(
        exposure,
        recipe,
        rng=np.random.default_rng(4),
    )
    assert uneven is not None
    vertical_delta = float(np.mean(np.abs(np.diff(uneven, axis=0))))
    horizontal_delta = float(np.mean(np.abs(np.diff(uneven, axis=1))))

    assert max(vertical_delta, horizontal_delta) > min(vertical_delta, horizontal_delta) * 1.5
    assert not np.allclose(uneven, 1.0)


def test_uneven_development_uses_one_dominant_flow_not_crosshatched_axes(monkeypatch):
    calls: list[bool] = []

    def one_axis(shape, rng, *, along_y, long_sigma, short_sigma):
        calls.append(bool(along_y))
        return np.zeros(shape, dtype=np.float32)

    monkeypatch.setattr(accidents, "_axis_streaks", one_axis)
    result = accidents._uneven_development_map(
        (73, 109),
        np.random.default_rng(39),
        sigma=17.0,
        agitation_deficit=0.8,
    )

    assert len(calls) == 1
    assert result.shape == (73, 109)
    assert np.all(np.isfinite(result))


def test_uneven_development_is_not_applied_again_as_postprocess_stain():
    density = np.ones((64, 80, 3), dtype=np.float32) * 0.8
    recipe = ChemistryConfig(uneven_development=1.0)

    processed, maps = apply_density_accidents(
        density,
        recipe,
        rng=np.random.default_rng(5),
    )

    assert np.array_equal(processed, density)
    assert "uneven_development" not in maps


def test_light_leak_uses_local_entry_instead_of_four_edge_frame():
    image = np.zeros((160, 200, 3), dtype=np.float32)
    recipe = ChemistryConfig(light_leak_strength=1.0)

    _, leak = apply_light_leak_to_exposure(image, recipe, rng=np.random.default_rng(11))
    assert leak is not None
    edge_means = (
        float(np.mean(leak[:, :12])),
        float(np.mean(leak[:, -12:])),
        float(np.mean(leak[:12, :])),
        float(np.mean(leak[-12:, :])),
    )
    assert max(edge_means) > min(edge_means) * 2.0


def test_surface_silver_plating_adds_neutral_density_without_stain():
    density = np.ones((96, 120, 3), dtype=np.float32) * 0.5
    recipe = ChemistryConfig(silver_plating=0.7, chemical_stain=0.0)

    processed, maps = apply_density_accidents(
        density,
        recipe,
        rng=np.random.default_rng(17),
    )

    assert "silver_plating" in maps
    assert "chemical_stain" not in maps
    assert float(np.mean(processed)) > float(np.mean(density))
    assert np.allclose(processed[..., 0], processed[..., 1])
    assert np.allclose(processed[..., 1], processed[..., 2])


def test_surface_silver_plating_is_optically_neutral_on_color_material():
    density = np.zeros((72, 96, 3), dtype=np.float32)
    film = FilmStockConfig(
        dye_absorption_matrix=(
            (1.0, 0.18, 0.04),
            (0.07, 1.0, 0.16),
            (0.03, 0.22, 1.0),
        )
    )
    processed, _ = apply_density_accidents(
        density,
        ChemistryConfig(silver_plating=0.8),
        rng=np.random.default_rng(19),
        film=film,
    )

    absorption = np.asarray(film.dye_absorption_matrix, dtype=np.float32)
    observed_rgb_density = np.einsum("...l,rl->...r", processed, absorption)
    channel_means = observed_rgb_density.mean(axis=(0, 1))
    assert float(np.max(channel_means) - np.min(channel_means)) < 1e-5


def test_reticulation_is_a_bounded_centered_emulsion_structure_component():
    density = np.ones((109, 151, 3), dtype=np.float32) * 0.65
    original = density.copy()
    components = apply_density_accident_components(
        density,
        ChemistryConfig(reticulation_strength=0.8, temperature_c=20.0),
        rng=np.random.default_rng(57),
        film=FilmStockConfig(),
    )

    field = components.maps["reticulation"]
    assert field.shape == density.shape[:2]
    assert np.isfinite(field).all()
    assert float(field.min()) < -0.05
    assert float(field.max()) > 0.25
    assert abs(float(np.mean(field))) < 2e-4
    assert components.reticulation_density_scale == 0.06
    assert components.reticulation_layer_scale is not None
    assert np.array_equal(density, original)


def test_reticulation_strength_scales_a_centered_component_without_changing_topology():
    density = np.ones((97, 137, 3), dtype=np.float32) * 0.65
    low = apply_density_accident_components(
        density,
        ChemistryConfig(reticulation_strength=0.25),
        rng=np.random.default_rng(59),
        film=FilmStockConfig(),
    )
    full = apply_density_accident_components(
        density,
        ChemistryConfig(reticulation_strength=1.0),
        rng=np.random.default_rng(59),
        film=FilmStockConfig(),
    )

    assert np.array_equal(low.maps["reticulation"], full.maps["reticulation"])
    assert np.isclose(low.reticulation_density_scale, 0.01875)
    assert np.isclose(full.reticulation_density_scale, 0.075)
    low_delta = low.density_cmy - density
    full_delta = full.density_cmy - density
    assert np.allclose(low_delta, full_delta * np.float32(0.25), atol=8e-8)
    assert abs(float(np.mean(full_delta, dtype=np.float64))) < 2e-7


def test_single_hot_bath_does_not_invent_reticulation_without_explicit_damage():
    density = np.ones((41, 59, 3), dtype=np.float32) * 0.5
    processed, maps = apply_density_accidents(
        density,
        ChemistryConfig(temperature_c=38.0, reticulation_strength=0.0),
        rng=np.random.default_rng(61),
    )

    assert "reticulation" not in maps
    assert np.array_equal(processed, density)


def test_zero_exposure_occlusion_is_strict_identity_and_does_not_mutate_input():
    image = np.linspace(0.05, 0.9, 48 * 64 * 3, dtype=np.float32).reshape(48, 64, 3)
    original = image.copy()
    output, field = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(),
        rng=np.random.default_rng(4),
    )
    assert field is None
    assert np.array_equal(output, original)
    assert np.array_equal(image, original)


def test_exposure_occlusion_reduces_linear_light_and_keeps_input_read_only():
    image = np.full((72, 96, 3), 0.55, dtype=np.float32)
    original = image.copy()
    recipe = ChemistryConfig(
        exposure_occlusion_dust=0.85,
        exposure_occlusion_hair=0.7,
        exposure_occlusion_insect=0.8,
        exposure_occlusion_debris=0.75,
    )
    output, field = apply_exposure_occlusion_to_linear(
        image,
        recipe,
        rng=np.random.default_rng(9),
    )
    assert field is not None
    assert field.shape == image.shape[:2]
    assert float(np.max(field)) > 0.2
    assert np.array_equal(image, original)
    assert float(np.min(output)) < float(np.min(original)) - 0.05
    assert np.all(output <= original + np.float32(1e-6))
    restored = original * (1.0 - field)[..., None]
    assert np.allclose(output, restored, atol=2e-6, rtol=0.0)


def test_exposure_occlusion_types_have_distinct_geometry():
    image = np.ones((90, 140, 3), dtype=np.float32)
    _, dust_field = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_dust=1.0),
        rng=np.random.default_rng(21),
    )
    _, hair_field = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_hair=1.0),
        rng=np.random.default_rng(21),
    )
    _, insect_field = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_insect=1.0),
        rng=np.random.default_rng(21),
    )
    _, debris_field = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_debris=1.0),
        rng=np.random.default_rng(21),
    )

    def _stats(field):
        binary = (field > 0.18).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        components = stats[1:]
        areas = components[:, cv2.CC_STAT_AREA].astype(np.float64)
        widths = components[:, cv2.CC_STAT_WIDTH].astype(np.float64)
        heights = components[:, cv2.CC_STAT_HEIGHT].astype(np.float64)
        aspects = np.maximum(widths, heights) / np.maximum(1.0, np.minimum(widths, heights))
        return count - 1, float(np.mean(areas)), float(np.mean(aspects))

    dust_n, dust_area, dust_aspect = _stats(dust_field)
    hair_n, _hair_area, hair_aspect = _stats(hair_field)
    insect_n, insect_area, _insect_aspect = _stats(insect_field)
    debris_n, debris_area, _debris_aspect = _stats(debris_field)
    assert dust_n > insect_n
    assert dust_n > hair_n
    assert hair_aspect > dust_aspect
    assert hair_aspect > 1.45
    assert insect_area > dust_area
    assert debris_n >= 1 and debris_area > 0.0
    assert not np.allclose(dust_field, debris_field, atol=0.02)


def test_exposure_occlusion_is_seed_stable_and_uses_bounded_control_field(monkeypatch):
    image = np.full((21, 1705, 3), 0.4, dtype=np.float32)
    recipe = ChemistryConfig(exposure_occlusion_dust=0.8, exposure_occlusion_hair=0.6)
    observed: list[tuple[int, int]] = []
    original = accidents._dust_occlusion_field

    def capture(shape, amount, rng):
        observed.append(tuple(int(value) for value in shape))
        return original(shape, amount, rng)

    monkeypatch.setattr(accidents, "_dust_occlusion_field", capture)
    a, field_a = apply_exposure_occlusion_to_linear(image, recipe, rng=np.random.default_rng(17))
    b, field_b = apply_exposure_occlusion_to_linear(image, recipe, rng=np.random.default_rng(17))
    assert observed and max(observed[0]) == 1600
    assert a.shape == image.shape
    assert np.array_equal(a, b)
    assert field_a is not None and field_a.shape == image.shape[:2]
    assert np.array_equal(field_a, field_b)


def test_hair_and_insect_keep_sheet_identity_while_dust_and_debris_follow_frame_rng():
    image = np.ones((80, 120, 3), dtype=np.float32)
    identity = ChemistryConfig(
        exposure_occlusion_hair=0.85,
        exposure_occlusion_insect=0.9,
        exposure_occlusion_identity_seed=41,
    )
    _, hair_a = apply_exposure_occlusion_to_linear(
        image,
        identity,
        rng=np.random.default_rng(3),
    )
    _, hair_b = apply_exposure_occlusion_to_linear(
        image,
        identity,
        rng=np.random.default_rng(99),
    )
    assert hair_a is not None and hair_b is not None
    assert np.array_equal(hair_a, hair_b)

    other_identity = ChemistryConfig(
        exposure_occlusion_hair=0.85,
        exposure_occlusion_insect=0.9,
        exposure_occlusion_identity_seed=42,
    )
    _, hair_c = apply_exposure_occlusion_to_linear(
        image,
        other_identity,
        rng=np.random.default_rng(3),
    )
    assert not np.array_equal(hair_a, hair_c)

    _, dust_a = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_dust=0.9),
        rng=np.random.default_rng(3),
    )
    _, dust_b = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_dust=0.9),
        rng=np.random.default_rng(99),
    )
    _, debris_a = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_debris=0.9),
        rng=np.random.default_rng(3),
    )
    _, debris_b = apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_debris=0.9),
        rng=np.random.default_rng(99),
    )
    assert dust_a is not None and dust_b is not None
    assert debris_a is not None and debris_b is not None
    assert not np.array_equal(dust_a, dust_b)
    assert not np.array_equal(debris_a, debris_b)


def test_hair_only_occlusion_does_not_consume_frame_rng():
    image = np.ones((48, 64, 3), dtype=np.float32)
    rng_a = np.random.default_rng(11)
    rng_b = np.random.default_rng(11)
    apply_exposure_occlusion_to_linear(
        image,
        ChemistryConfig(exposure_occlusion_hair=0.8),
        rng=rng_a,
    )
    assert float(rng_a.random()) == float(rng_b.random())


