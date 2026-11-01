from __future__ import annotations

import copy
from dataclasses import asdict

import numpy as np
import pytest

from film_foundry.app.workflow import config_from_share_code, encode_recipe_share
from film_foundry.contracts import data_kernel
from film_foundry.core.engine import (
    develop_negative, develop_negative_from_latent, expose_image_to_latent, scan_medium_output,
)
from film_foundry.core.halation import halation_psf_kernel
from film_foundry.core.latent_io import (
    latent_identity_from_film, load_exposed_latent, mark_exposed_latent_consumed, save_exposed_latent,
)
from film_foundry.core.material_geometry import (
    MATERIAL_RADIUS_FIELDS, MaterialGeometry, project_formation_config, resolve_material_geometry,
)
from film_foundry.core.states import developed_medium_metadata
from film_foundry.model.config import DarkroomConfig


def _image():
    image = np.linspace(0.03, 0.96, 96 * 144 * 3, dtype=np.float32).reshape(96, 144, 3)
    image[42:48, 65:71] = 1.0
    return image


def _physical_config():
    config = DarkroomConfig()
    config.film.spatial_reference_short_edge_mm = 24.0
    return config


def test_geometry_projects_same_material_length_on_full_and_work_grids():
    config = _physical_config()
    geometry = resolve_material_geometry(config.film, config.chemistry, (800, 1200, 3))
    assert geometry.frame_short_edge_mm == 24.0
    assert geometry.radius_um(0.003) == pytest.approx(72.0)
    assert geometry.radius_pixels(0.003, (800, 1200)) == pytest.approx(2.4)
    assert geometry.radius_pixels(0.003, (400, 600)) == pytest.approx(1.2)
    # Rounding the work grid does not redefine the physical frame dimensions.
    assert geometry.radius_pixels(0.003, (399, 600)) / 399 == pytest.approx(0.003)
    rotated = resolve_material_geometry(config.film, config.chemistry, (1200, 800, 3))
    assert rotated.frame_short_edge_mm == geometry.frame_short_edge_mm


def test_format_changes_all_selected_radii_without_mutating_recipe():
    config = _physical_config()
    config.chemistry.frame_size = "6x7"
    before = asdict(config)
    geometry = resolve_material_geometry(config.film, config.chemistry, (800, 1200, 3))
    assert geometry.frame_long_edge_mm == 69.0
    projected = project_formation_config(config, geometry)
    for key in MATERIAL_RADIUS_FIELDS:
        assert getattr(projected.film, key) == pytest.approx(getattr(config.film, key) * 36.0 / 69.0)
    assert projected.chemistry.frame_size == "35mm"
    assert projected.film.grain_scales == config.film.grain_scales
    assert projected.film.grain_density_correlation_radius == config.film.grain_density_correlation_radius
    assert asdict(config) == before


