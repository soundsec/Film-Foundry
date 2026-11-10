"""Formation-only approximation of the bath's spatial wet-time field.

The complete motion history is integrated on a bounded physical control grid.
Only wet seconds are interpolated: exposure, chemical pools, kinetics and the
final optical master retain their requested geometry. This is not a scanner.
"""
from threading import Lock
import numpy as np
from .spatial_fields import GlobalFieldGrid, GlobalRateMultiplierField, global_field_grid


class ContactTimeFieldCache:
    """One immutable recipe/frame, shared by tiles and provisional work grids.

    Cache lifetime is the formation call, never a global or persistent medium
    cache. A resumed call rebuilds from its frozen recipe and reaction window.
    """
    def __init__(self, shape, extent, long_edge, *, precision='float64'):
        self.grid = global_field_grid(shape, long_edge).work_shape
        self.extent = tuple(extent)
        self.precision = precision
        self.fields = {}
        self.lock = Lock()

    def sample(self, index, event, duration, window, shape, start, stop):
        rotary = event['kind'] == 'rotary_contact'
        interval = tuple(window or (0., 1.)) if rotary else (0., 1.)
        if rotary and event['immersion_fraction'] in (0., 1.):
            return np.full((stop-start, shape[1]), duration*(interval[1]-interval[0])*event['immersion_fraction'], np.float32)
        key = (index, float(duration), interval)
        with self.lock:
            field = self.fields.get(key)
            if field is None:
                if rotary:
                    from .rotary_contact import contact_duration
                    values = contact_duration(self.grid, 0, self.grid[0], duration, event,
                                              interval, extent=self.extent,precision=self.precision)
                else:
                    from .process_operations import contact_duration
                    values = contact_duration(self.grid, self.extent, 0, self.grid[0], duration, event)
                field = np.asarray(values, np.float32)
                field.setflags(write=False)
                self.fields[key] = field
        # Use the original float32 half-pixel mapping, not OpenCV's quantized
        # remap table. Narrow fronts may soften; temporal integration is intact.
        rows = GlobalRateMultiplierField(field, GlobalFieldGrid(tuple(shape[:2]), self.grid)).slice_rows(start, stop)
        return np.clip(rows, 0., duration * (interval[1] - interval[0]))
