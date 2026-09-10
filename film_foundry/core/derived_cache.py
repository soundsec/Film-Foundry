"""Small, immutable derived-value caches shared across image frames.

Only configuration-derived values belong here. Full-resolution image data,
random fields, material states, and scan observations must never enter this
cache because doing so would retain user images or blur stage ownership.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np


def _matrix_key(values) -> tuple[float, ...]:
    matrix = np.asarray(values, dtype=np.float32).reshape(3, 3)
    if not np.isfinite(matrix).all():
        raise ValueError("Cached 3x3 matrix contains non-finite values.")
    return tuple(float(value) for value in matrix.reshape(-1))


@lru_cache(maxsize=128)
def _cached_pseudoinverse_3x3(key: tuple[float, ...]) -> np.ndarray:
    matrix = np.asarray(key, dtype=np.float32).reshape(3, 3)
    result = np.linalg.pinv(matrix).astype(np.float32)
    result.flags.writeable = False
    return result


def pseudoinverse_3x3(values) -> np.ndarray:
    """Return a read-only cached float32 pseudoinverse for a 3x3 matrix."""
    return _cached_pseudoinverse_3x3(_matrix_key(values))


@lru_cache(maxsize=128)
def _cached_bounded_inverse_3x3(
    key: tuple[float, ...],
    lower: float,
    upper: float,
) -> np.ndarray:
    matrix = np.asarray(key, dtype=np.float32).reshape(3, 3)
    try:
        result = np.linalg.inv(matrix).astype(np.float32)
    except np.linalg.LinAlgError:
        result = np.eye(3, dtype=np.float32)
    result = np.clip(result, float(lower), float(upper)).astype(np.float32)
    result.flags.writeable = False
    return result


def bounded_inverse_3x3(values, lower: float, upper: float) -> np.ndarray:
    """Return a read-only cached inverse with the requested safety bounds."""
    return _cached_bounded_inverse_3x3(
        _matrix_key(values), float(lower), float(upper)
    )


def derived_cache_info() -> dict[str, object]:
    """Expose bounded-cache diagnostics without exposing cached arrays."""
    return {
        "pseudoinverse_3x3": _cached_pseudoinverse_3x3.cache_info()._asdict(),
        "bounded_inverse_3x3": _cached_bounded_inverse_3x3.cache_info()._asdict(),
    }


def clear_derived_caches() -> None:
    """Clear small derived caches for tests or long-lived editor sessions."""
    _cached_pseudoinverse_3x3.cache_clear()
    _cached_bounded_inverse_3x3.cache_clear()
