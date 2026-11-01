"""Read-only material-pool work grids shared by local development estimates.

Downsampling follows material exposure, never precedes its nonlinear response.
Both a scene-side row source and a frozen latent therefore supply the same
bounded pools without reconstructing RGB or allocating another full latent.
"""

from __future__ import annotations

import cv2
import numpy as np

from film_foundry.core.film_process.integration import (
    _exposure_material, _material_latent_state, _material_layer_count,
    _color_material, _is_monochrome_material, _silver_material,
)
from film_foundry.core.film_process.model import FilmProcessState
from film_foundry.core.spatial_fields import global_field_grid


class MaterialLatentSource:
    MODEL = "post_exposure_pool_area_grid_v1"
    ROW_PIXELS = 262144
    POOLS = ("halide", "developability", "metallic_silver", "coupler", "dye", "bleached_halide")

    def __init__(self, film, *, image_linear=None, exposed_latent=None,
                 latent_layer_exposure_addition=None):
        if (image_linear is None) == (exposed_latent is None):
            raise ValueError("local development requires either image rows or frozen latent pools")
        if exposed_latent is not None:
            if latent_layer_exposure_addition is not None:
                raise ValueError("layer exposure additions are already frozen in the latent")
            exposed_latent.validate()
            layers = _material_layer_count(film, exposed_latent)
            self.material = (_silver_material(film, layers) if _is_monochrome_material(film)
                             else _color_material(film, layers))
            self.shape = tuple(exposed_latent.halide.shape[:2])
        else:
            image_linear = np.asarray(image_linear, dtype=np.float32)
            if image_linear.ndim != 3 or image_linear.shape[-1] != 3 or min(image_linear.shape[:2]) <= 0:
                raise ValueError("local development requires a non-empty HxWx3 linear source")
            low, high = float(np.min(image_linear)), float(np.max(image_linear))
            if not np.isfinite(low) or not np.isfinite(high) or low < 0.0:
                raise ValueError("local development source must be finite and non-negative")
            self.material = _exposure_material(film)
            self.shape = tuple(image_linear.shape[:2])
        self.film, self.image, self.latent = film, image_linear, exposed_latent
        self.addition = latent_layer_exposure_addition
        self._grids = {}

    def _rows(self, start, stop):
        if self.latent is not None:
            pools = {name: (None if (a := getattr(self.latent, name)) is None else a[start:stop])
                     for name in self.POOLS}
            return FilmProcessState(**pools,
                auxiliary_remaining=self.latent.auxiliary_remaining,
                masking_coupler_remaining=self.latent.masking_coupler_remaining)
        addition = self.addition
        if addition is not None:
            addition = (addition.slice_rows(start, stop) if hasattr(addition, "slice_rows")
                        else np.asarray(addition)[start:stop])
        return _material_latent_state(self.image[start:stop], self.film, self.material,
                                     latent_layer_exposure_addition=addition)

    def working_state(self, work_long_edge):
        """Borrowed, read-only-by-contract pools. A consuming estimator must copy."""
        grid = global_field_grid(self.shape, max(1, int(work_long_edge)))
        if grid.work_shape in self._grids:
            return self._grids[grid.work_shape], grid
        # Keep one work grid. Different adjacency/DIR extents must not retain
        # two complete sets of temporary material pools at the same time.
        self._grids.clear()
        if not grid.reduced:
            state = self.latent if self.latent is not None else self._rows(0, self.shape[0])
        else:
            height, width = self.shape
            gh, gw = grid.work_shape
            layers = self.material.layer_count
            fields = None
            block_rows = max(1, self.ROW_PIXELS // width)
            for y in range(gh):
                # Integrate exact source-pixel coverage. Only one output row's
                # source footprint and the bounded destination pools are live.
                top, bottom = y * height / gh, (y + 1) * height / gh
                a, b = int(np.floor(top)), min(height, int(np.ceil(bottom)))
                for start in range(a, b, block_rows):
                    stop = min(start + block_rows, b)
                    rows = self._rows(start, stop)
                    if fields is None:
                        fields = {name: (None if getattr(rows, name) is None else
                                        np.empty((gh, gw, layers), np.float32)) for name in self.POOLS}
                    indices = np.arange(start, stop, dtype=np.float64)
                    weight = ((np.minimum(indices + 1, bottom) - np.maximum(indices, top))
                              / (bottom - top)).astype(np.float32)
                    for name, target in fields.items():
                        if target is None:
                            continue
                        if start == a:
                            target[y].fill(0.0)
                        values = getattr(rows, name)
                        horizontal = (values if width == gw else cv2.resize(
                            values, (gw, stop-start), interpolation=cv2.INTER_AREA).reshape(stop-start, gw, layers))
                        target[y] += np.einsum("y,yxl->xl", weight, horizontal)
            state = FilmProcessState(**fields,
                auxiliary_remaining=rows.auxiliary_remaining,
                masking_coupler_remaining=rows.masking_coupler_remaining)
        self._grids[grid.work_shape] = state
        return state, grid

    def take_working_state(self, work_long_edge):
        """Transfer an owned work grid, copying only a borrowed original latent."""
        state, grid = self.working_state(work_long_edge)
        del self._grids[grid.work_shape]
        return (state.copy() if state is self.latent else state), grid
