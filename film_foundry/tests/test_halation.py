import cv2
import numpy as np
import film_foundry.core.halation as halation_module

from film_foundry.core.color import luminance
from film_foundry.core.halation import (
    FULL_HALATION_SOURCE_MODEL,
    PARAMETERIZED_WARM_HALATION_PLAN,
    REDUCED_HALATION_SOURCE_MODEL,
    _automatic_halation_source_tile_rows,
    apply_halation,
    apply_parameterized_warm_return,
    halation_return_field,
    halation_psf_kernel,
    halation_source_energy,
    halation_source_energy_tiled,
    radius_to_sigma,
    soft_threshold,
    spread_halation_work_source,
    spread_multiscale_halation_work_source,
)
from film_foundry.core.spatial_fields import (
    LayerExposureAdditionField,
    SpatialFieldPlan,
    global_field_grid,
    resample_global_scalar_field,
)
from film_foundry.model.config import FilmStockConfig


def _legacy_halation_source_energy(
    image: np.ndarray,
    film: FilmStockConfig,
) -> np.ndarray:
    """Pre-lifetime-optimization formula retained as a regression oracle."""
    y_original = luminance(image)
    source_sigma = radius_to_sigma(film.halation_source_blur_radius, image.shape)
    scattered = cv2.GaussianBlur(
        y_original,
        (0, 0),
        sigmaX=source_sigma,
        sigmaY=source_sigma,
    )
    grad_x = cv2.Sobel(y_original, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(y_original, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)
    gradient_scale = float(np.percentile(gradient, 96.0))
    if gradient_scale <= 1e-6:
        edge_mask = np.zeros_like(y_original, dtype=np.float32)
    else:
        normalized = np.clip(gradient / gradient_scale, 0.0, 1.0)
        x = np.clip((normalized - 0.18) / 0.42, 0.0, 1.0)
        edge_mask = (x * x * (3.0 - 2.0 * x)).astype(np.float32)
    edge_mask *= float(np.clip(film.halation_gradient_suppression, 0.0, 1.0))
    y = (
        y_original * (1.0 - edge_mask) + scattered * edge_mask
    ).astype(np.float32)

    highlight_mask = soft_threshold(
        y,
        film.halation_threshold,
        film.halation_softness,
    )
    peak_sigma = radius_to_sigma(film.halation_peak_radius, image.shape)
    local_base = cv2.GaussianBlur(y, (0, 0), sigmaX=peak_sigma, sigmaY=peak_sigma)
    local_peak = np.clip(y - local_base, 0.0, None)
    peak_mask = soft_threshold(
        local_peak / (local_base + 0.05),
        float(film.halation_peak_threshold),
        float(film.halation_peak_softness),
    )

    suppression = float(np.clip(film.halation_area_suppression, 0.0, 1.0))
    if suppression <= 0.0:
        area_weight = np.ones_like(highlight_mask, dtype=np.float32)
    else:
        area_sigma = radius_to_sigma(film.halation_area_radius, image.shape)
        local_area = cv2.GaussianBlur(
            highlight_mask,
            (0, 0),
            sigmaX=area_sigma,
            sigmaY=area_sigma,
        )
        area_mask = soft_threshold(
            local_area,
            float(film.halation_area_threshold),
            0.25,
        )
        area_weight = np.clip(
            1.0 - suppression * area_mask,
            0.0,
            1.0,
        ).astype(np.float32)
    excess_energy = np.clip(y - film.halation_threshold, 0.0, None)
    return (
        highlight_mask * peak_mask * area_weight * excess_energy
    ).astype(np.float32)


def test_soft_threshold_is_smooth_and_bounded():
    values = np.array([0.0, 0.45, 0.5, 0.55, 1.0], dtype=np.float32)
    mask = soft_threshold(values, threshold=0.5, softness=0.1)
    assert mask[0] == 0.0
    assert mask[-1] == 1.0
    assert np.all(np.diff(mask) >= 0.0)


def test_soft_threshold_preserves_float64_reference_rounding():
    values = np.linspace(-0.25, 1.25, 257, dtype=np.float64)
    edge0 = 0.5 - 0.1
    edge1 = 0.5 + 0.1
    x = np.clip((values - edge0) / (edge1 - edge0), 0.0, 1.0)
    expected = (x * x * (3.0 - 2.0 * x)).astype(np.float32)

    actual = soft_threshold(values, threshold=0.5, softness=0.1)

    assert np.array_equal(actual, expected)


def test_halation_responds_to_highlights_only():
    film = FilmStockConfig(halation_strength=0.5, halation_threshold=0.7, halation_peak_radius=0.04)
    dark = np.full((32, 32, 3), 0.1, dtype=np.float32)
    bright = dark.copy()
    bright[16, 16] = 1.0

    dark_out = apply_halation(dark, film)
    bright_out = apply_halation(bright, film)

    assert np.allclose(dark_out, dark, atol=1e-4)
    assert bright_out.mean() > bright.mean()


def test_halation_source_prefers_local_peaks_over_broad_bright_areas():
    film = FilmStockConfig(halation_threshold=0.7, halation_peak_radius=0.04)
    broad = np.full((64, 64, 3), 0.95, dtype=np.float32)
    peaked = np.full((64, 64, 3), 0.45, dtype=np.float32)
    peaked[32, 32] = 1.0

    broad_source = halation_source_energy(broad, film)
    peaked_source = halation_source_energy(peaked, film)

    assert peaked_source.max() > broad_source.max()
    assert broad_source.mean() < peaked_source.mean()


def test_parameterized_warm_halation_declares_pre_latent_nonpersistent_field():
    payload = PARAMETERIZED_WARM_HALATION_PLAN.as_dict()

    assert payload["key"] == "parameterized_warm_halation"
    assert payload["stage"] == "pre_latent_exposure"
    assert payload["quantity"] == "exposure_addition"
    assert payload["field_kind"] == "global_scalar"
    assert payload["requires_global_source"] is True
    assert payload["requires_tile_halo"] is True
    assert payload["persistent_state"] is False
    assert payload["disabled_identity_guarantee"] is True


def test_global_scalar_grid_preserves_frame_geometry_for_both_orientations():
    landscape = global_field_grid((500, 800, 3), 200)
    portrait = global_field_grid((800, 500, 3), 200)

    assert landscape.work_shape == (125, 200)
    assert portrait.work_shape == (200, 125)
    field = np.arange(500 * 800, dtype=np.float32).reshape(500, 800)
    small = resample_global_scalar_field(field, landscape, to_work_grid=True)
    restored = resample_global_scalar_field(small, landscape, to_work_grid=False)
    assert small.shape == landscape.work_shape
    assert restored.shape == landscape.full_shape


def test_invalid_spatial_stage_is_rejected():
    try:
        SpatialFieldPlan(
            key="bad",
            stage="scanner",
            quantity="exposure_addition",
            field_kind="global_scalar",
            requires_global_source=True,
            requires_tile_halo=False,
        )
    except ValueError as exc:
        assert "stage" in str(exc)
    else:
        raise AssertionError("invalid spatial stage must fail closed")


def test_point_source_is_detected_before_global_work_grid_reduction():
    film = FilmStockConfig(
        halation_threshold=0.7,
        halation_peak_radius=0.02,
        halation_area_suppression=0.0,
    )
    image = np.full((240, 360, 3), 0.08, dtype=np.float32)
    image[119:121, 179:181] = 1.0

    source = halation_source_energy(image, film)
    grid = global_field_grid(source.shape, 90)
    work_source = resample_global_scalar_field(source, grid, to_work_grid=True)

    assert source.max() > 0.0
    assert work_source.max() > 0.0
    assert work_source.sum(dtype=np.float64) > 0.0


def test_halation_source_lifetime_optimization_preserves_legacy_formula():
    rng = np.random.default_rng(20260716)
    image = rng.uniform(0.02, 0.82, (93, 137, 3)).astype(np.float32)
    image[20:24, 31:35] = (1.0, 0.92, 0.78)
    image[61:64, 88:92] = (0.82, 0.93, 1.0)
    film = FilmStockConfig()

    expected = _legacy_halation_source_energy(image, film)
    actual = halation_source_energy(image, film)

    assert np.allclose(actual, expected, atol=2e-7)


def test_tiled_halation_source_is_exact_and_tile_size_independent():
    rng = np.random.default_rng(20260717)
    image = rng.uniform(0.02, 0.84, (173, 257, 3)).astype(np.float32)
    # Include frame-edge and would-be tile-boundary sources.
    image[0:3, 0:3] = (1.0, 0.91, 0.78)
    image[40:44, 126:130] = (0.88, 0.96, 1.0)
    image[169:173, 253:257] = (1.0, 0.82, 0.66)
    film = FilmStockConfig()
    expected = halation_source_energy(image, film)

    for tile_rows in (7, 31, 64):
        actual = halation_source_energy_tiled(
            image,
            film,
            tile_rows=tile_rows,
        )
        assert np.array_equal(actual, expected)


def test_reduced_halation_source_retains_peak_and_area_gates_and_is_tile_exact():
    image = np.full((173, 257, 3), 0.04, dtype=np.float32)
    image[18:22, 31:35] = 1.25
    image[74:142, 140:232] = 0.94
    original = image.copy()
    film = FilmStockConfig(
        halation_threshold=0.45,
        halation_softness=0.08,
        halation_peak_threshold=0.01,
        halation_peak_softness=0.04,
        halation_area_suppression=0.9,
    )

    reduced = halation_source_energy(
        image,
        film,
        model=REDUCED_HALATION_SOURCE_MODEL,
    )
    full = halation_source_energy(
        image,
        film,
        model=FULL_HALATION_SOURCE_MODEL,
    )

    assert np.array_equal(image, original)
    assert float(np.max(reduced[17:23, 30:36])) > 0.0
    assert float(np.mean(reduced[92:124, 166:206])) < 0.05 * float(
        np.max(reduced[17:23, 30:36])
    )
    assert float(np.max(full)) > 0.0
    for tile_rows in (7, 31, 64):
        tiled = halation_source_energy_tiled(
            image,
            film,
            tile_rows=tile_rows,
            model=REDUCED_HALATION_SOURCE_MODEL,
        )
        assert np.array_equal(tiled, reduced)


def test_halation_source_models_fail_closed_when_unknown():
    image = np.full((17, 23, 3), 0.8, dtype=np.float32)
    film = FilmStockConfig()

    for runner in (
        lambda: halation_source_energy(image, film, model="unknown"),
        lambda: halation_source_energy_tiled(
            image,
            film,
            tile_rows=8,
            model="unknown",
        ),
    ):
        try:
            runner()
        except ValueError as exc:
            assert "model" in str(exc)
        else:
            raise AssertionError("unknown halation source model must fail closed")


def test_reduced_halation_source_tracks_full_energy_on_graded_highlights():
    yy, xx = np.mgrid[:240, :360].astype(np.float32)
    base = 0.04 + 0.75 * (xx / 359.0)
    base += 0.10 * np.sin(xx * 0.11) * np.cos(yy * 0.07)
    image = np.stack((base * 1.08, base, base * 0.92), axis=-1).astype(np.float32)
    image[35:39, 45:49] = 1.5
    image[120:124, 280:284] = 1.2
    film = FilmStockConfig(
        halation_threshold=0.55,
        halation_softness=0.08,
        halation_peak_threshold=0.01,
        halation_peak_softness=0.04,
        halation_area_suppression=0.8,
    )

    full = halation_source_energy(
        image,
        film,
        model=FULL_HALATION_SOURCE_MODEL,
    )
    reduced = halation_source_energy(
        image,
        film,
        model=REDUCED_HALATION_SOURCE_MODEL,
    )
    active = (full > 0.0) | (reduced > 0.0)

    assert 0.95 <= float(reduced.sum() / full.sum()) <= 1.05
    assert float(np.corrcoef(full[active], reduced[active])[0, 1]) > 0.995
    assert not np.array_equal(reduced, full)


def test_reduced_halation_source_bypasses_full_micro_scatter_pass(monkeypatch):
    image = np.full((31, 47, 3), 0.04, dtype=np.float32)
    image[15, 23] = 2.0
    film = FilmStockConfig(
        halation_threshold=0.45,
        halation_peak_threshold=0.001,
        halation_area_suppression=0.0,
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("full micro-scatter source pass was used")

    monkeypatch.setattr(
        halation_module,
        "_halation_source_luminance",
        fail_if_called,
    )
    result = halation_source_energy(
        image,
        film,
        model=REDUCED_HALATION_SOURCE_MODEL,
    )

    assert float(np.max(result)) > 0.0


def test_large_reduced_frames_enable_bounded_source_tiles_only():
    assert _automatic_halation_source_tile_rows((5000, 8000, 3), 1800) == 500
    assert _automatic_halation_source_tile_rows((2400, 3600, 3), 1800) == 512
    assert _automatic_halation_source_tile_rows((5000, 8000, 3), None) is None
    assert _automatic_halation_source_tile_rows((1000, 1200, 3), 900) is None


def test_refactored_halation_matches_legacy_warm_formula_exactly():
    image = np.full((96, 128, 3), 0.12, dtype=np.float32)
    image[20:24, 30:34] = (1.0, 0.92, 0.78)
    image[62:66, 84:88] = (0.85, 0.92, 1.0)
    film = FilmStockConfig(halation_strength=0.37)
    work_long_edge = 64

    source = halation_source_energy(image, film)
    scale = min(1.0, work_long_edge / max(source.shape))
    work_size = (
        max(1, int(round(source.shape[1] * scale))),
        max(1, int(round(source.shape[0] * scale))),
    )
    legacy_source = cv2.resize(source, work_size, interpolation=cv2.INTER_AREA)
    kernel = halation_psf_kernel((*legacy_source.shape, 3), film)
    legacy_spread = cv2.filter2D(
        legacy_source,
        cv2.CV_32F,
        kernel,
        borderType=cv2.BORDER_CONSTANT,
    )
    legacy_spread = cv2.resize(
        legacy_spread,
        (image.shape[1], image.shape[0]),
        interpolation=cv2.INTER_LINEAR,
    )
    color = np.asarray(film.halation_color, dtype=np.float32)
    color /= max(float(color.max(initial=1.0)), 1e-6)
    expected = np.clip(
        image + legacy_spread[..., None] * color * film.halation_strength,
        0.0,
        4.0,
    ).astype(np.float32)

    actual = apply_halation(image, film, work_long_edge=work_long_edge)

    assert np.array_equal(actual, expected)


def test_warm_return_is_exact_does_not_mutate_input_and_validates_shape():
    rng = np.random.default_rng(20260718)
    image = rng.uniform(0.0, 3.95, (47, 61, 3)).astype(np.float32)
    spread = rng.uniform(0.0, 0.30, image.shape[:2]).astype(np.float32)
    film = FilmStockConfig(
        halation_strength=0.37,
        halation_color=(1.0, 0.42, 0.16),
    )
    original = image.copy()
    color = np.asarray(film.halation_color, dtype=np.float32)
    color /= max(float(color.max(initial=1.0)), 1e-6)
    expected = np.clip(
        image + spread[..., None] * color * film.halation_strength,
        0.0,
        4.0,
    ).astype(np.float32)

    actual = apply_parameterized_warm_return(image, spread, film)

    assert np.array_equal(actual, expected)
    assert np.array_equal(image, original)

    try:
        apply_parameterized_warm_return(image, spread[:-1], film)
    except ValueError as exc:
        assert "match" in str(exc)
    else:
        raise AssertionError("mismatched halation spread must fail closed")


def test_scalar_layer_return_field_expands_lazily_and_exactly():
    scalar = np.linspace(0.0, 0.4, 5 * 7, dtype=np.float32).reshape(5, 7)
    addition = LayerExposureAdditionField(
        scalar,
        (2.0, 1.0, 0.0),
        strength=0.35,
    )
    target = np.zeros((5, 7, 3), dtype=np.float32)
    scales = np.asarray((0.8, 1.1, 0.9), dtype=np.float32)
    expected = np.empty_like(target)
    for layer, weight in enumerate((1.0, 0.5, 0.0)):
        expected[..., layer] = scalar * weight * 0.35 * scales[layer]

    addition.add_scaled_to(target, scales)

    assert np.array_equal(target, expected)
    assert addition.scalar_field.flags.writeable is False
    assert addition.shape == (5, 7, 3)
    tile = addition.slice_rows(1, 4)
    assert tile.shape == (3, 7, 3)
    assert np.shares_memory(tile.scalar_field, addition.scalar_field)


def test_shared_halation_return_field_preserves_compatibility_formula():
    image = np.full((61, 89, 3), 0.08, dtype=np.float32)
    image[29:32, 43:46] = (1.0, 0.92, 0.78)
    film = FilmStockConfig(halation_strength=0.31)

    spread = halation_return_field(image, film, work_long_edge=48)
    expected = apply_parameterized_warm_return(image, spread, film)
    actual = apply_halation(image, film, work_long_edge=48)

    assert np.array_equal(actual, expected)


def test_halation_empty_source_can_skip_material_field_without_changing_public_result():
    x = np.linspace(0.08, 0.96, 109, dtype=np.float32)[None, :]
    image = np.broadcast_to(x[..., None], (73, 109, 3)).copy()
    film = FilmStockConfig()

    public_result = halation_return_field(image, film, work_long_edge=48)
    material_result = halation_return_field(
        image,
        film,
        work_long_edge=48,
        allow_empty_none=True,
    )

    assert public_result is not None
    assert np.count_nonzero(public_result) == 0
    assert material_result is None


def test_halation_nonempty_source_is_identical_when_empty_skip_is_allowed():
    image = np.full((240, 360, 3), 0.08, dtype=np.float32)
    image[119:121, 179:181] = 1.0
    film = FilmStockConfig(
        halation_threshold=0.7,
        halation_peak_radius=0.02,
        halation_area_suppression=0.0,
    )

    expected = halation_return_field(image, film, work_long_edge=48)
    actual = halation_return_field(
        image,
        film,
        work_long_edge=48,
        allow_empty_none=True,
    )

    assert expected is not None
    assert actual is not None
    assert np.array_equal(actual, expected)


def test_multiscale_main_only_is_exact_legacy_spread_identity():
    rng = np.random.default_rng(20260722)
    source = rng.uniform(0.0, 0.2, (73, 109)).astype(np.float32)
    grid = global_field_grid(source.shape, None)
    film = FilmStockConfig()

    expected = spread_halation_work_source(source, film, grid)
    actual = spread_multiscale_halation_work_source(
        source,
        film,
        grid,
        (0.0, 1.0, 0.0),
    )

    assert np.array_equal(actual, expected)


def test_multiscale_halation_has_ordered_radius_and_energy_preserving_mix():
    size = 129
    source = np.zeros((size, size), dtype=np.float32)
    source[size // 2, size // 2] = 1.0
    grid = global_field_grid(source.shape, None)
    film = FilmStockConfig(
        halation_outer_radius=0.05,
        halation_exponential_radius=0.022,
    )
    compact = spread_multiscale_halation_work_source(source, film, grid, (1, 0, 0))
    main = spread_multiscale_halation_work_source(source, film, grid, (0, 1, 0))
    wide = spread_multiscale_halation_work_source(source, film, grid, (0, 0, 1))
    mixed = spread_multiscale_halation_work_source(
        source,
        film,
        grid,
        (0.2, 0.6, 0.2),
    )
    yy, xx = np.mgrid[:size, :size]
    radius_squared = (yy - size // 2) ** 2 + (xx - size // 2) ** 2

    def rms_radius(field: np.ndarray) -> float:
        return float(
            np.sqrt(
                np.sum(field * radius_squared, dtype=np.float64)
                / np.sum(field, dtype=np.float64)
            )
        )

    assert rms_radius(compact) < rms_radius(main) < rms_radius(wide)
    assert np.isclose(float(compact.sum()), 1.0, atol=1e-6)
    assert np.isclose(float(main.sum()), 1.0, atol=1e-6)
    assert np.isclose(float(wide.sum()), 1.0, atol=1e-6)
    assert np.allclose(mixed, compact * 0.2 + main * 0.6 + wide * 0.2, atol=2e-7)


def test_multiscale_halation_rejects_invalid_scale_weights():
    source = np.zeros((9, 13), dtype=np.float32)
    grid = global_field_grid(source.shape, None)
    film = FilmStockConfig()

    for weights in ((0.0, 0.0, 0.0), (1.0, -0.1, 0.1), (1.0, 0.0)):
        try:
            spread_multiscale_halation_work_source(source, film, grid, weights)
        except ValueError as exc:
            assert "weights" in str(exc)
        else:
            raise AssertionError("invalid multiscale weights must fail closed")


def test_multiscale_global_grid_is_orientation_consistent():
    landscape_grid = global_field_grid((91, 137), 67)
    portrait_grid = global_field_grid((137, 91), 67)
    landscape_source = np.zeros(landscape_grid.work_shape, dtype=np.float32)
    landscape_source[landscape_source.shape[0] // 2, landscape_source.shape[1] // 2] = 1.0
    portrait_source = landscape_source.T.copy()
    film = FilmStockConfig(
        halation_outer_radius=0.05,
        halation_exponential_radius=0.022,
    )

    landscape = spread_multiscale_halation_work_source(
        landscape_source,
        film,
        landscape_grid,
        (0.2, 0.6, 0.2),
    )
    portrait = spread_multiscale_halation_work_source(
        portrait_source,
        film,
        portrait_grid,
        (0.2, 0.6, 0.2),
    )

    assert landscape.shape == (91, 137)
    assert portrait.shape == (137, 91)
    assert np.allclose(landscape, portrait.T, atol=2e-7)