def test_larger_physical_frame_has_smaller_halation_psf_at_same_pixel_count():
    config = _physical_config()
    small = resolve_material_geometry(config.film, config.chemistry, (400, 600, 3))
    config.chemistry.frame_long_edge_mm = 72.0
    large = resolve_material_geometry(config.film, config.chemistry, (400, 600, 3))
    kernels = [halation_psf_kernel((400, 600, 3), project_formation_config(config, g).film)
               for g in (small, large)]
    moments = []
    for kernel in kernels:
        y, x = np.indices(kernel.shape)
        moments.append(np.sum(kernel * ((x - kernel.shape[1] // 2) ** 2 + (y - kernel.shape[0] // 2) ** 2)))
        assert float(kernel.sum()) == pytest.approx(1.0, abs=2e-7)
    assert moments[1] < moments[0] * 0.35


@pytest.mark.parametrize("reference,long_edge", [(0, None), (-1, None), (True, None), (float('nan'), None),
                                                    (24, 0), (24, float('inf')), (None, 36)])
def test_invalid_physical_lengths_fail_closed(reference, long_edge):
    config = DarkroomConfig()
    config.film.spatial_reference_short_edge_mm = reference
    config.chemistry.frame_long_edge_mm = long_edge
    with pytest.raises(ValueError):
        develop_negative(_image(), config)


def test_unknown_physical_format_requires_explicit_extent_but_legacy_still_works():
    config = DarkroomConfig()
    config.chemistry.frame_size = "custom"
    assert resolve_material_geometry(config.film, config.chemistry, (10, 15, 3)) is None
    config.film.spatial_reference_short_edge_mm = 24.0
    with pytest.raises(ValueError, match="unknown physical frame_size"):
        resolve_material_geometry(config.film, config.chemistry, (10, 15, 3))
    config.chemistry.frame_long_edge_mm = 42.0
    assert resolve_material_geometry(config.film, config.chemistry, (10, 15, 3)).frame_long_edge_mm == 42


@pytest.mark.parametrize('execution_mode', ['quality', 'reduced_fast'])
def test_reference_frame_matches_legacy_35mm_arrays_and_keeps_unprojected_snapshot(execution_mode):
    legacy = DarkroomConfig()
    physical = _physical_config()
    legacy.processing.execution_mode = physical.processing.execution_mode = execution_mode
    before = asdict(physical)
    old = develop_negative(_image(), legacy, rng=np.random.default_rng(53))
    new = develop_negative(_image(), physical, rng=np.random.default_rng(53))
    for name in ('linear_input', 'after_mtf', 'after_halation', 'density_cmy', 'density_grain', 'optical_density_rgb'):
        np.testing.assert_array_equal(getattr(old, name), getattr(new, name))
    assert asdict(physical) == before
    snapshot = new.metadata['runtime_config']
    assert snapshot.film.halation_core_radius == physical.film.halation_core_radius
    assert snapshot.chemistry.frame_long_edge_mm == 36
    geometry = new.metadata['film_process_model']['material_geometry']
    assert geometry['model'] == data_kernel.MATERIAL_GEOMETRY_MODEL_ID
    assert set(geometry) == set(data_kernel.MATERIAL_GEOMETRY_FIELDS)
    assert developed_medium_metadata(new)['film_process_model']['material_geometry'] == geometry


def test_physical_grain_changes_scale_without_changing_formation_or_double_frame_gain():
    config = _physical_config()
    config.enable_mtf = config.enable_halation = False
    config.chemistry.frame_size = '6x7'
    medium = develop_negative(_image(), config, rng=np.random.default_rng(77))
    # A custom geometry with the same extent must not inherit an additional
    # empirical frame-size amplitude/radius adjustment.
    custom = copy.deepcopy(config)
    custom.chemistry.frame_size = '35mm'
    custom.chemistry.frame_long_edge_mm = 69.0
    same = develop_negative(_image(), custom, rng=np.random.default_rng(77))
    np.testing.assert_array_equal(medium.density_grain, same.density_grain)
    smaller = copy.deepcopy(custom)
    smaller.chemistry.frame_long_edge_mm = 36.0
    changed = develop_negative(_image(), smaller, rng=np.random.default_rng(77))
    np.testing.assert_array_equal(medium.density_cmy, changed.density_cmy)
    assert not np.array_equal(medium.density_grain, changed.density_grain)


def test_frozen_latent_keeps_geometry_across_copy_npz_consumption_and_development(tmp_path):
    config = _physical_config()
    config.chemistry.frame_long_edge_mm = 69.0
    latent = expose_image_to_latent(_image(), config)
    geometry = latent.material_geometry
    assert latent.copy().material_geometry is geometry
    path = tmp_path / 'frame.darkroom_latent.npz'
    save_exposed_latent(path, latent, identity=latent_identity_from_film(config.film))
    loaded, metadata = load_exposed_latent(path)
    assert loaded.material_geometry == geometry
    assert metadata['material_geometry'] == geometry.as_dict()
    # A later recipe's frame settings cannot resize the exposed material.
    changed = copy.deepcopy(config)
    changed.film.spatial_reference_short_edge_mm = None
    changed.chemistry.frame_long_edge_mm = 36.0
    live_medium = develop_negative_from_latent(latent, config, rng=np.random.default_rng(99))
    restored_medium = develop_negative_from_latent(loaded, changed, rng=np.random.default_rng(99))
    np.testing.assert_array_equal(live_medium.optical_density_rgb, restored_medium.optical_density_rgb)
    assert restored_medium.metadata['runtime_config'].chemistry.frame_long_edge_mm == 69
    mark_exposed_latent_consumed(path)
    consumed, _ = load_exposed_latent(path, allow_consumed=True)
    assert consumed.material_geometry == geometry


def test_geometry_rejects_resized_or_unknown_model_latents():
    latent = expose_image_to_latent(_image(), _physical_config())
    latent.material_geometry = MaterialGeometry(36, 24, (48, 72))
    with pytest.raises(ValueError, match='dimensions'):
        latent.validate()
    with pytest.raises(ValueError, match='unsupported'):
        MaterialGeometry.from_dict({'model': 'future_model'})


def test_scan_settings_do_not_resize_formed_medium_and_share_preserves_parameters():
    config = _physical_config()
    config.chemistry.frame_long_edge_mm = 69.0
    restored = config_from_share_code(encode_recipe_share(config))
    assert restored.film.spatial_reference_short_edge_mm == 24
    assert restored.chemistry.frame_long_edge_mm == 69
    medium = develop_negative(_image(), restored, rng=np.random.default_rng(31))
    optical = medium.optical_density_rgb.copy()
    geometry = copy.deepcopy(medium.metadata['film_process_model']['material_geometry'])
    scan = copy.deepcopy(config)
    scan.chemistry.frame_long_edge_mm = 24.0
    scan.scanner.transmission_light_ev = 1.0
    scan_medium_output(medium, scan, 'manual')
    np.testing.assert_array_equal(medium.optical_density_rgb, optical)
    assert medium.metadata['film_process_model']['material_geometry'] == geometry


def test_file_workflow_saves_geometry_and_reference_units_for_scan_reload(tmp_path):
    from PIL import Image
    from film_foundry.app.workflow import develop_image_file
    from film_foundry.core.negative_io import load_developed_medium_for_scan

    source = tmp_path / 'input.png'
    Image.fromarray((_image() * 255).astype(np.uint8)).save(source)
    config = _physical_config()
    config.chemistry.frame_size = '6x7'
    config.output.save_scanner_raw = False
    config.output.export_transparent_plate = False
    medium, path, _ = develop_image_file(source, tmp_path / 'formed', config)
    loaded = load_developed_medium_for_scan(path)
    np.testing.assert_array_equal(loaded.optical_density_rgb, medium.optical_density_rgb)
    assert loaded.metadata['film_process_model']['material_geometry'] == medium.metadata['film_process_model']['material_geometry']
    snapshot = loaded.metadata['runtime_config']
    assert snapshot.film.halation_core_radius == config.film.halation_core_radius
    assert snapshot.chemistry.frame_long_edge_mm == 69
