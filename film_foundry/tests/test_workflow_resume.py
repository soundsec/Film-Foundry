from pathlib import Path
import ast
import json
import zipfile

import numpy as np
from PIL import Image

from film_foundry.app import resume
from film_foundry.app.resume import (
    complete_developed_archive,
    complete_image_file,
    complete_latent_archive,
)
from film_foundry.app.workflow import developed_path_for, planned_developed_path
from film_foundry.core.latent_io import load_exposed_latent, mark_exposed_latent_consumed
from film_foundry.core.states import DevelopedNegative
from film_foundry.model.config import DarkroomConfig
from film_foundry.app.api import WorkflowRequest, run_request


def test_resume_module_does_not_import_ui():
    source = Path(resume.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        name == "film_foundry.ui" or name.startswith("film_foundry.ui.")
        for name in imported
    )


def test_complete_image_file_rejects_missing_and_empty(tmp_path):
    missing = tmp_path / "out.png"
    assert complete_image_file(missing) is False
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert complete_image_file(empty) is False
    filled = tmp_path / "ok.png"
    filled.write_bytes(b"not empty")
    assert complete_image_file(filled) is True


def test_complete_latent_archive_accepts_consumed_and_rejects_garbage(tmp_path):
    image = np.linspace(0.12, 0.86, 6 * 8 * 3, dtype=np.float32).reshape(6, 8, 3)
    source = tmp_path / "frame.png"
    Image.fromarray(np.round(image * 255.0).astype(np.uint8), mode="RGB").save(source)
    config = DarkroomConfig()
    config.enable_grain = False
    config.save_sidecar = False
    config.output.save_scanner_raw = False
    latent = tmp_path / "frame.darkroom_latent.npz"
    run_request(
        WorkflowRequest(
            command="expose",
            input_path=source,
            output_path=latent,
            config=config,
        )
    )
    assert complete_latent_archive(latent) is True
    mark_exposed_latent_consumed(latent)
    assert complete_latent_archive(latent) is True
    load_exposed_latent(latent, allow_consumed=True)

    garbage = tmp_path / "bad.darkroom_latent.npz"
    garbage.write_bytes(b"not a zip")
    assert complete_latent_archive(garbage) is False
    empty = tmp_path / "empty.darkroom_latent.npz"
    empty.write_bytes(b"")
    assert complete_latent_archive(empty) is False
    truncated = tmp_path / "trunc.darkroom_latent.npz"
    with zipfile.ZipFile(truncated, "w") as archive:
        archive.writestr("readme.txt", "not a latent")
    assert complete_latent_archive(truncated) is False


def test_complete_developed_archive_uses_scan_ingest(tmp_path):
    density = np.linspace(0.2, 1.4, 4 * 5 * 3, dtype=np.float32).reshape(4, 5, 3)
    path = tmp_path / "medium.darkroom_negative.npz"
    np.savez_compressed(
        path,
        density_cmy=density,
        density_grain=density,
        developed_medium_metadata=json.dumps(
            {
                "medium_family": "silver_halide_film",
                "medium_process": "negative",
                "image_polarity": "negative",
                "view_mode": "transmission",
                "base_type": "orange_mask",
                "color_system": "dye",
            }
        ),
    )
    assert complete_developed_archive(path) is True
    garbage = tmp_path / "bad.darkroom_negative.npz"
    garbage.write_bytes(b"not a zip")
    assert complete_developed_archive(garbage) is False


def test_planned_developed_path_matches_medium_path(tmp_path):
    medium = DevelopedNegative(
        linear_input=np.empty((0, 0, 3), dtype=np.float32),
        after_mtf=np.empty((0, 0, 3), dtype=np.float32),
        after_halation=np.empty((0, 0, 3), dtype=np.float32),
        density_cmy=np.zeros((2, 3, 3), dtype=np.float32),
        density_grain=np.zeros((2, 3, 3), dtype=np.float32),
        image_polarity="negative",
    )
    input_path = tmp_path / "frame.png"
    output_root = tmp_path / "out"
    assert planned_developed_path(
        input_path,
        output_root,
        image_polarity="negative",
    ) == developed_path_for(input_path, output_root, medium)
    file_target = tmp_path / "one.darkroom_negative.npz"
    assert planned_developed_path(
        input_path,
        file_target,
        image_polarity="positive",
    ) == file_target
