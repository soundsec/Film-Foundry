from pathlib import Path
import zipfile

import numpy as np
import pytest

from film_foundry.core.atomic_io import atomic_savez


def test_fast_npz_keeps_numpy_payloads_and_stored_selection(tmp_path: Path):
    arrays = {
        "optical_density_rgb": np.arange(3 * 7781 * 3, dtype=np.float32).reshape(3, 7781, 3) / 71,
        "zero": np.full((3, 7781, 3), -0.0, dtype=np.float32),
        "developed_medium_metadata": np.asarray('{"label":"测试底片"}'),
        "fortran": np.asfortranarray(np.arange(12, dtype=np.float64).reshape(3, 4)),
    }
    paths = [tmp_path / name for name in ("normal.npz", "fast.npz", "stored.npz")]
    atomic_savez(paths[0], **arrays)
    atomic_savez(paths[1], compression_level=1, **arrays)
    atomic_savez(paths[2], compressed=False, compression_level=1, **arrays)
    with zipfile.ZipFile(paths[0]) as reference:
        for path in paths[1:]:
            with zipfile.ZipFile(path) as actual:
                assert actual.namelist() == reference.namelist()
                for name in reference.namelist():
                    assert actual.read(name) == reference.read(name)
                if path == paths[2]:
                    assert all(item.compress_type == zipfile.ZIP_STORED for item in actual.infolist())
    with np.load(paths[1], allow_pickle=False) as loaded:
        for key, value in arrays.items():
            assert loaded[key].dtype == value.dtype
            assert loaded[key].tobytes() == value.tobytes()


def test_fast_npz_failed_write_preserves_previous_target(tmp_path: Path, monkeypatch):
    target = tmp_path / "existing.npz"
    target.write_bytes(b"original")

    def fail_after_start(output, *args, **kwargs):
        output.write(b"partial")
        raise OSError("write failed")

    monkeypatch.setattr(np.lib.format, "write_array", fail_after_start)
    with pytest.raises(OSError, match="write failed"):
        atomic_savez(target, compression_level=1, image=np.zeros(3))
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]
    with pytest.raises(ValueError, match="compression level"):
        atomic_savez(target, compression_level=10, image=np.zeros(3))
    assert target.read_bytes() == b"original"
