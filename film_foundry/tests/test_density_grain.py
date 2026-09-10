import hashlib

import numpy as np
import cv2

from film_foundry.core import density_grain as density_grain_module
from film_foundry.core.density_grain import (
    FULL_DENSITY_GRAIN_MODEL,
    LEGACY_REDUCED_DENSITY_GRAIN_MODEL,
    REDUCED_DENSITY_GRAIN_MODEL,
    _apply_shadow_noise_scale,
    _blurred_multilayer_field,
    _density_response_level,
    _effective_grain_scale,
    _grain_scales_for_film,
    _reduced_cloud_parameters,
    apply_density_grain,
)
from film_foundry.core.execution_topology import (
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.core.random_fields import standard_normal_float32
from film_foundry.model.config import ChemistryConfig, FilmStockConfig


def test_developer_clump_factor_only_reweights_declared_grain_scales():
    film = FilmStockConfig(
        grain_scales=(0.001, 0.004, 0.012),
        grain_scale_weights=(0.72, 0.34, 0.10),
    )

    standard_scales, standard_weights = _grain_scales_for_film(film, 1.0, 1.0)
    clumped_scales, clumped_weights = _grain_scales_for_film(film, 1.0, 2.0)
    fine_scales, fine_weights = _grain_scales_for_film(film, 1.0, 0.5)
    coarse_index = int(np.argmax(standard_scales))

    assert np.array_equal(clumped_scales, standard_scales)
    assert np.array_equal(fine_scales, standard_scales)
    assert np.isclose(np.linalg.norm(standard_weights), 1.0)
    assert np.isclose(np.linalg.norm(clumped_weights), 1.0)
    assert np.isclose(np.linalg.norm(fine_weights), 1.0)
    assert clumped_weights[coarse_index] > standard_weights[coarse_index]
    assert fine_weights[coarse_index] < standard_weights[coarse_index]


def test_reduced_effective_scale_is_bounded_and_tracks_declared_scale_energy():
    scales = np.asarray((0.001, 0.004, 0.016), dtype=np.float32)
    fine_weighted = np.asarray((0.95, 0.20, 0.04), dtype=np.float32)
    coarse_weighted = np.asarray((0.04, 0.20, 0.95), dtype=np.float32)

    fine_scale = _effective_grain_scale(scales, fine_weighted)
    coarse_scale = _effective_grain_scale(scales, coarse_weighted)

    assert float(scales.min()) <= fine_scale <= float(scales.max())
    assert float(scales.min()) <= coarse_scale <= float(scales.max())
    assert coarse_scale > fine_scale


def test_reduced_cloud_is_bounded_and_only_represents_residual_coarse_energy():
    one_scale = np.asarray((0.004,), dtype=np.float32)
    one_weight = np.asarray((1.0,), dtype=np.float32)
    cloud_scale, cloud_mix = _reduced_cloud_parameters(
        one_scale,
        one_weight,
        _effective_grain_scale(one_scale, one_weight),
    )
    assert np.isclose(cloud_scale, 0.004)
    assert cloud_mix == 0.0

    scales = np.asarray((0.001, 0.004, 0.016), dtype=np.float32)
    fine_weighted = np.asarray((0.90, 0.30, 0.12), dtype=np.float32)
    coarse_weighted = np.asarray((0.12, 0.30, 0.90), dtype=np.float32)
    fine_effective = _effective_grain_scale(scales, fine_weighted)
    coarse_effective = _effective_grain_scale(scales, coarse_weighted)
    fine_cloud_scale, fine_cloud_mix = _reduced_cloud_parameters(
        scales,
        fine_weighted,
        fine_effective,
    )
    coarse_cloud_scale, coarse_cloud_mix = _reduced_cloud_parameters(
        scales,
        coarse_weighted,
        coarse_effective,
    )

    assert np.isclose(fine_cloud_scale, float(scales.max()))
    assert np.isclose(coarse_cloud_scale, float(scales.max()))
    assert 0.0 < fine_cloud_mix <= 0.24
    assert 0.0 < coarse_cloud_mix <= 0.24
    assert fine_cloud_mix > coarse_cloud_mix


def test_reduced_density_grain_is_seeded_and_layers_remain_partly_correlated():
    density = np.ones((192, 224, 3), dtype=np.float32)
    film = FilmStockConfig(granularity_sigma=(0.03, 0.03, 0.03))
    chemistry = ChemistryConfig()

    first = apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(20260811),
        image_polarity="positive",
        component_scope="emulsion",
        model=REDUCED_DENSITY_GRAIN_MODEL,
    )
    second = apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(20260811),
        image_polarity="positive",
        component_scope="emulsion",
        model=REDUCED_DENSITY_GRAIN_MODEL,
    )
    delta = first - density
    correlation = float(
        np.corrcoef(delta[..., 0].ravel(), delta[..., 1].ravel())[0, 1]
    )

    assert np.array_equal(first, second)
    assert correlation > 0.03
    assert not np.allclose(delta[..., 0], delta[..., 1])


