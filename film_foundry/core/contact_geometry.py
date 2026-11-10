"""Finite, seeded contact contours in material millimetres.

The contour changes where liquid or mechanical pressure reaches the material;
it is never applied as a blur or colour overlay to an observed image.
"""
import numpy as np


def contact_footprint(shape, extent, event):
    h, w = shape
    radius = event['radius_mm']
    x = ((np.arange(w)+.5)/w-event['center_x'])*extent[0]
    y = ((np.arange(h)+.5)/h-event['center_y'])*extent[1]
    distance = np.hypot(x[None, :], y[:, None])
    variation = event.get('boundary_variation', 0.)
    if variation:
        # Separate identity from amplitude variation and grain. Smooth large
        # lobes plus smaller torn edges, rather than independent pixel noise.
        rng = np.random.default_rng(int(event.get('distribution_seed', 0)) ^ 0x45A19)
        angle = np.arctan2(y[:, None], x[None, :])
        frequencies = (2, 3, 5, 9, 17)
        weights = (.42, .28, .16, .09, .05)
        contour = np.zeros(shape, np.float64)
        for frequency, weight, phase in zip(frequencies, weights, rng.uniform(0, 2*np.pi, 5)):
            contour += weight*np.sin(frequency*angle+phase)
        amplitude = .65*variation
        # Preserve nominal area for an unclipped continuous radial contour.
        radius = radius*(1+amplitude*contour)/np.sqrt(1+amplitude**2*sum(v*v for v in weights)/2)
    width = max(event.get('edge_width_mm', 0.), extent[0]/w, extent[1]/h)
    weight = np.clip((radius-distance)/width+.5, 0, 1)
    return (weight*weight*(3-2*weight)).astype(np.float32)
