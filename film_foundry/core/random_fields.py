"""Memory-bounded helpers for deterministic stochastic control fields."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np


_DEFAULT_FLOAT64_TEMP_VALUES = 2_000_000

try:
    _probe_rng = np.random.default_rng(0)
    _probe_out = np.empty(1, dtype=np.float64)
    _probe_rng.standard_normal(1, dtype=np.float64, out=_probe_out)
except TypeError:  # pragma: no cover - supported Generator APIs expose out
    _STANDARD_NORMAL_ACCEPTS_OUT = False
else:
    _STANDARD_NORMAL_ACCEPTS_OUT = True
finally:
    del _probe_rng, _probe_out


def standard_normal_float32(
    rng: np.random.Generator,
    shape: Sequence[int],
    *,
    max_temp_values: int = _DEFAULT_FLOAT64_TEMP_VALUES,
) -> np.ndarray:
    """Return legacy-equivalent float32 normal samples with a bounded temp.

    ``Generator.standard_normal(shape).astype(float32)`` first materializes a
    complete float64 field.  Large exact grain fields therefore retain an
    avoidable 8 bytes per pixel while their float32 destination is also live.
    Filling the contiguous destination in C-order chunks consumes the same
    random sequence and performs the same per-value float64-to-float32 cast;
    only the lifetime and maximum size of the conversion source change.
    """
    normalized_shape = tuple(int(value) for value in shape)
    if any(value < 0 for value in normalized_shape):
        raise ValueError("Random field dimensions must be non-negative.")
    chunk_values = max(1, int(max_temp_values))
    result = np.empty(normalized_shape, dtype=np.float32)
    return fill_standard_normal_float32(
        rng,
        result,
        max_temp_values=chunk_values,
    )


def fill_standard_normal_float32(
    rng: np.random.Generator,
    destination: np.ndarray,
    *,
    max_temp_values: int = _DEFAULT_FLOAT64_TEMP_VALUES,
) -> np.ndarray:
    """Overwrite one contiguous float32 buffer with the same bounded stream."""
    result = np.asarray(destination)
    if result.dtype != np.float32 or not result.flags.c_contiguous:
        raise ValueError("Random field destination must be C-contiguous float32.")
    chunk_values = max(1, int(max_temp_values))
    flat = result.reshape(-1)
    source_buffer = (
        np.empty(min(chunk_values, flat.size), dtype=np.float64)
        if _STANDARD_NORMAL_ACCEPTS_OUT and flat.size
        else None
    )
    for start in range(0, flat.size, chunk_values):
        stop = min(start + chunk_values, flat.size)
        count = stop - start
        if source_buffer is None:
            source = rng.standard_normal(count)
        else:
            source = source_buffer[:count]
            rng.standard_normal(
                count,
                dtype=np.float64,
                out=source,
            )
        np.copyto(flat[start:stop], source, casting="unsafe")
    del source_buffer
    return result
