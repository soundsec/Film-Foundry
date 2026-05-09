import numpy as np

from half_frame_darkroom.core.engine import develop_negative, process_array, scan_negative
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