def test_reduced_density_grain_preserves_density_and_positive_endpoint_directions():
    film = FilmStockConfig(
        granularity_sigma=(0.03, 0.03, 0.03),
        grain_density_correlation_radius=0.0005,
    )
    chemistry = ChemistryConfig()
    low = np.full((128, 128, 3), 0.20, dtype=np.float32)
    high = np.full((128, 128, 3), 1.40, dtype=np.float32)
    positive_mid = np.full((128, 128, 3), 1.05, dtype=np.float32)
    positive_blocked = np.broadcast_to(
        np.asarray(film.density_max, dtype=np.float32),
        positive_mid.shape,
    ).copy()

    def reduced_delta(source: np.ndarray, polarity: str) -> np.ndarray:
        return apply_density_grain(
            source,
            film,
            chemistry,
            rng=np.random.default_rng(41),
            image_polarity=polarity,
            component_scope="emulsion",
            model=REDUCED_DENSITY_GRAIN_MODEL,
        ) - source

    assert float(reduced_delta(high, "negative").std()) > float(
        reduced_delta(low, "negative").std()
    )
    assert float(reduced_delta(positive_blocked, "positive").std()) < float(
        reduced_delta(positive_mid, "positive").std()
    )


def test_reduced_density_grain_models_keep_bounded_blur_passes(monkeypatch):
    density = np.ones((96, 128, 3), dtype=np.float32)
    film = FilmStockConfig(
        grain_scales=(0.001, 0.004, 0.012),
        grain_scale_weights=(0.72, 0.34, 0.10),
    )
    chemistry = ChemistryConfig()
    original_blur = density_grain_module.cv2.GaussianBlur
    calls = {"count": 0}

    def counted_blur(*args, **kwargs):
        calls["count"] += 1
        return original_blur(*args, **kwargs)

    monkeypatch.setattr(density_grain_module.cv2, "GaussianBlur", counted_blur)
    apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(53),
        image_polarity="positive",
        component_scope="emulsion",
        model=FULL_DENSITY_GRAIN_MODEL,
    )
    full_calls = calls["count"]
    calls["count"] = 0
    apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(53),
        image_polarity="positive",
        component_scope="emulsion",
        model=LEGACY_REDUCED_DENSITY_GRAIN_MODEL,
    )
    legacy_reduced_calls = calls["count"]
    calls["count"] = 0
    apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(53),
        image_polarity="positive",
        component_scope="emulsion",
        model=REDUCED_DENSITY_GRAIN_MODEL,
    )
    reduced_calls = calls["count"]

    assert full_calls == 12
    assert legacy_reduced_calls == 2
    assert reduced_calls == 3


def test_density_grain_rejects_unknown_model():
    with np.testing.assert_raises(ValueError):
        apply_density_grain(
            np.ones((8, 10, 3), dtype=np.float32),
            FilmStockConfig(),
            ChemistryConfig(),
            model="not-a-grain-model",
        )


