"""Finite, seeded contact contours in material millimetres.

The contour changes where liquid or mechanical pressure reaches the material;
it is never applied as a blur or colour overlay to an observed image.
"""
import math
from functools import lru_cache

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


# Each kind has its own repeatable wetting/handling pattern for the same roll.
_CONTACT_SALTS = {
    'bath_contact': 17, 'developer_inhibitor': 31, 'fixer_contact': 37,
    'contaminate': 41, 'silver_bath': 43, 'dye_bath': 47, 'emulsion_wear': 53,
    'backing_condition': 59, 'backing_wash': 61,
}

@lru_cache(maxsize=128)
def _contact_pattern(kind, seed):
    rng = np.random.default_rng([seed, _CONTACT_SALTS[kind]])
    anchor = rng.random(2)
    angle = rng.uniform(0., 2*math.pi)
    parts = []
    if kind == 'bath_contact':
        # Several attached bubbles occupy only a fraction of a wetted patch.
        for i in range(6):
            size = rng.uniform(.12,.23) if i == 0 else rng.uniform(.045,.15)
            parts.append((0. if i == 0 else rng.uniform(-.85,.85),
                          0. if i == 0 else rng.uniform(-.42,.42),
                          size, size*rng.uniform(.72,.98), rng.uniform(-.6,.6), rng.uniform(.48,.94)))
    elif kind == 'emulsion_wear':
        # A pressure track consists of narrow, offset strokes along the wipe.
        for i in range(4):
            parts.append((rng.uniform(-.32,.32), (i-1.5)*.10+rng.uniform(-.035,.035),
                          rng.uniform(.5,1.1), rng.uniform(.025,.095), rng.uniform(-.09,.09), rng.uniform(.48,.92)))
    else:
        # A larger wet head fades into smaller, displaced traces along its run.
        for i in range(7):
            t = i/6
            a = rng.uniform(.48,.69)*(1.-.7*t)
            parts.append((1.35*t+rng.uniform(-.12,.12), .22*math.sin(4*t)+rng.uniform(-.23,.23),
                          a, a*rng.uniform(.4,.85), rng.uniform(-.7,.7), rng.uniform(.45,.9)*(1.-.45*t)))
    return tuple(anchor), angle, tuple(parts)


def local_contact(shape, extent, event, radius, start, stop):
    # Coordinates stay attached to the physical frame for all crops, row blocks
    # and preview sizes. Only evaluate the rectangles intersecting each mark.
    h, w = shape
    dx, dy = extent[0]/w, extent[1]/h
    kind = event['kind']
    anchor, angle, parts = _contact_pattern(kind, event['contact_seed'])
    if event['placement'] == 'automatic':
        margin = min(radius*.55, min(extent)*.18)
        cx, cy = (u*(length+2*margin)-margin for u,length in zip(anchor,extent))
    else:
        cx, cy = event['center_x']*extent[0], event['center_y']*extent[1]
    co, si = math.cos(angle), math.sin(angle)
    weight = np.zeros((stop-start,w),np.float32)
    softness = min(.85, event.get('edge_width_mm',0.)/radius)
    variation = event.get('boundary_variation',0.)
    for offset, cross, major, minor, turn, strength in parts:
        px = cx + radius*(offset*co-cross*si)
        py = cy + radius*(offset*si+cross*co)
        ca, sa = math.cos(angle+turn), math.sin(angle+turn)
        a, b = radius*major, radius*minor
        # The crosswise bend tapers the liquid edge and keeps wipe tracks continuous.
        bend = (.65 if kind == 'emulsion_wear' else .10)+.32*variation
        ex = abs(ca)*a + abs(sa)*b*(1.+bend)
        ey = abs(sa)*a + abs(ca)*b*(1.+bend)
        left, right = max(0,math.floor((px-ex)/dx)), min(w,math.ceil((px+ex)/dx))
        top, bottom = max(start,math.floor((py-ey)/dy)), min(stop,math.ceil((py+ey)/dy))
        if left >= right or top >= bottom:
            continue
        x = (np.arange(left,right,dtype=np.float32)[None,:]+.5)*dx-px
        y = (np.arange(top,bottom,dtype=np.float32)[:,None]+.5)*dy-py
        u = (ca*x+sa*y)/a
        v = (-sa*x+ca*y)/b + bend*np.sin(3*u+turn)
        section = np.maximum(0.,1.-u*u-v*v)
        if kind == 'bath_contact':
            # Liquid reaches the perimeter before the last attached core lifts.
            section **= .8+softness
        elif kind == 'emulsion_wear':
            # Local pressure falls and recovers along an interrupted wipe.
            section *= .65+.35*np.sin(6*u+4*offset)**2
            section **= 1.4+softness
        else:
            section = section**(1.4+softness)
        patch = weight[top-start:bottom-start,left:right]
        np.maximum(patch, strength*section, out=patch)
    return weight
