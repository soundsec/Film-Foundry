import copy

import numpy as np

from half_frame_darkroom.core.engine import develop_negative, process_array, scan_negative
from half_frame_darkroom.core.scanner import negative_total_density_rgb
from half_frame_darkroom.core.states import DevelopedNegative, ScannedPositive
from half_frame_darkroom.model.config import DarkroomConfig


def test_develop_and_scan_are_separate_states():
    image = np.full((12, 16, 3), 0.45, dtype=np.float32)
    config = DarkroomConfig()
    config.enable_grain = False
    config.scanner.scan_normalize = False

    negative = develop_negative(image, config)
    scanned = scan_negative(negative)

    assert isinstance(negative, DevelopedNegative)
    assert isinstance(scanned, ScannedPositive)
    assert negative.density_grain.shape == image.shape
    assert scanned.negative_total_density.shape == image.shape
    assert scanned.scanner_raw.shape == image.shape
    assert scanned.output_srgb.shape == image.shape
    assert scanned.positive_linear.min() >= 0.0
    assert scanned.positive_linear.max() <= 1.0


def test_process_array_matches_develop_then_scan_without_grain():
    image = np.linspace(0.1, 0.9, 12 * 16 * 3, dtype=np.float32).reshape(12, 16, 3)
    config = DarkroomConfig()
    config.enable_grain = False
    config.scanner.scan_normalize = False

    direct = process_array(image, config)
    negative = develop_negative(image, config)
    scanned = scan_negative(negative)

    assert np.allclose(direct, scanned.output_srgb)


def test_bw_negative_scan_ignores_rgb_color_bias_controls():
    image = np.linspace(0.1, 0.9, 12 * 16, dtype=np.float32)
    image = np.repeat(image.reshape(12, 16, 1), 3, axis=-1)
    config = DarkroomConfig()
    config.mode = "bw_negative"
    config.enable_grain = False
    config.scanner.scan_normalize = False
    config.scanner.print_color_shift = (0.12, -0.08, 0.06)
    config.scanner.highlight_color_bias = (0.8, 1.2, 0.7)

    negative = develop_negative(image, config)
    scanned = scan_negative(negative, config)

    assert np.allclose(scanned.positive_linear[..., 0], scanned.positive_linear[..., 1])
    assert np.allclose(scanned.positive_linear[..., 0], scanned.positive_linear[..., 2])


def test_develop_recipe_changes_negative_density_state():
    image = np.linspace(0.08, 0.92, 14 * 18 * 3, dtype=np.float32).reshape(14, 18, 3)
    base = DarkroomConfig()
    base.enable_grain = False
    base.scanner.scan_normalize = False

    altered = copy.deepcopy(base)
    altered.chemistry.developer_type = "push"
    altered.chemistry.time_min = 13.5
    altered.chemistry.temperature_c = 24.0
    altered.chemistry.concentration = 1.35

    negative_a = develop_negative(image, base)
    negative_b = develop_negative(image, altered)
    scanned_a = scan_negative(negative_a, base)
    scanned_b = scan_negative(negative_b, altered)

    assert not np.allclose(negative_a.density_cmy, negative_b.density_cmy)
    assert not np.allclose(negative_a.density_grain, negative_b.density_grain)
    assert not np.allclose(scanned_a.output_srgb, scanned_b.output_srgb)


