"""Three-band camera-side transmission of unprocessed, base-first film."""
import numpy as np
from functools import lru_cache
from importlib.resources import files

from film_foundry.contracts.carbon_backing import validate_carbon_backing
from film_foundry.contracts.film_exposure import rear_optics
from .material_geometry import FRAME_LONG_EDGES_MM
from .film_process.integration import _base_and_mask_density

@lru_cache(maxsize=1)
def _harman_doses():
    with files('film_foundry').joinpath('assets/harman_red_125_wedge.txt').open() as rows:
        table = np.loadtxt(rows)
    incident = (.18 * 10. ** (table[:,0] - 2.643)).astype(np.float32)
    return incident, table[:,4:].astype(np.float32)


def _wedge_drive(dose, knots, drive):
    inside = np.interp(dose, knots, drive)
    slope = (drive[-1]-drive[-2])/(knots[-1]-knots[-2])
    return np.where(dose < knots[0], dose*(drive[0]/knots[0]),
                    np.where(dose > knots[-1], drive[-1]+(dose-knots[-1])*slope, inside))


def expose_from_base(linear, config, *, monochrome, frame_config):
    import cv2
    optics = rear_optics(config.exposure.rear_optics)
    frame = frame_config.frame_size.strip().lower().replace('-', '_').replace(' ', '_')
    long_mm = frame_config.frame_long_edge_mm or FRAME_LONG_EDGES_MM[frame]
    # In film-rail coordinates the emulsion moves by t, while refraction
    # moves the paraxial focus by t*(1-1/n). The residual is t/n.
    residual = 0. if optics['auto_refocus'] else optics['thickness_um'] / optics['index'] - optics['focus_shift_um']
    diameter = abs(residual) / (1000 * optics['f_number']) * max(linear.shape[:2]) / long_mm
    # Separable box with the same per-axis variance as a uniform circular pupil.
    half_width = diameter * np.sqrt(3.) / 4.
    if half_width > .5:
        radius = int(np.ceil(half_width - .5))
        kernel = np.ones(2 * radius + 1, np.float32)
        kernel[[0, -1]] = half_width - radius + .5
        kernel /= kernel.sum()
        linear = cv2.sepFilter2D(linear, -1, kernel, kernel, borderType=cv2.BORDER_REPLICATE)
    if optics['mirror'] != 'none':
        linear = cv2.flip(linear, 1 if optics['mirror'] == 'horizontal' else 0)
    transmit_from_base(linear, config.film, monochrome=monochrome, profile=optics['profile'])
    return linear


def transmit_from_base(linear, film, *, monochrome, profile='generic'):
    """Modify the private incident-light buffer before the stock maps it to layers."""
    backing = validate_carbon_backing(film.carbon_backing)
    carbon = backing['initial_mg_m2'] * backing['density_per_mg_m2'] if backing else 0.
    if film.physical_coating_optics and any(r["kind"] == "support" for r in film.coating_stack["layers"]):
        # The permanent support is traversed at its declared position.
        linear *= np.float32(10. ** -carbon)
        return
    permanent, _ = _base_and_mask_density(film)
    support = np.power(10., -(np.asarray(permanent) + carbon))
    if film.physical_coating_optics:
        linear *= support.astype(np.float32)
        return
    if monochrome or film.color_process == 'chromogenic_monochrome':
        linear *= (support * 0.5).astype(np.float32)
        return
    if profile == 'harman_red_125':
        knots, drives = _harman_doses()
        relative_support = (support / (10. ** -.03)).astype(np.float32)
        for start in range(0, linear.shape[0], 128):
            block = linear[start:start+128]
            block *= relative_support
            b = block[..., 2]
            # White-wedge data constrains relative speeds. Chromatic blue
            # cross-sensitivity is qualitative; these shares are estimates.
            block[..., 0] = _wedge_drive(.90*block[..., 0] + .10*b, knots, drives[:,0])
            block[..., 1] = _wedge_drive(.965*block[..., 1]+.035*b, knots, drives[:,1])
            block[..., 2] = _wedge_drive(b, knots, drives[:,2])
        return
    # Effective reverse paths to the red, green and blue records. Native blue
    # sensitivity in the first two records sees light before the yellow filter.
    # These reference coefficients describe a generic stack, not a measured stock.
    gain = (support * np.exp2((-.65, -2.15, -4.25))).astype(np.float32)
    for start in range(0, linear.shape[0], 128):
        block = linear[start:start + 128]
        blue = block[..., 2] * np.float32(support[2])
        block *= gain
        block[..., 0] += blue * .10
        block[..., 1] += blue * .035