def test_chunked_float32_normal_field_is_exactly_the_legacy_conversion():
    legacy_rng = np.random.default_rng(20260722)
    bounded_rng = np.random.default_rng(20260722)
    expected = legacy_rng.standard_normal((17, 23)).astype(np.float32)

    actual = standard_normal_float32(
        bounded_rng,
        (17, 23),
        max_temp_values=37,
    )

    assert np.array_equal(actual, expected)
    assert bounded_rng.bit_generator.state == legacy_rng.bit_generator.state


def test_scalar_scratch_density_response_is_exactly_the_reference_topology():
    density = (
        np.random.default_rng(109)
        .uniform(-0.2, 2.8, (97, 131, 3))
        .astype(np.float32)
    )
    d_min = np.asarray((0.08, 0.12, 0.17), dtype=np.float32)
    d_max = np.asarray((2.15, 2.30, 2.05), dtype=np.float32)

    optimized = _density_response_level(density, d_min, d_max)
    with execution_topology(REFERENCE_TOPOLOGY):
        reference = _density_response_level(density, d_min, d_max)

    assert np.array_equal(optimized, reference)


def test_density_grain_optimized_and_reference_topologies_are_exact_and_rng_stable():
    density = np.linspace(
        0.05,
        1.75,
        83 * 127 * 3,
        dtype=np.float32,
    ).reshape(83, 127, 3)
    film = FilmStockConfig(
        grain_scales=(0.001, 0.005),
        grain_scale_weights=(0.78, 0.34),
    )
    chemistry = ChemistryConfig()
    optimized_rng = np.random.default_rng(113)
    reference_rng = np.random.default_rng(113)

    optimized = apply_density_grain(
        density,
        film,
        chemistry,
        rng=optimized_rng,
        component_scope="emulsion",
    )
    with execution_topology(REFERENCE_TOPOLOGY):
        reference = apply_density_grain(
            density,
            film,
            chemistry,
            rng=reference_rng,
            component_scope="emulsion",
        )

    assert np.array_equal(optimized, reference)
    assert optimized_rng.bit_generator.state == reference_rng.bit_generator.state


def test_positive_reduced_work_sigma_deferral_is_exact_and_rng_stable():
    density = np.linspace(
        0.03,
        2.10,
        97 * 131 * 3,
        dtype=np.float32,
    ).reshape(97, 131, 3)
    film = FilmStockConfig(
        grain_scales=(0.001, 0.005),
        grain_scale_weights=(0.78, 0.34),
        granularity_sigma=(0.022, 0.019, 0.025),
    )
    chemistry = ChemistryConfig()
    optimized_rng = np.random.default_rng(139)
    reference_rng = np.random.default_rng(139)

    optimized = apply_density_grain(
        density,
        film,
        chemistry,
        rng=optimized_rng,
        work_long_edge=61,
        image_polarity="positive",
        component_scope="emulsion",
    )
    with execution_topology(REFERENCE_TOPOLOGY):
        reference = apply_density_grain(
            density,
            film,
            chemistry,
            rng=reference_rng,
            work_long_edge=61,
            image_polarity="positive",
            component_scope="emulsion",
        )

    assert np.array_equal(optimized, reference)
    assert optimized_rng.bit_generator.state == reference_rng.bit_generator.state


def test_scalar_shadow_grain_scale_is_exactly_the_reference_rgb_field():
    rng = np.random.default_rng(127)
    source = rng.standard_normal((91, 137, 3)).astype(np.float32)
    weight = rng.random((91, 137), dtype=np.float32)
    sigma_base = np.asarray((0.012, 0.019, 0.027), dtype=np.float32)
    optimized = source.copy()
    reference = source.copy()

    _apply_shadow_noise_scale(
        optimized,
        weight,
        sigma_base,
        1.37,
    )
    with execution_topology(REFERENCE_TOPOLOGY):
        _apply_shadow_noise_scale(
            reference,
            weight,
            sigma_base,
            1.37,
        )

    assert np.array_equal(optimized, reference)


