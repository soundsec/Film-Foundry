from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from film_foundry.core.color import linear_to_srgb, luminance
from film_foundry.core.execution_topology import REFERENCE_TOPOLOGY, execution_topology
from film_foundry.core.scan_output import ScanOutputPlan
from film_foundry.core.scan_runtime import _normalize_encode_output_tiled
from film_foundry.core.scanner import apply_scan_normalization_range, scan_normalization_range
from film_foundry.model.config import DarkroomConfig, ScannerConfig


def old_tone(image, curve):
    # Frozen pre-plan output mapping, including lifted black and out-of-gamut y.
    if curve is None or not any(abs(y - x) > 1e-7 for x, y in curve):
        return image
    output = image.copy()
    brightness = luminance(output)
    target = np.interp(brightness, [x for x, _ in curve], [y for _, y in curve]).astype(np.float32)
    black = brightness <= 1e-8
    ratio = np.divide(target, brightness, out=np.zeros_like(target), where=~black)
    output *= ratio[..., None]
    output[black] = target[black, None]
    np.clip(output, 0., 1., out=output)
    return output


@pytest.mark.parametrize("mode,strength,flat", [
    ("rgb", 1., False), ("luma", .65, False), ("rgb", 0., False), ("luma", 1., True),
])
@pytest.mark.parametrize("curve", [None, ((0., .08), (.3, .21), (1., 1.15))])
def test_plan_matches_separate_cpu_steps_and_partitions_exactly(mode, strength, flat, curve):
    image = np.linspace(-.05, 1.1, 19 * 23 * 3, dtype=np.float32).reshape(19, 23, 3)
    image[0, :3] = (0., .0031308, .00313081)
    if flat:
        image.fill(.42)
    original = image.copy()
    scanner = ScannerConfig(scan_normalize=True, scan_normalize_mode=mode,
                            scan_normalize_strength=strength, tone_curve=curve)
    black, white = scan_normalization_range(image, mode=mode)
    plan = ScanOutputPlan.prepare(scanner, black=black, white=white)
    normalized = apply_scan_normalization_range(image, black, white, strength=strength, mode=mode)
    expected = old_tone(linear_to_srgb(normalized), curve)
    image.setflags(write=False)
    np.testing.assert_array_equal(plan.apply_cpu(image, consume_input=True), expected)
    for rows in (1, 7, 19):
        owned = image.copy()
        for start in range(0, len(image), rows):
            block = owned[start:start + rows]
            output = plan.apply_cpu(block, consume_input=True)
            assert np.shares_memory(output, block)
        np.testing.assert_array_equal(owned, expected)
    borrowed = image[:, ::2]
    np.testing.assert_array_equal(plan.apply_cpu(borrowed), expected[:, ::2])
    np.testing.assert_array_equal(image, original)


def test_plan_freezes_parameters_without_retaining_mutable_config_or_statistics():
    curve = [[0., .1], [1., .9]]
    scanner = ScannerConfig(scan_normalize=True, tone_curve=curve)
    black = np.array([.1, .2, .3], dtype=np.float32)
    white = np.array([.8, .9, 1.], dtype=np.float32)
    plan = ScanOutputPlan.prepare(scanner, black=black, white=white)
    image = np.linspace(0., 1., 60, dtype=np.float32).reshape(4, 5, 3)
    expected = plan.apply_cpu(image)
    black.fill(99); white.fill(99); curve[0][1] = .9
    scanner.tone_curve = None; scanner.scan_normalize_strength = 0
    np.testing.assert_array_equal(plan.apply_cpu(image), expected)
    with pytest.raises(FrozenInstanceError):
        plan.strength = 0
    with pytest.raises(ValueError, match="full-frame"):
        ScanOutputPlan.prepare(ScannerConfig(scan_normalize=True))


def test_integrated_output_uses_global_range_before_modifying_any_block(monkeypatch):
    # Production enters through engine, which composes scan_runtime's services.
    from film_foundry.core import engine  # noqa: F401
    from film_foundry.core import scan_runtime
    image = np.zeros((24, 31, 3), dtype=np.float32)
    image[:8] = (.01, .03, .7)
    image[8:16] = (.9, .08, .04)
    image[16:] = (.06, .8, .1)
    config = DarkroomConfig()
    config.scanner.scan_normalize = True
    config.scanner.scan_normalize_mode = "rgb"
    config.scanner.tone_curve = ((0., .04), (.5, .6), (1., 1.))
    with execution_topology(REFERENCE_TOPOLOGY):
        expected = _normalize_encode_output_tiled(image.copy(), config, 5)
    original_range = scan_runtime.scan_normalization_range
    calls = []

    def measure(values, **kwargs):
        np.testing.assert_array_equal(values, image)
        calls.append(values.shape)
        return original_range(values, **kwargs)

    monkeypatch.setattr(scan_runtime, "scan_normalization_range", measure)
    actual = _normalize_encode_output_tiled(image.copy(), config, 5)
    assert calls == [image.shape]
    np.testing.assert_array_equal(actual, expected)
