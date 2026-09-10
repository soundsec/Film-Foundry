import cv2
import numpy as np
import pytest
import film_foundry.core.mtf as mtf_module

from film_foundry.core.color import luminance
from film_foundry.core.halation import radius_to_sigma
from film_foundry.core.mtf import (
    FULL_EMULSION_MTF_MODEL,
    REDUCED_EMULSION_MTF_MODEL,
    _above_threshold_mask,
    _robust_normalize,
    apply_emulsion_mtf,
    local_frequency_mask,
)
from film_foundry.core.execution_topology import (
    REFERENCE_TOPOLOGY,
    execution_topology,
)
from film_foundry.model.config import FilmStockConfig


def _frozen_mtf_reference(image: np.ndarray, film: FilmStockConfig) -> np.ndarray:
    """Pre-lifecycle-optimization formula; operation order is intentional."""
    strength = float(np.clip(film.emulsion_mtf_strength, 0.0, 1.0))
    artifact_strength = float(
        np.clip(film.digital_artifact_suppression, 0.0, 1.0)
    )
    sigma = radius_to_sigma(film.emulsion_blur_radius, image.shape)
    emulsion_blur = cv2.GaussianBlur(
        image,
        (0, 0),
        sigmaX=sigma,
        sigmaY=sigma,
    )
    stronger_blur = cv2.GaussianBlur(
        image,
        (0, 0),
        sigmaX=sigma * 1.8,
        sigmaY=sigma * 1.8,
    )
    y = luminance(image)
    low = cv2.GaussianBlur(y, (0, 0), sigmaX=sigma, sigmaY=sigma)
    highpass = np.abs(y - low)
    grad_x = cv2.Sobel(y, cv2.CV_32F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(y, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(grad_x, grad_y)
    frequency_mask = _above_threshold_mask(
        _robust_normalize(highpass + 0.35 * gradient),
        film.high_frequency_threshold,
        0.32,
    )[..., None]
    mtf_blend = np.clip(frequency_mask * strength, 0.0, 1.0)
    artifact_blend = np.clip(
        frequency_mask * artifact_strength * 0.55,
        0.0,
        1.0,
    )
    out = image * (1.0 - mtf_blend) + emulsion_blur * mtf_blend
    out = out * (1.0 - artifact_blend) + stronger_blur * artifact_blend
    return np.clip(out, 0.0, 4.0).astype(np.float32)


def test_emulsion_mtf_keeps_flat_fields_stable():
    image = np.full((32, 32, 3), 0.45, dtype=np.float32)
    film = FilmStockConfig(emulsion_mtf_strength=0.8, digital_artifact_suppression=0.8)
    out = apply_emulsion_mtf(image, film)
    assert np.allclose(out, image, atol=1e-5)


def test_emulsion_mtf_reduces_single_pixel_frequency():
    checker = (np.indices((32, 32)).sum(axis=0) % 2).astype(np.float32)
    image = np.repeat(checker[..., None], 3, axis=-1)
    film = FilmStockConfig(
        emulsion_mtf_strength=0.9,
        emulsion_blur_radius=0.004,
        digital_artifact_suppression=0.4,
    )
    out = apply_emulsion_mtf(image, film)
    assert out.std() < image.std()


def test_local_frequency_mask_is_exact_across_allocation_alignments():
    image = np.linspace(0.002, 0.91, 20 * 28 * 3, dtype=np.float32).reshape(20, 28, 3)
    film = FilmStockConfig(
        emulsion_mtf_strength=0.25,
        emulsion_blur_radius=0.0025,
        digital_artifact_suppression=0.35,
        high_frequency_threshold=0.12,
    )
    expected = local_frequency_mask(image, film)

    for byte_offset in range(0, 64, 4):
        storage = np.empty(image.nbytes + 64, dtype=np.uint8)
        aligned_offset = (-storage.ctypes.data + byte_offset) % 64
        varied = storage[
            aligned_offset:aligned_offset + image.nbytes
        ].view(np.float32).reshape(image.shape)
        varied[...] = image
        actual = local_frequency_mask(varied, film)
        assert np.array_equal(actual, expected)


def test_emulsion_mtf_lifecycle_optimization_is_float32_equivalent_and_non_mutating():
    image = np.linspace(0.01, 0.99, 37 * 53 * 3, dtype=np.float32).reshape(37, 53, 3)
    image[..., 1] = np.roll(image[..., 1], 7, axis=1)
    film = FilmStockConfig(
        emulsion_mtf_strength=0.73,
        emulsion_blur_radius=0.0031,
        digital_artifact_suppression=0.61,
        high_frequency_threshold=0.18,
    )
    original = image.copy()
    expected = _frozen_mtf_reference(image, film)

    public_output = apply_emulsion_mtf(image, film)
    # OpenCV may select a different SIMD alignment when the two RGB blurs are
    # allocated after the scalar mask instead of before it.  The formulas and
    # float32 operation order are unchanged, but that native alignment can
    # move an isolated result by one float32 ULP.  Keep the boundary explicit
    # instead of using a broad visual tolerance.
    np.testing.assert_allclose(
        public_output,
        expected,
        rtol=0.0,
        atol=np.finfo(np.float32).eps,
    )
    assert np.array_equal(image, original)
    assert not np.shares_memory(public_output, image)

    private_input = image.copy()
    consumed_output = apply_emulsion_mtf(
        private_input,
        film,
        consume_input=True,
    )
    np.testing.assert_allclose(
        consumed_output,
        expected,
        rtol=0.0,
        atol=np.finfo(np.float32).eps,
    )
    np.testing.assert_allclose(
        consumed_output,
        public_output,
        rtol=0.0,
        atol=np.finfo(np.float32).eps,
    )
    assert np.shares_memory(consumed_output, private_input)


def test_large_channelwise_mtf_is_exactly_the_reference_rgb_topology():
    image = np.random.default_rng(43).random(
        (1000, 1000, 3),
        dtype=np.float32,
    )
    original = image.copy()
    film = FilmStockConfig(
        emulsion_mtf_strength=0.73,
        emulsion_blur_radius=0.0031,
        digital_artifact_suppression=0.61,
        high_frequency_threshold=0.18,
    )
    optimized_input = image.copy()
    optimized = apply_emulsion_mtf(
        optimized_input,
        film,
        consume_input=True,
    )
    with execution_topology(REFERENCE_TOPOLOGY):
        reference = apply_emulsion_mtf(
            image,
            film,
        )

    assert optimized is optimized_input
    assert np.array_equal(optimized, reference)
    assert np.array_equal(image, original)


def test_reduced_mtf_keeps_frequency_gate_and_uses_one_spatial_response(monkeypatch):
    y, x = np.mgrid[:96, :144]
    image = np.empty((96, 144, 3), dtype=np.float32)
    image[..., 0] = np.where(x < 72, 0.12, 0.88)
    image[..., 1] = 0.5 + 0.35 * np.sin(x * 0.73)
    image[..., 2] = 0.5 + 0.30 * np.sin(x * 0.17) * np.cos(y * 0.31)
    image = np.clip(image, 0.0, 1.0).astype(np.float32)
    original = image.copy()
    film = FilmStockConfig(
        emulsion_mtf_strength=0.42,
        emulsion_blur_radius=0.012,
        digital_artifact_suppression=0.28,
        high_frequency_threshold=0.026,
    )

    original_blur = mtf_module.cv2.GaussianBlur
    calls: list[float] = []

    def record_blur(*args, sigmaX, sigmaY, **kwargs):
        calls.append(float(sigmaX))
        return original_blur(*args, sigmaX=sigmaX, sigmaY=sigmaY, **kwargs)

    monkeypatch.setattr(mtf_module.cv2, "GaussianBlur", record_blur)
    full = apply_emulsion_mtf(image, film, model=FULL_EMULSION_MTF_MODEL)
    full_calls = len(calls)
    calls.clear()
    reduced = apply_emulsion_mtf(image, film, model=REDUCED_EMULSION_MTF_MODEL)

    assert full_calls == 3  # scalar frequency gate + two canonical RGB PSFs
    assert len(calls) == 2  # same gate + one moment-matched RGB PSF
    assert np.array_equal(image, original)
    assert not np.shares_memory(reduced, image)
    assert np.all(np.isfinite(reduced))
    assert reduced[..., 1].std() < image[..., 1].std()
    full_change = full - image
    reduced_change = reduced - image
    assert np.corrcoef(full_change.ravel(), reduced_change.ravel())[0, 1] > 0.98


def test_reduced_mtf_keeps_flat_field_and_private_consumption_contract():
    flat = np.full((40, 52, 3), 0.37, dtype=np.float32)
    film = FilmStockConfig(
        emulsion_mtf_strength=0.9,
        emulsion_blur_radius=0.01,
        digital_artifact_suppression=0.8,
    )
    public = apply_emulsion_mtf(
        flat,
        film,
        model=REDUCED_EMULSION_MTF_MODEL,
    )
    private = flat.copy()
    consumed = apply_emulsion_mtf(
        private,
        film,
        consume_input=True,
        model=REDUCED_EMULSION_MTF_MODEL,
    )

    np.testing.assert_allclose(public, flat, rtol=0.0, atol=1e-6)
    np.testing.assert_allclose(consumed, flat, rtol=0.0, atol=1e-6)
    assert np.shares_memory(consumed, private)


def test_emulsion_mtf_models_fail_closed_when_unknown():
    with pytest.raises(ValueError, match="unsupported emulsion MTF model"):
        apply_emulsion_mtf(
            np.full((8, 10, 3), 0.4, dtype=np.float32),
            FilmStockConfig(),
            model="not-a-model",
        )
