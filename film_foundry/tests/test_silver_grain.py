import numpy as np

from film_foundry.core.silver_grain import (
    SilverGrainPlan,
    apply_metallic_silver_grain,
)
from film_foundry.model.config import DarkroomConfig


def _plan(shape: tuple[int, int], *, seed: int = 17, tile_rows: int = 11) -> SilverGrainPlan:
    return SilverGrainPlan(
        full_shape=shape,
        seed=seed,
        strength=0.08,
        radius=0.006,
        clump_mix=0.24,
        tile_rows=tile_rows,
    )


def test_silver_grain_zero_component_is_strict_identity():
    optical = np.full((19, 31, 3), 0.4, dtype=np.float32)
    before = optical.copy()
    applied = apply_metallic_silver_grain(
        optical,
        np.zeros(optical.shape[:2], dtype=np.float32),
        _plan(optical.shape[:2]),
    )
    assert applied is False
    assert np.array_equal(optical, before)


def test_silver_grain_is_repeatable_and_strictly_neutral():
    shape = (29, 43)
    silver = np.full(shape, 0.75, dtype=np.float32)
    first = np.full((*shape, 3), 0.9, dtype=np.float32)
    second = first.copy()
    plan = _plan(shape, seed=91)

    assert apply_metallic_silver_grain(first, silver, plan)
    assert apply_metallic_silver_grain(second, silver, plan)

    assert np.array_equal(first, second)
    delta = first - np.float32(0.9)
    assert np.array_equal(delta[..., 0], delta[..., 1])
    assert np.array_equal(delta[..., 1], delta[..., 2])
    assert float(np.std(delta[..., 0])) > 0.0


def test_silver_grain_is_exact_across_external_row_tiles():
    shape = (37, 53)
    y = np.linspace(0.0, 1.0, shape[0], dtype=np.float32)[:, None]
    x = np.linspace(0.1, 0.9, shape[1], dtype=np.float32)[None, :]
    silver = (y * x).astype(np.float32)
    full = np.full((*shape, 3), 0.8, dtype=np.float32)
    tiled = full.copy()
    plan = _plan(shape, seed=123, tile_rows=7)

    apply_metallic_silver_grain(full, silver, plan)
    for start in range(0, shape[0], 9):
        stop = min(start + 9, shape[0])
        apply_metallic_silver_grain(
            tiled[start:stop],
            silver[start:stop],
            plan,
            row_offset=start,
        )

    assert np.array_equal(tiled, full)


def test_silver_grain_amplitude_tracks_actual_silver_density():
    shape = (41, 59)
    plan = _plan(shape, seed=44)
    low = np.full(shape, 0.08, dtype=np.float32)
    high = np.full(shape, 0.80, dtype=np.float32)
    low_optical = np.ones((*shape, 3), dtype=np.float32)
    high_optical = np.ones((*shape, 3), dtype=np.float32)

    apply_metallic_silver_grain(low_optical, low, plan)
    apply_metallic_silver_grain(high_optical, high, plan)

    assert float(np.std(high_optical[..., 0] - 1.0)) > (
        float(np.std(low_optical[..., 0] - 1.0)) * 2.0
    )


def test_reduced_bw_silver_grain_matches_material_row_tiling():
    from film_foundry.core.engine import (
        _apply_reduced_silver_grain,
        _develop_reduced_material_tiled,
    )
    from film_foundry.core.film_process.integration import develop_bw_negative_reduced

    rng = np.random.default_rng(20260722)
    image = rng.uniform(0.01, 0.95, (23, 37, 3)).astype(np.float32)
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.film.color_process = "bw"
    config.chemistry.program_key = "bw_negative"
    plan = _plan(image.shape[:2], seed=602, tile_rows=5)

    full = develop_bw_negative_reduced(image, config.film, config.chemistry)
    assert _apply_reduced_silver_grain(full, config.film, plan)
    _, tiled_optical, _, audit = _develop_reduced_material_tiled(
        image,
        config,
        "bw_negative",
        7,
        silver_grain_plan=plan,
    )

    assert np.array_equal(tiled_optical, full.optical_density_rgb)
    assert audit["execution"]["independent_silver_grain_applied"] is True


def test_fully_bleached_color_medium_skips_independent_silver_random_field():
    from film_foundry.core.engine import _develop_reduced_material_tiled
    from film_foundry.core.film_process.integration import develop_color_negative_reduced

    rng = np.random.default_rng(20260723)
    image = rng.uniform(0.01, 0.95, (17, 29, 3)).astype(np.float32)
    config = DarkroomConfig()
    config.mode = "color_negative"
    config.chemistry.program_key = "color_negative"
    plan = _plan(image.shape[:2], seed=911)

    full = develop_color_negative_reduced(image, config.film, config.chemistry)
    _, tiled_optical, _, audit = _develop_reduced_material_tiled(
        image,
        config,
        "color_negative",
        6,
        silver_grain_plan=plan,
    )

    assert np.array_equal(tiled_optical, full.optical_density_rgb)
    assert audit["execution"]["independent_silver_grain_applied"] is False


def test_engine_silver_only_path_uses_independent_grain_without_coloring_it():
    import copy

    from film_foundry.core.engine import develop_negative

    rng = np.random.default_rng(20260724)
    image = rng.uniform(0.02, 0.92, (31, 47, 3)).astype(np.float32)
    baseline = DarkroomConfig()
    baseline.mode = "bw_negative"
    baseline.film.color_process = "bw"
    baseline.film.film_base_density_rgb = (0.04, 0.04, 0.04)
    baseline.film.density_min = (0.08, 0.08, 0.08)
    baseline.film.density_max = (1.9, 1.9, 1.9)
    baseline.chemistry.program_key = "bw_negative"
    baseline.enable_mtf = False
    baseline.enable_halation = False
    baseline.enable_grain = False
    without_grain = develop_negative(
        image,
        baseline,
        rng=np.random.default_rng(70),
    )

    experimental = copy.deepcopy(baseline)
    experimental.enable_grain = True
    experimental.film.silver_grain_strength = 0.08
    experimental.film.silver_grain_radius = 0.003
    with_grain = develop_negative(
        image,
        experimental,
        rng=np.random.default_rng(70),
    )

    delta = with_grain.optical_density_rgb - without_grain.optical_density_rgb
    assert np.array_equal(delta[..., 0], delta[..., 1])
    assert np.array_equal(delta[..., 1], delta[..., 2])
    assert float(np.std(delta[..., 0])) > 0.0
    formation = with_grain.metadata["film_process_model"]["silver_grain_formation"]
    assert formation["enabled"] is True
    assert formation["applied"] is True
    assert formation["destination"] == "derived_optical_density_rgb"
