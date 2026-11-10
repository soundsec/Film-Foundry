import numpy as np
import pytest

from film_foundry.core.emulsion_integrity import mix_density
from film_foundry.core.execution_topology import execution_topology


def test_local_coverage_refinement_keeps_endpoints_broadcast_and_dense_results():
    rng = np.random.default_rng(73)
    image = rng.uniform(0., 8., (41, 53, 3)).astype(np.float32)
    image.setflags(write=False)
    for base in (np.array([.1, .3, .2], np.float32), image * .05):
        for fractional in (False, True):
            coverage = np.ones(image.shape[:2], np.float32)
            coverage[7:11, 18:23] = 0.
            coverage[11:14, 18:23] = .47
            if fractional:
                coverage[:] = rng.uniform(0., 1., coverage.shape)
            with execution_topology('reference'):
                expected = mix_density(image, base, coverage)
            actual = mix_density(image, base, coverage)
            np.testing.assert_array_equal(actual, expected)
            assert not np.shares_memory(actual, image)
            np.testing.assert_array_equal(actual[coverage == 1.], image[coverage == 1.])


def test_medium_preview_size_policy_preserves_master_and_full_pack(tmp_path, monkeypatch):
    from film_foundry.core import medium_export
    from film_foundry.core.runtime_validate import _validate_output_runtime
    from film_foundry.core.preview import resize_to_long_edge
    from film_foundry.model.config import DarkroomConfig
    from dataclasses import asdict
    from film_foundry.ui.runtime_settings import SETTINGS
    cfg = DarkroomConfig()
    assert cfg.output.medium_preview_long_edge == 1600
    assert any(key == 'medium_preview_long_edge' for _, key, *_ in SETTINGS)
    image = np.random.default_rng(7).random((117, 169, 3), dtype=np.float32)
    image.setflags(write=False)
    captured = []
    monkeypatch.setattr(medium_export, 'save_image', lambda data, *_, **kwargs: captured.append(data.copy()))
    cfg.output.medium_preview_long_edge = 80
    _validate_output_runtime(cfg)
    assert DarkroomConfig.from_dict(asdict(cfg)).output.medium_preview_long_edge == 80
    medium_export._save_medium_preview(image, tmp_path / 'browse.png', cfg)
    np.testing.assert_array_equal(captured[-1], resize_to_long_edge(image, 80))
    cfg.output.export_layer_pack = True
    medium_export._save_medium_preview(image, tmp_path / 'pack.png', cfg)
    np.testing.assert_array_equal(captured[-1], image)
    cfg.output.export_layer_pack = False
    cfg.output.medium_preview_long_edge = 0
    medium_export._save_medium_preview(image, tmp_path / 'native.png', cfg)
    np.testing.assert_array_equal(captured[-1], image)
    cfg.output.medium_preview_long_edge = -1
    with pytest.raises(ValueError, match='medium_preview_long_edge'):
        _validate_output_runtime(cfg)