def test_precomputed_mean_normalization_is_exactly_the_legacy_formula(monkeypatch):
    source = np.random.default_rng(101).standard_normal((97, 131, 3)).astype(np.float32)
    legacy = source.copy()
    legacy -= float(np.mean(legacy))
    legacy /= max(float(np.std(legacy)), 1e-6)

    if density_grain_module._STD_ACCEPTS_PRECOMPUTED_MEAN:
        optimized = density_grain_module._normalize_unit_field(source.copy())
        assert np.array_equal(optimized, legacy)

    monkeypatch.setattr(density_grain_module, "_STD_ACCEPTS_PRECOMPUTED_MEAN", False)
    fallback = density_grain_module._normalize_unit_field(source.copy())
    assert np.array_equal(fallback, legacy)


def test_multilayer_grain_buffer_reuse_preserves_rng_consumption_order():
    shape = (17, 23)
    actual_rng = np.random.default_rng(103)
    field = _blurred_multilayer_field(actual_rng, shape, 0.006)

    reference_rng = np.random.default_rng(103)
    reference_rng.standard_normal(shape)
    for _ in range(3):
        reference_rng.uniform(-1.0, 1.0, 2)
        reference_rng.standard_normal(shape)
        reference_rng.uniform(-1.0, 1.0, 2)

    assert field.shape == (*shape, 3)
    assert field.dtype == np.float32
    assert np.isfinite(field).all()
    assert hashlib.sha256(field.tobytes()).hexdigest() == (
        "ea25afd24c3537a381cbf7f85ae7ff3c404cd92f85341e67b29575136b311669"
    )
    assert np.array_equal(
        actual_rng.bit_generator.random_raw(8),
        reference_rng.bit_generator.random_raw(8),
    )


def test_multiscale_field_can_overwrite_private_scratch_exactly():
    expected_rng = np.random.default_rng(107)
    actual_rng = np.random.default_rng(107)
    expected = _blurred_multilayer_field(expected_rng, (19, 29), 0.004)
    scratch = np.full((19, 29, 3), -999.0, dtype=np.float32)

    actual = _blurred_multilayer_field(
        actual_rng,
        (19, 29),
        0.004,
        out=scratch,
    )

    assert actual is scratch
    assert np.array_equal(actual, expected)
    assert actual_rng.bit_generator.state == expected_rng.bit_generator.state


def test_density_grain_is_repeatable_with_seed():
    density = np.ones((24, 24, 3), dtype=np.float32) * 1.0
    film = FilmStockConfig()
    chemistry = ChemistryConfig()
    first = apply_density_grain(density, film, chemistry, rng=np.random.default_rng(3))
    second = apply_density_grain(density, film, chemistry, rng=np.random.default_rng(3))
    assert np.allclose(first, second)


def test_density_grain_preserves_input_ownership_at_native_and_reduced_work_sizes():
    density = np.linspace(0.1, 1.4, 40 * 56 * 3, dtype=np.float32).reshape(40, 56, 3)
    original = density.copy()
    film = FilmStockConfig()
    chemistry = ChemistryConfig()

    for work_long_edge in (None, 24):
        result = apply_density_grain(
            density,
            film,
            chemistry,
            rng=np.random.default_rng(23),
            work_long_edge=work_long_edge,
            component_scope="emulsion",
        )

        assert np.array_equal(density, original)
        assert not np.shares_memory(result, density)
        assert result.shape == density.shape
        assert result.dtype == np.float32


def test_consumed_grain_input_becomes_exact_effective_delta_at_native_and_reduced_sizes():
    density = np.linspace(
        0.0,
        1.4,
        57 * 89 * 3,
        dtype=np.float32,
    ).reshape(57, 89, 3)
    film = FilmStockConfig(
        grain_scales=(0.001, 0.005),
        grain_scale_weights=(0.78, 0.34),
        granularity_sigma=(0.08, 0.07, 0.09),
    )
    chemistry = ChemistryConfig()

    for work_long_edge in (None, 37):
        expected_rng = np.random.default_rng(131)
        actual_rng = np.random.default_rng(131)
        ordinary = apply_density_grain(
            density,
            film,
            chemistry,
            rng=expected_rng,
            work_long_edge=work_long_edge,
            component_scope="emulsion",
        )
        consumed = density.copy()
        actual = apply_density_grain(
            consumed,
            film,
            chemistry,
            rng=actual_rng,
            work_long_edge=work_long_edge,
            component_scope="emulsion",
            consume_input_as_effective_delta=True,
        )

        expected_delta = ordinary - density
        assert actual is consumed
        assert np.array_equal(actual, expected_delta)
        assert actual_rng.bit_generator.state == expected_rng.bit_generator.state


