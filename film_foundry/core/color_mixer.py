"""Output-referred HSL mixer v1; smooth circular bands, independent of media."""
import numpy as np
from film_foundry.model.config import normalize_color_mixer

CENTERS = np.array([0, 30, 60, 120, 180, 240, 270, 300, 360], dtype=np.float32)


def apply_color_mixer(image, settings):
    settings = normalize_color_mixer(settings)
    if settings is None:
        return image  # Exact historical path, without clipping or conversion.
    rgb = np.clip(image, 0, 1)
    hi, lo = rgb.max(axis=-1), rgb.min(axis=-1)
    delta = hi-lo
    safe = np.maximum(delta, 1e-12)
    r, g, b = (rgb[..., c] for c in range(3))
    hue = np.where(hi == r, (g-b)/safe, np.where(hi == g, (b-r)/safe+2, (r-g)/safe+4))
    hue = (hue*60) % 360
    light = (hi+lo)*.5
    sat = delta/np.maximum(1-np.abs(2*light-1), 1e-12)
    index = np.minimum(np.searchsorted(CENTERS, hue, side="right")-1, 7)
    t = (hue-CENTERS[index])/(CENTERS[index+1]-CENTERS[index])
    t = t*t*(3-2*t)
    rows = np.array(settings, dtype=np.float32)
    adjustment = rows[index]*(1-t[..., None])+rows[(index+1) % 8]*t[..., None]
    hue = (hue+adjustment[..., 0]) % 360
    sat = np.clip(sat*(1+adjustment[..., 1]/100), 0, 1)
    light = np.clip(light+adjustment[..., 2]*.0025*np.minimum(delta/.05, 1), 0, 1)
    chroma = (1-np.abs(2*light-1))*sat
    sector = hue/60
    x = chroma*(1-np.abs(sector % 2-1))
    z = np.zeros_like(x)
    choices = ((chroma,x,z),(x,chroma,z),(z,chroma,x),(z,x,chroma),(x,z,chroma),(chroma,z,x))
    result = np.empty_like(rgb)
    for c in range(3):
        result[..., c] = np.select([sector < k for k in (1,2,3,4,5)],
                                  [choices[k][c] for k in range(5)], choices[5][c]) + light-chroma*.5
    # Exact neutral pixels have no hue and must not be recolored or relit.
    return np.where((delta == 0)[..., None], rgb, np.clip(result, 0, 1)).astype(np.float32)
