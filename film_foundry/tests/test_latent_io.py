from __future__ import annotations

from pathlib import Path
import json

import numpy as np
import pytest

from film_foundry.contracts import data_kernel
from film_foundry.core.film_process import (
    apply_process_program,
    freeze_material_latent_state,
    program_from_develop_recipe,
)
from film_foundry.core.film_process.integration import (
    _color_material,
    _compatibility_for_program,
)
from film_foundry.core.latent_io import (
    LATENT_KIND,
    LATENT_STAGE,
    LATENT_SUFFIX,
    load_exposed_latent,
    latent_identity_from_film,
    save_exposed_latent,
)
from film_foundry.model.config import DarkroomConfig


def test_latent_catalog_matches_io_module():
    assert data_kernel.LATENT_SUFFIX == LATENT_SUFFIX
    assert data_kernel.LATENT_KIND == LATENT_KIND
    assert data_kernel.LATENT_STAGE == LATENT_STAGE
    assert data_kernel.LATENT_REQUIRED_ARRAY_KEYS == (
        "halide",
        "developability",
        "metallic_silver",
    )
    assert data_kernel.phone_latent_ingest_npz_keys()[:3] == (
        "halide",
        "developability",
        "metallic_silver",
    )


def test_save_does_not_mutate_or_consume_latent_state(tmp_path):
    config = DarkroomConfig()
    film = config.film
    material = _color_material(film)
    image = np.linspace(0.04, 0.92, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    state = freeze_material_latent_state(image, film, material)
    before = np.array(state.developability, copy=True)
    path = tmp_path / f"frame{LATENT_SUFFIX}"

    written = save_exposed_latent(path, state, identity=latent_identity_from_film(film))
    loaded, metadata = load_exposed_latent(written)

    np.testing.assert_array_equal(state.developability, before)
    np.testing.assert_array_equal(loaded.developability, before)
    np.testing.assert_array_equal(loaded.halide, state.halide)
    assert metadata["kind"] == LATENT_KIND
    assert metadata["stage"] == LATENT_STAGE
    assert metadata["consumed"] is False
    assert metadata["image_polarity"] == "negative"
    assert "frame_identity" not in metadata
    assert loaded.coupler is not None
    assert loaded.metallic_silver.shape == before.shape


def test_loaded_latent_process_matches_in_memory_process(tmp_path):
    config = DarkroomConfig()
    film = config.film
    recipe = config.chemistry
    material = _color_material(film)
    image = np.linspace(0.05, 0.88, 5 * 7 * 3, dtype=np.float32).reshape(5, 7, 3)
    live = freeze_material_latent_state(image, film, material)
    path = tmp_path / f"frame{LATENT_SUFFIX}"
    save_exposed_latent(path, live, identity=latent_identity_from_film(film))
    restored, _metadata = load_exposed_latent(path)

    program = program_from_develop_recipe(
        recipe,
        mode="color_negative",
        material_process="negative",
        layer_count=3,
    )
    compatibility = _compatibility_for_program(film, program.key, 3)
    live_result = apply_process_program(
        material,
        live,
        program,
        compatibility,
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
        validate_each_step=False,
    )
    loaded_result = apply_process_program(
        material,
        restored,
        program,
        compatibility,
        consume_latent_state=True,
        transfer_final_pool_ownership=True,
        validate_each_step=False,
    )

    np.testing.assert_array_equal(
        live_result.final_medium.metallic_silver,
        loaded_result.final_medium.metallic_silver,
    )
    np.testing.assert_array_equal(
        live_result.final_medium.dye,
        loaded_result.final_medium.dye,
    )
    np.testing.assert_array_equal(
        live_result.final_medium.residual_halide,
        loaded_result.final_medium.residual_halide,
    )


def test_load_exposed_latent_rejects_developed_npz(tmp_path):
    path = tmp_path / "frame.darkroom_negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=np.asarray(json.dumps({"image_polarity": "negative"})),
    )
    with pytest.raises(ValueError, match="已形成介质"):
        load_exposed_latent(path)


def test_mark_consumed_rejects_fresh_reload(tmp_path):
    from film_foundry.core.latent_io import mark_exposed_latent_consumed

    config = DarkroomConfig()
    film = config.film
    material = _color_material(film)
    image = np.linspace(0.1, 0.8, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    state = freeze_material_latent_state(image, film, material)
    path = tmp_path / f"frame{LATENT_SUFFIX}"
    save_exposed_latent(path, state, identity=latent_identity_from_film(film))
    mark_exposed_latent_consumed(path)

    with pytest.raises(ValueError, match="已消耗"):
        load_exposed_latent(path)
    loaded, metadata = load_exposed_latent(path, allow_consumed=True)
    assert metadata["consumed"] is True
    np.testing.assert_array_equal(loaded.developability, state.developability)


def test_mark_consumed_preserves_frame_identity(tmp_path):
    from film_foundry.core.latent_io import mark_exposed_latent_consumed

    config = DarkroomConfig()
    film = config.film
    material = _color_material(film)
    image = np.linspace(0.1, 0.8, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    state = freeze_material_latent_state(image, film, material)
    path = tmp_path / f"frame{LATENT_SUFFIX}"
    identity = latent_identity_from_film(film)
    identity["frame_identity"] = {
        "roll_id": "roll-a",
        "frame_id": "A12",
        "frame_index": 12,
    }
    save_exposed_latent(path, state, identity=identity)
    mark_exposed_latent_consumed(path)
    _loaded, metadata = load_exposed_latent(path, allow_consumed=True)
    assert metadata["consumed"] is True
    assert metadata["frame_identity"] == {
        "roll_id": "roll-a",
        "frame_id": "A12",
        "frame_index": 12,
    }


def test_reduced_develop_from_exposed_latent_matches_image_path():
    from film_foundry.core.film_process import develop_color_negative_reduced

    config = DarkroomConfig()
    film = config.film
    recipe = config.chemistry
    material = _color_material(film)
    image = np.linspace(0.06, 0.9, 6 * 5 * 3, dtype=np.float32).reshape(6, 5, 3)
    frozen = freeze_material_latent_state(image, film, material)
    from_image = develop_color_negative_reduced(image, film, recipe)
    from_latent = develop_color_negative_reduced(
        None,
        film,
        recipe,
        exposed_latent=frozen,
    )
    np.testing.assert_array_equal(from_image.density_cmy, from_latent.density_cmy)
    np.testing.assert_array_equal(
        from_image.optical_density_rgb,
        from_latent.optical_density_rgb,
    )