def test_consumed_grain_input_requires_explicit_private_float32_ownership():
    density = np.ones((17, 23, 3), dtype=np.float64)

    with np.testing.assert_raises(ValueError):
        apply_density_grain(
            density,
            FilmStockConfig(),
            ChemistryConfig(),
            rng=np.random.default_rng(137),
            consume_input_as_effective_delta=True,
        )


def test_positive_low_density_does_not_receive_negative_shadow_clumping():
    density = np.ones((256, 256, 3), dtype=np.float32) * 0.18
    film = FilmStockConfig(granularity_sigma=(0.03, 0.03, 0.03))
    chemistry = ChemistryConfig()
    negative = apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(31),
        image_polarity="negative",
    )
    positive = apply_density_grain(
        density,
        film,
        chemistry,
        rng=np.random.default_rng(31),
        image_polarity="positive",
    )

    assert float((negative - density).std()) > float((positive - density).std()) * 1.15


def test_density_grain_variance_grows_with_density():
    film = FilmStockConfig(grain_density_correlation_radius=0.0005)
    chemistry = ChemistryConfig()
    low = np.ones((48, 48, 3), dtype=np.float32) * 0.2
    high = np.ones((48, 48, 3), dtype=np.float32) * 1.4
    low_out = apply_density_grain(low, film, chemistry, rng=np.random.default_rng(4))
    high_out = apply_density_grain(high, film, chemistry, rng=np.random.default_rng(4))
    assert (high_out - high).std() > (low_out - low).std()


def test_positive_grain_tapers_at_blocked_dmax_instead_of_growing_without_bound():
    film = FilmStockConfig(
        granularity_sigma=(0.03, 0.03, 0.03),
        grain_density_correlation_radius=0.0005,
    )
    chemistry = ChemistryConfig()
    middle = np.ones((128, 128, 3), dtype=np.float32) * 1.05
    blocked = np.ones((128, 128, 3), dtype=np.float32) * np.asarray(
        film.density_max,
        dtype=np.float32,
    )

    middle_out = apply_density_grain(
        middle,
        film,
        chemistry,
        rng=np.random.default_rng(41),
        image_polarity="positive",
    )
    blocked_out = apply_density_grain(
        blocked,
        film,
        chemistry,
        rng=np.random.default_rng(41),
        image_polarity="positive",
    )

    assert float((blocked_out - blocked).std()) < float((middle_out - middle).std())


def test_grain_correlation_radius_changes_spatial_texture():
    density = np.ones((512, 512, 3), dtype=np.float32) * 1.2
    chemistry = ChemistryConfig()
    fine = FilmStockConfig(grain_density_correlation_radius=0.0005)
    coarse = FilmStockConfig(grain_density_correlation_radius=0.004)

    fine_out = apply_density_grain(density, fine, chemistry, rng=np.random.default_rng(5))
    coarse_out = apply_density_grain(density, coarse, chemistry, rng=np.random.default_rng(5))

    fine_neighbor_delta = np.abs(np.diff(fine_out[..., 0], axis=1)).mean()
    coarse_neighbor_delta = np.abs(np.diff(coarse_out[..., 0], axis=1)).mean()
    assert coarse_neighbor_delta < fine_neighbor_delta


