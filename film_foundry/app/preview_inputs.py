"""Explicit approximate preview copies of frozen material, never persisted.

Area resampling lowers the preview grid while keeping physical frame extent,
profiles, polarity and base anchors. Formal APIs do not use these copies.
"""
from dataclasses import fields, replace
import copy

import numpy as np

from film_foundry.core.preview import resize_to_long_edge


def preview_latent(latent, long_edge):
    values = {}
    for field in fields(latent):
        value = getattr(latent, field.name)
        if isinstance(value, np.ndarray):
            resized = resize_to_long_edge(value, long_edge)
            # OpenCV drops a singleton channel on a one-layer material.
            if value.ndim == 3 and resized.ndim == 2:
                resized = resized[..., None]
            values[field.name] = np.array(resized, dtype=np.float32, copy=True)
    if latent.material_geometry is not None:
        values["material_geometry"] = replace(latent.material_geometry, full_shape=values["halide"].shape[:2])
    result = replace(latent, **values)
    result.validate()
    return result


def preview_medium(medium, long_edge):
    values = {}
    for field in fields(medium):
        value = getattr(medium, field.name)
        if isinstance(value, np.ndarray) and value.size:
            values[field.name] = resize_to_long_edge(value, long_edge)
    if medium.spectral_medium is not None:
        spectral = medium.spectral_medium
        # OpenCV's non-integer INTER_AREA implementation supports <=4
        # channels. Material components are data bands, so resize in RGB-sized
        # groups without changing ordinary image resampling.
        components = spectral.component_amounts
        if long_edge is not None and 0 < long_edge < max(components.shape[:2]):
            components = np.concatenate([
                resize_to_long_edge(components[..., start:start+3], long_edge)
                for start in range(0, 9, 3)
            ], axis=-1)
        values["spectral_medium"] = replace(
            spectral, component_amounts=components,
            post_process_density_rgb=(None if spectral.post_process_density_rgb is None else
                                     resize_to_long_edge(spectral.post_process_density_rgb, long_edge)),
        )
    values["metadata"] = copy.deepcopy(medium.metadata)
    return replace(medium, **values)
