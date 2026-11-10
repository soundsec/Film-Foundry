"""One file job's bounded, derived spectral density cache. Never material state."""
from contextlib import contextmanager
from contextvars import ContextVar
import os

import numpy as np
from film_foundry.contracts.spectral_optics import SpectralObservationProfile

from .execution_topology import reference_execution_enabled

_current = ContextVar('file_spectral_observation', default=None)


@contextmanager
def observation_reuse_scope(max_bytes=512*1024*1024):
    """Reuse immutable final optics within one synchronous file operation only.

    This is not a general cache for mutable arrays. Callers must not alter the
    final spectral medium while this scope is active. Hits are consumer-owned.
    """
    if os.environ.get('FILM_FOUNDRY_OBSERVATION_REUSE') == '0':
        max_bytes = 0
    state = {'limit': max_bytes, 'entry': None}
    token = _current.set(state)
    try:
        yield
    finally:
        state['entry'] = None
        _current.reset(token)


def observe_density(medium, observer, row_slice, compute):
    state = _current.get()
    if state is None or medium is None or reference_execution_enabled():
        return compute(medium, observer, row_slice=row_slice)
    observer = SpectralObservationProfile.from_value(observer)
    height, width = medium.component_amounts.shape[:2]
    if height*width*12 + height > state['limit']:
        return compute(medium, observer, row_slice=row_slice)
    if row_slice is not None and (not isinstance(row_slice, slice) or row_slice.step not in (None, 1)):
        return compute(medium, observer, row_slice=row_slice)
    start, stop, _ = (row_slice or slice(None)).indices(height)
    entry = state['entry']
    if entry is None or entry[0] is not medium or entry[1] != observer:
        # Drop a previous profile before allocating its replacement. A strong
        # medium reference also prevents accidental object-id reuse.
        state['entry'] = None
        entry = None
        try:
            entry = (medium, observer, np.empty((height, width, 3), np.float32), np.zeros(height, bool))
        except MemoryError:
            state['limit'] = 0
            return compute(medium, observer, row_slice=row_slice)
        state['entry'] = entry
    _, _, density, valid = entry
    if not valid[start:stop].all():
        result = compute(medium, observer, row_slice=row_slice)
        density[start:stop] = result
        valid[start:stop] = True
        return result
    # Scanners may consume their input. Keep the reusable density privately owned.
    return density[start:stop].copy()