def test_density_grain_scale_weights_add_low_frequency_clumps():
    density = np.ones((256, 256, 3), dtype=np.float32) * 1.2
    chemistry = ChemistryConfig()
    fine = FilmStockConfig(grain_scales=(0.001,), grain_scale_weights=(1.0,))
    coarse = FilmStockConfig(grain_scales=(0.012,), grain_scale_weights=(1.0,))

    fine_delta = apply_density_grain(density, fine, chemistry, rng=np.random.default_rng(6))[..., 0] - density[..., 0]
    coarse_delta = apply_density_grain(density, coarse, chemistry, rng=np.random.default_rng(6))[..., 0] - density[..., 0]

    fine_low = cv2.GaussianBlur(fine_delta, (0, 0), sigmaX=12.0, sigmaY=12.0)
    coarse_low = cv2.GaussianBlur(coarse_delta, (0, 0), sigmaX=12.0, sigmaY=12.0)
    assert float(np.std(coarse_low)) > float(np.std(fine_low)) * 2.0


def test_density_grain_layers_are_partly_correlated_not_identical():
    density = np.ones((256, 256, 3), dtype=np.float32) * 1.0
    chemistry = ChemistryConfig()
    film = FilmStockConfig(granularity_sigma=(0.03, 0.03, 0.03))

    delta = apply_density_grain(density, film, chemistry, rng=np.random.default_rng(9)) - density
    corr_c_m = float(np.corrcoef(delta[..., 0].ravel(), delta[..., 1].ravel())[0, 1])

    assert corr_c_m > 0.03
    assert not np.allclose(delta[..., 0], delta[..., 1])


def test_low_density_grain_has_coarser_shadow_clumps():
    chemistry = ChemistryConfig()
    film = FilmStockConfig(granularity_sigma=(0.03, 0.03, 0.03))
    low = np.ones((256, 256, 3), dtype=np.float32) * 0.11
    mid = np.ones((256, 256, 3), dtype=np.float32) * 0.75

    low_delta = apply_density_grain(low, film, chemistry, rng=np.random.default_rng(10))[..., 0] - low[..., 0]
    mid_delta = apply_density_grain(mid, film, chemistry, rng=np.random.default_rng(10))[..., 0] - mid[..., 0]
    low_lowpass = cv2.GaussianBlur(low_delta, (0, 0), sigmaX=12.0, sigmaY=12.0)
    mid_lowpass = cv2.GaussianBlur(mid_delta, (0, 0), sigmaX=12.0, sigmaY=12.0)

    low_ratio = float(np.std(low_lowpass) / max(float(np.std(low_delta)), 1e-9))
    mid_ratio = float(np.std(mid_lowpass) / max(float(np.std(mid_delta)), 1e-9))
    assert low_ratio > mid_ratio * 1.25


def test_push_and_exhaustion_amplify_actual_density_grain():
    density = np.ones((192, 192, 3), dtype=np.float32) * 1.0
    film = FilmStockConfig(granularity_sigma=(0.02, 0.02, 0.02))
    normal = ChemistryConfig()
    stressed = ChemistryConfig(
        developer_type="push",
        push_stops=2.0,
        temperature_c=26.0,
        developer_exhaustion=0.55,
        fixer_exhaustion=0.35,
        silver_retention=0.25,
    )

    normal_delta = apply_density_grain(density, film, normal, rng=np.random.default_rng(17)) - density
    stressed_delta = apply_density_grain(density, film, stressed, rng=np.random.default_rng(17)) - density

    assert float(stressed_delta.std()) > float(normal_delta.std()) * 1.35


def test_emulsion_grain_scope_ignores_post_process_deposits_and_retained_silver():
    density = np.ones((128, 144, 3), dtype=np.float32) * 0.9
    film = FilmStockConfig(granularity_sigma=(0.02, 0.02, 0.02))
    clean = ChemistryConfig()
    deposits = ChemistryConfig(
        fixer_exhaustion=0.8,
        chemical_stain=0.7,
        silver_retention=0.65,
    )

    clean_grain = apply_density_grain(
        density,
        film,
        clean,
        rng=np.random.default_rng(31),
        component_scope="emulsion",
    )
    deposit_grain = apply_density_grain(
        density,
        film,
        deposits,
        rng=np.random.default_rng(31),
        component_scope="emulsion",
    )

    assert np.allclose(clean_grain, deposit_grain, atol=2e-7)
