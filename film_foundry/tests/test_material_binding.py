import copy
import json

import numpy as np
import pytest

from film_foundry.contracts.material_snapshot import MaterialSnapshot
from film_foundry.contracts.data_kernel import MATERIAL_SNAPSHOT_KEY
from film_foundry.core.engine import expose_image_to_latent, develop_negative_from_latent
from film_foundry.core.latent_io import (
    save_exposed_latent, load_exposed_latent, latent_identity_from_film, mark_exposed_latent_consumed,
)
from film_foundry.model.config import DarkroomConfig


def source():
    cfg = DarkroomConfig(enable_mtf=False, enable_halation=False, enable_grain=False)
    cfg.film.name = "Frozen material"
    cfg.film.film_base_density_rgb = (.17, .51, .82)
    cfg.chemistry.program_key = "color_negative"
    image = np.linspace(.02, .92, 45, dtype=np.float32).reshape(3, 5, 3)
    return cfg, expose_image_to_latent(image, cfg)


def test_film_is_bound_but_baths_remain_editable_and_caller_config_stays_unchanged():
    cfg, latent = source()
    before = latent.material_snapshot.as_dict()
    changed = copy.deepcopy(cfg)
    changed.film.name = "Other stock"
    changed.film.image_polarity = "positive"
    changed.film.color_process = "monochrome"
    changed.film.film_base_density_rgb = (1., 1., 1.)
    changed.film.dye_absorption_matrix = ((2., 0., 0.), (0., 2., 0.), (0., 0., 2.))
    changed.film.density_max = (4., 4., 4.)
    changed.film.cross_process_dye_coupling = .1
    for program in ("color_negative", "color_reversal", "bw_negative"):
        cfg.chemistry.program_key = changed.chemistry.program_key = program
        expected = develop_negative_from_latent(latent.copy(), cfg, rng=np.random.default_rng(23))
        actual = develop_negative_from_latent(latent.copy(), changed, rng=np.random.default_rng(23))
        np.testing.assert_array_equal(actual.optical_density_rgb, expected.optical_density_rgb)
        assert actual.metadata["material_binding"]["source"] == "latent_snapshot"
        assert actual.metadata["runtime_config"].film.name == "Frozen material"
    assert changed.film.name == "Other stock"
    assert before == latent.material_snapshot.as_dict()
    assert latent.copy().material_snapshot is latent.material_snapshot


def test_snapshot_survives_archive_consumption_and_overrules_wrong_save_identity(tmp_path):
    cfg, latent = source()
    path = tmp_path / "bound.darkroom_latent.npz"
    wrong = copy.deepcopy(cfg.film)
    wrong.image_polarity = "positive"
    save_exposed_latent(path, latent, identity=latent_identity_from_film(wrong))
    loaded, meta = load_exposed_latent(path)
    assert meta["image_polarity"] == "negative"
    assert meta[MATERIAL_SNAPSHOT_KEY] == latent.material_snapshot.as_dict()
    mark_exposed_latent_consumed(path)
    consumed, after = load_exposed_latent(path, allow_consumed=True)
    assert after["consumed"] is True
    assert consumed.material_snapshot == loaded.material_snapshot


def test_legacy_latent_uses_explicit_caller_material_and_records_the_fallback():
    cfg, latent = source()
    latent.material_snapshot = None
    a = develop_negative_from_latent(latent.copy(), cfg)
    changed = copy.deepcopy(cfg)
    changed.film.film_base_density_rgb = (.5, .5, .5)
    b = develop_negative_from_latent(latent.copy(), changed)
    assert a.metadata["material_binding"]["source"] == "caller_legacy"
    assert np.max(np.abs(a.optical_density_rgb-b.optical_density_rgb)) > .01


def test_snapshot_is_detached_immutable_and_rejects_invalid_values_or_versions():
    film = {"name": "A", "film_base_density_rgb": [.1, .2, .3]}
    snapshot = MaterialSnapshot(film)
    film["film_base_density_rgb"][0] = 9
    assert snapshot.film["film_base_density_rgb"][0] == .1
    assert MaterialSnapshot({"float": np.float32(.5), "integer": np.int64(2)}).parameters() == {"float": .5, "integer": 2}
    with pytest.raises(TypeError):
        snapshot.film["name"] = "B"
    for bad in ({"model": "future", "film": {}}, {"model": "film_material_parameters_v1", "film": None},
                {"model": "film_material_parameters_v1", "film": {"x": float("nan")}}):
        with pytest.raises(ValueError):
            MaterialSnapshot.from_value(bad)


def test_runtime_look_strength_is_not_applied_again_after_loading_material_snapshot():
    cfg = DarkroomConfig(enable_mtf=False, enable_halation=False, enable_grain=False)
    cfg.look.look_strength = .65
    cfg.look.saturation_multiplier = .8
    latent = expose_image_to_latent(np.full((2, 3, 3), .3, np.float32), cfg)
    a = develop_negative_from_latent(latent.copy(), cfg)
    changed = copy.deepcopy(cfg)
    changed.look.look_strength = 1.
    changed.look.saturation_multiplier = 1.4
    b = develop_negative_from_latent(latent.copy(), changed)
    np.testing.assert_array_equal(a.optical_density_rgb, b.optical_density_rgb)


def test_reference_material_units_are_not_frozen_after_geometry_projection():
    cfg = DarkroomConfig(enable_mtf=False, enable_halation=False, enable_grain=False)
    cfg.film.spatial_reference_short_edge_mm = 24.
    cfg.chemistry.frame_long_edge_mm = 69.
    latent = expose_image_to_latent(np.full((3, 5, 3), .3, np.float32), cfg)
    assert latent.material_snapshot.film["emulsion_blur_radius"] == cfg.film.emulsion_blur_radius


def test_unknown_snapshot_in_npz_is_rejected_instead_of_using_caller_material(tmp_path):
    cfg, latent = source()
    path = tmp_path / "bad.darkroom_latent.npz"
    save_exposed_latent(path, latent, identity=latent_identity_from_film(cfg.film))
    with np.load(path, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    metadata = json.loads(str(arrays["latent_medium_metadata"].item()))
    metadata[MATERIAL_SNAPSHOT_KEY]["model"] = "future"
    arrays["latent_medium_metadata"] = np.asarray(json.dumps(metadata))
    np.savez(path, **arrays)
    with pytest.raises(ValueError, match="snapshot"):
        load_exposed_latent(path)
