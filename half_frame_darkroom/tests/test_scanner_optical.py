import numpy as np

from half_frame_darkroom.core.scanner import (
    balance_negative_base,
    invert_negative_image,
    negative_total_density_rgb,
    negative_transmittance_rgb,
    render_negative_image,
    render_positive_scan,
    scan_negative_raw,
    scanner_raw_to_positive_rgb,
)
from half_frame_darkroom.model.config import FilmStockConfig, ScannerConfig


def test_transmittance_decreases_with_density():
    film = FilmStockConfig()
    low = np.full((1, 1, 3), 0.2, dtype=np.float32)
    high = np.full((1, 1, 3), 1.0, dtype=np.float32)

    low_t = negative_transmittance_rgb(low, film)
    high_t = negative_transmittance_rgb(high, film)

    assert np.all(high_t < low_t)


def test_scanner_raw_and_positive_are_displayable():
    film = FilmStockConfig()
    scanner = ScannerConfig()
    density = np.full((4, 4, 3), 0.8, dtype=np.float32)

    total_density = negative_total_density_rgb(density, film)
    raw = scan_negative_raw(density, film)
    negative_linear = render_negative_image(density, film)
    balanced = balance_negative_base(negative_linear)
    raw_positive = invert_negative_image(balanced)
    rendered = render_positive_scan(raw_positive, scanner)
    positive = scanner_raw_to_positive_rgb(raw, scanner)

    assert total_density.shape == density.shape
    assert raw.shape == density.shape
    assert negative_linear.shape == density.shape
    assert balanced.shape == density.shape
    assert raw_positive.shape == density.shape
    assert rendered.shape == density.shape
    assert positive.shape == density.shape
    assert raw.min() > 0.0
    assert positive.min() >= 0.0
    assert positive.max() <= 1.0
