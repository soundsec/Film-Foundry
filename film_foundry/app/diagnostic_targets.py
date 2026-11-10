"""Reusable synthetic sRGB targets for creative material comparisons.

These are defined digital swatches, not measured commercial calibration-card
reflectances. They test the same exposure/formation/observation path as images.
"""
from functools import lru_cache
import numpy as np

TARGET_VERSION = 1
TARGETS = ('color24', 'grayscale', 'edges')
# Six skin/earth/sky colours, six foliage/flower colours, six saturated colours,
# and six neutrals. Deliberately not sold as a ColorChecker calibration dataset.
SWATCHES = (
    (116,82,66),(192,146,122),(94,122,157),(90,108,62),(135,126,174),(90,173,164),
    (216,128,42),(76,87,164),(188,76,96),(92,59,111),(150,184,55),(227,164,38),
    (40,62,148),(69,145,72),(177,43,49),(237,204,46),(183,72,147),(25,157,181),
    (245,245,245),(202,202,202),(158,158,158),(116,116,116),(78,78,78),(32,32,32),
)


@lru_cache(maxsize=12)
def _target(kind, long_edge):
    if kind not in TARGETS:
        raise ValueError(f'Unknown diagnostic target: {kind}')
    w = max(96, min(2048, int(long_edge)))
    h = w * 2 // 3
    a = np.full((h,w,3), .12, dtype=np.float32)
    if kind == 'color24':
        for i, colour in enumerate(SWATCHES):
            row,col = divmod(i,6)
            x0,x1 = col*w//6,(col+1)*w//6
            y0,y1 = row*h//4,(row+1)*h//4
            margin = max(2,w//120)
            a[y0+margin:y1-margin,x0+margin:x1-margin] = np.array(colour)/255
    elif kind == 'grayscale':
        x = np.linspace(0,1,w,dtype=np.float32)
        a[:h//3] = x[None,:,None]
        a[h//3:2*h//3] = (np.minimum((x*21).astype(int),20)/20)[None,:,None]
        a[2*h//3:5*h//6] = (x*.12)[None,:,None]
        a[5*h//6:] = (.88+x*.12)[None,:,None]
    else:
        y,x = np.mgrid[:h,:w].astype(np.float32)
        # A slanted edge and rings expose blur/halation; line-pair wedges show
        # anisotropy. Smooth coverage avoids baking a jagged edge into the input.
        left = np.clip((x-.32*w-.12*(y-h/2))+.5,0,1)
        a[:] = (.04 + .92*left)[...,None]
        radius = np.sqrt((x-.76*w)**2+(y-.32*h)**2)
        rings = .5+.45*np.sin(radius*.24)
        region = (x>w*.52)&(y<h*.65)
        a[region] = rings[region,None]
        region = (x>w*.52)&(y>=h*.65)
        lines = .5+.45*np.sin((x-.52*w)**2/(w*.035))
        a[region] = lines[region,None]
    a.setflags(write=False)
    return a


def diagnostic_target(kind='color24', *, long_edge=480):
    """An independent array; editing it cannot contaminate later previews."""
    return _target(kind, long_edge).copy()


def target_identity(kind, long_edge):
    return ('diagnostic_target', TARGET_VERSION, kind, int(long_edge))
