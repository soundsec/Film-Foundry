import numpy as np
import pytest

from film_foundry.core.derived_cache import (
    bounded_inverse_3x3,
    clear_derived_caches,
    derived_cache_info,
    pseudoinverse_3x3,
)


def test_pseudoinverse_cache_is_bounded_read_only_and_exact():
    clear_derived_caches()
    matrix = ((1.0, 0.1, 0.03), (0.06, 0.95, 0.12), (0.02, 0.14, 1.08))

    first = pseudoinverse_3x3(matrix)
    second = pseudoinverse_3x3(matrix)

    assert first is second
    assert not first.flags.writeable
    assert np.array_equal(first, np.linalg.pinv(np.asarray(matrix, dtype=np.float32)).astype(np.float32))
    assert derived_cache_info()["pseudoinverse_3x3"]["hits"] == 1
    with pytest.raises(ValueError):
        first[0, 0] = 0.0


def test_bounded_inverse_cache_preserves_singular_fallback():
    clear_derived_caches()
    singular = ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    result = bounded_inverse_3x3(singular, -0.3, 1.35)

    assert np.array_equal(result, np.eye(3, dtype=np.float32))
    assert not result.flags.writeable
