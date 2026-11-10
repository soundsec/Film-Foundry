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

    from film_foundry.core import atomic_io
    monkeypatch.setattr(atomic_io, "_write_npz_array", fail_after_start)
    with pytest.raises(OSError, match="write failed"):
        atomic_savez(target, compression_level=1, image=np.zeros(3))
    assert target.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [target]
    with pytest.raises(ValueError, match="compression level"):
        atomic_savez(target, compression_level=10, image=np.zeros(3))
    assert target.read_bytes() == b"original"


def test_parallel_deflate_preserves_npy_bytes_and_falls_back(tmp_path, monkeypatch):
    from film_foundry.core import parallel_deflate as parallel
    monkeypatch.setenv('FILM_FOUNDRY_PARALLEL_NPZ', '1')
    monkeypatch.setattr(parallel, 'MIN_ARRAY_BYTES', 1)
    monkeypatch.setattr(parallel, 'BLOCK_BYTES', 1024)
    monkeypatch.setattr(parallel.os, 'cpu_count', lambda: 4)
    arrays = {'rgb': np.random.default_rng(7).normal(size=(128,129,3)).astype(np.float32),
              'fortran': np.asfortranarray(np.arange(12000).reshape(100,120)),
              'view': np.arange(65536, dtype=np.float32)[::3],
              'zero': np.zeros((200,30),np.float16), 'metadata': np.asarray('测试')}
    serial, fast, fallback = [tmp_path/name for name in ('serial.npz','fast.npz','fallback.npz')]
    atomic_savez(serial, compression_level=1, **arrays)
    atomic_savez(fast, compression_level=1, parallel_compression=True, compression_workers=4, **arrays)
    calls = []
    def fail(*args):
        calls.append(1)
        raise RuntimeError('worker unavailable')
    monkeypatch.setattr(parallel, '_block', fail)
    atomic_savez(fallback, compression_level=1, parallel_compression=True, **arrays)
    assert calls
    with zipfile.ZipFile(serial) as baseline:
        for path in (fast, fallback):
            with zipfile.ZipFile(path) as actual:
                assert actual.namelist() == baseline.namelist()
                for name in actual.namelist():
                    assert actual.read(name) == baseline.read(name)
            with np.load(path, allow_pickle=False) as loaded:
                for key, value in arrays.items():
                    np.testing.assert_array_equal(loaded[key], value)


def test_parallel_compression_is_optional_and_single_archive_bounded(monkeypatch):
    from film_foundry.core import parallel_deflate as parallel
    from film_foundry.core.execution_topology import execution_topology
    monkeypatch.setenv('FILM_FOUNDRY_PARALLEL_NPZ', '1')
    monkeypatch.setattr(parallel.os, 'cpu_count', lambda: 4)
    with parallel.compression_pool(True) as first:
        assert first is not None
        with parallel.compression_pool(True) as second:
            assert second is None
    with parallel.compression_pool(True, workers=1) as pool:
        assert pool is None
    with execution_topology('reference'), parallel.compression_pool(True) as pool:
        assert pool is None
    monkeypatch.setenv('FILM_FOUNDRY_PARALLEL_NPZ', '0')
    with parallel.compression_pool(True) as pool:
        assert pool is None