def test_scan_interpretation_does_not_mutate_negative_state():
    image = np.linspace(0.06, 0.94, 13 * 17 * 3, dtype=np.float32).reshape(13, 17, 3)
    develop_config = DarkroomConfig()
    develop_config.enable_grain = False
    develop_config.scanner.scan_normalize = False
    negative = develop_negative(image, develop_config)

    density_cmy_before = negative.density_cmy.copy()
    density_grain_before = negative.density_grain.copy()
    stored_config = negative.metadata["runtime_config"]
    total_density_before = negative_total_density_rgb(density_grain_before, stored_config.film)

    scan_a = DarkroomConfig()
    scan_a.scanner.scan_normalize = False
    scan_a.look.print_contrast = 0.85
    scan_a.look.print_exposure_ev = -0.25

    scan_b = DarkroomConfig()
    scan_b.scanner.scan_normalize = False
    scan_b.look.print_contrast = 1.55
    scan_b.look.print_exposure_ev = 0.45
    scan_b.scanner.print_color_shift = (0.08, -0.04, -0.08)

    scanned_a = scan_negative(negative, scan_a)
    scanned_b = scan_negative(negative, scan_b)
    total_density_after = negative_total_density_rgb(negative.density_grain, stored_config.film)

    assert np.allclose(negative.density_cmy, density_cmy_before)
    assert np.allclose(negative.density_grain, density_grain_before)
    assert np.allclose(total_density_after, total_density_before)
    assert not np.allclose(scanned_a.output_srgb, scanned_b.output_srgb)


def test_rescan_with_stored_runtime_does_not_apply_film_look_twice():
    image = np.linspace(0.05, 0.95, 12 * 15 * 3, dtype=np.float32).reshape(12, 15, 3)
    develop_config = DarkroomConfig()
    develop_config.enable_grain = False
    develop_config.scanner.scan_normalize = False
    develop_config.look.look_strength = 1.45
    develop_config.look.negative_contrast = 1.35
    develop_config.look.saturation_multiplier = 1.25

    negative = develop_negative(image, develop_config)
    stored_runtime = negative.metadata["runtime_config"]
    stored_gamma = np.asarray(stored_runtime.film.hd_gamma, dtype=np.float32)
    stored_dye = np.asarray(stored_runtime.film.dye_absorption_matrix, dtype=np.float32)

    scan_config = DarkroomConfig()
    scan_config.scanner.scan_normalize = False
    scan_config.look.look_strength = 2.0
    scan_config.look.negative_contrast = 2.0
    scan_config.look.saturation_multiplier = 1.8
    scan_config.look.print_contrast = 1.4
    scan_config.look.print_exposure_ev = 0.2

    scanned = scan_negative(negative, scan_config)
    scan_runtime = scanned.metadata["runtime_config"]

    assert np.allclose(scan_runtime.film.hd_gamma, stored_gamma)
    assert np.allclose(scan_runtime.film.dye_absorption_matrix, stored_dye)


def test_light_leak_enters_negative_formation_before_scan():
    image = np.zeros((18, 24, 3), dtype=np.float32) + 0.12
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.scanner.scan_normalize = False

    leaked = copy.deepcopy(clean)
    leaked.chemistry.light_leak_strength = 0.85

    negative_clean = develop_negative(image, clean, rng=np.random.default_rng(9))
    negative_leaked = develop_negative(image, leaked, rng=np.random.default_rng(9))

    assert negative_leaked.metadata["has_light_leak_map"] is True
    assert not np.allclose(negative_clean.after_halation, negative_leaked.after_halation)
    assert not np.allclose(negative_clean.density_cmy, negative_leaked.density_cmy)
    assert negative_leaked.density_cmy.mean() > negative_clean.density_cmy.mean()


def test_chemical_stain_and_uneven_development_modify_density_master():
    image = np.linspace(0.08, 0.92, 18 * 24 * 3, dtype=np.float32).reshape(18, 24, 3)
    clean = DarkroomConfig()
    clean.enable_grain = False
    clean.enable_halation = False
    clean.scanner.scan_normalize = False

    ruined = copy.deepcopy(clean)
    ruined.chemistry.chemical_stain = 0.80
    ruined.chemistry.uneven_development = 0.70

    negative_clean = develop_negative(image, clean, rng=np.random.default_rng(11))
    negative_ruined = develop_negative(image, ruined, rng=np.random.default_rng(11))

    assert "chemical_stain" in negative_ruined.metadata["accident_maps"]
    assert "uneven_development" in negative_ruined.metadata["accident_maps"]
    assert not np.allclose(negative_clean.density_grain, negative_ruined.density_grain)
    assert np.isfinite(negative_ruined.density_grain).all()
    assert negative_ruined.density_grain.min() >= 0.0
