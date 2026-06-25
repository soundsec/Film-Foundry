import numpy as np
import pytest

from half_frame_darkroom.core.negative_io import load_negative_density_arrays


def test_numeric_negative_npz_loads_without_pickle(tmp_path):
    path = tmp_path / "negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    np.savez_compressed(path, density_cmy=density, density_grain=density + 0.1)

    density_cmy, density_grain = load_negative_density_arrays(path)

    assert density_cmy.shape == (2, 3, 3)
    assert density_grain.shape == (2, 3, 3)
    assert density_grain.dtype == np.float32


def test_legacy_object_negative_npz_is_rejected_by_default(tmp_path):
    path = tmp_path / "legacy_negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    legacy = {"density_cmy": density, "density_grain": density}
    np.savez_compressed(path, legacy=np.array(legacy, dtype=object))

    with pytest.raises(ValueError, match="does not load pickled"):
        load_negative_density_arrays(path)


def test_legacy_object_negative_npz_can_be_loaded_when_explicitly_trusted(tmp_path):
    path = tmp_path / "legacy_negative.npz"
    density = np.zeros((2, 3, 3), dtype=np.float32)
    legacy = {"density_cmy": density, "density_grain": density + 0.2}
    np.savez_compressed(path, legacy=np.array(legacy, dtype=object))

    density_cmy, density_grain = load_negative_density_arrays(path, allow_legacy_pickle=True)

    assert density_cmy.shape == (2, 3, 3)
    assert np.allclose(density_grain, 0.2)
