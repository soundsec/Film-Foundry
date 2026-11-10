"""Form film rebates from small material/process reference exposures.

The image gate is never recalculated or resampled. Rebate references use the
same reaction operators and stock coefficients, without spatial accidents or
grain. They become part of the stored optical/spectral medium, not a scan UI
overlay. This explicit approximation keeps optional borders inexpensive.
"""
from dataclasses import asdict, replace

import cv2
import numpy as np

from film_foundry.contracts.data_kernel import (
    FORMED_FILM_REBATE_KEY, FORMED_FILM_REBATE_MODEL, FILM_REBATE_EXPOSURE_MODEL,
)
from film_foundry.contracts.edge_marks import EdgeMarkPattern, factory_edge_pattern
from film_foundry.core.film_border import resolve_film_border_layout, validate_film_border_config
from film_foundry.model.config import FilmBorderConfig


def border_snapshot(config):
    """Freeze only edge settings; in-picture imprint has already been exposed."""
    record = asdict(validate_film_border_config(config))
    for key in tuple(record):
        if key.startswith('imprint_'):
            del record[key]
    record['model'] = FILM_REBATE_EXPOSURE_MODEL
    return record


def validate_border_snapshot(record):
    if record is None:
        return None  # old latent: no edge exposure was recorded
    return border_snapshot(_border_config(record))


def _border_config(record):
    values = dict(record)
    if values.pop('model', FILM_REBATE_EXPOSURE_MODEL) != FILM_REBATE_EXPOSURE_MODEL:
        raise ValueError('Unsupported film rebate exposure model')
    return validate_film_border_config(FilmBorderConfig(**values))


def _strip_masks(config, layout, label):
    """Rasterize only two rebates, not a full-size temporary canvas."""
    width, height = layout.canvas_size
    rows = layout.image_rect[1]
    dose = np.zeros((2 * rows, width), dtype=np.float32)
    holes = np.zeros_like(dose)
    proportional = (config.factory_barcode != 'legacy' or
                    config.factory_layout not in {'legacy','text_code','combined','text_only'})
    def paste(stamp,x,y,side):
        low=0 if side=='top' else rows;high=low+rows
        xa=max(0,x);xb=min(width,x+stamp.shape[1]);ya=max(low,y);yb=min(high,y+stamp.shape[0])
        if xa<xb and ya<yb:
            np.maximum(dose[ya:yb,xa:xb],stamp[ya-y:yb-y,xa-x:xb-x],out=dose[ya:yb,xa:xb])
    for spec in layout.factory_edges:
        pattern = (EdgeMarkPattern.decode(spec.pattern_code) if spec.pattern_code
                   else factory_edge_pattern(label,config.factory_batch,config.frame_number,config.factory_layout,config.factory_barcode,spec.side,config.factory_barcode_seed))
        grid = np.array([[pattern.ink(x, y) for x in range(pattern.width)]
                         for y in range(pattern.height)], dtype=np.float32)
        if spec.flip:
            grid = grid[::-1, ::-1]
        x0, y0, x1, y1 = spec.normalized_rect
        x0, x1 = round(x0 * width), round(x1 * width)
        offset = 0 if spec.side == 'top' else height - 2 * rows
        y0, y1 = round(y0 * height) - offset, round(y1 * height) - offset
        if proportional:
            # New plates retain glyph/module proportions. Legacy layouts with
            # legacy bars retain the original full-band stretch for replay.
            x1 = min(x1, x0 + max(1, round((y1-y0)*pattern.width/pattern.height)))
        # Supersample the lettering footprint only; image pixels stay intact.
        if x1 > x0 and y1 > y0:
            stamp = cv2.resize(grid, ((x1-x0)*4, (y1-y0)*4), interpolation=cv2.INTER_NEAREST)
            stamp = cv2.resize(stamp, (x1-x0, y1-y0), interpolation=cv2.INTER_AREA)
            dx,dy=getattr(config,'factory_offset_'+spec.side)
            paste(stamp,x0+round(dx*width),y0+round(dy*rows),spec.side)
        if spec.side=='bottom' and y1>y0 and (config.frame_number is not None or config.advance_marker_style not in {'legacy','none'}):
            from film_foundry.core.imprint_glyphs import render_display_text
            caption=(str(config.frame_number)+' ->' if config.advance_direction=='right' else '<- '+str(config.frame_number))
            xa,xb=round(.84*width),round(.99*width)
            if xb>xa:
                if config.advance_marker_style=='legacy':
                    mark=render_display_text(caption,'dot_matrix',(y1-y0)*4,(xb-xa)*4)
                    if proportional:xa=xb-min(xb-xa,max(1,round(mark.shape[1]/4)))
                    mark=cv2.resize(mark,(xb-xa,y1-y0),interpolation=cv2.INTER_AREA).astype(np.float32)/255
                else:
                    from film_foundry.core.edge_direction import advance_stencil
                    mark=advance_stencil(config.frame_number,config.advance_marker_style,config.advance_direction,(y1-y0)*4,(xb-xa)*4)
                    mark=cv2.resize(mark,(max(1,round(mark.shape[1]/4)),max(1,round(mark.shape[0]/4))),interpolation=cv2.INTER_AREA)
                    xa=xb-mark.shape[1]
                dx,dy=config.advance_marker_offset
                paste(mark,xa+round(dx*width),y0+round(dy*rows),'bottom')
    if config.factory_mark_type == 'laser' and config.factory_mark_spread > 0:
        sigma = max(.25, rows * .012) * config.factory_mark_spread
        if any(value for offset in (config.factory_offset_top,config.factory_offset_bottom,config.advance_marker_offset) for value in offset):
            # The packed strips are not physically adjacent. A moved mark may
            # reach the inner edge, but must never diffuse into the other strip.
            dose=np.concatenate([cv2.GaussianBlur(dose[start:start+rows],(0,0),sigma,borderType=cv2.BORDER_CONSTANT) for start in (0,rows)])
        else:
            dose = cv2.GaussianBlur(dose, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)
    if config.factory_exposure_variation>0:
        # Factory lamp / mask exposure, not a random scanner overlay. Independent
        # deterministic identity leaves the photographed image's RNG untouched.
        seed=7183+10007*config.factory_batch+37*(config.frame_number or 0)
        rng=np.random.default_rng(seed)
        for side in range(2):
            field=rng.uniform(-1.,1.,(2,32)).astype(np.float32)
            field=cv2.resize(field,(width,rows),interpolation=cv2.INTER_CUBIC)
            dose[side*rows:(side+1)*rows]*=1+.45*config.factory_exposure_variation*np.clip(field,-1,1)
    # Rounded perforations, using a signed-distance field for one-pixel AA.
    for spec in layout.sprocket_holes:
        x0, y0, x1, y1 = spec.normalized_rect
        x0, x1, y0, y1 = x0*width, x1*width, y0*height, y1*height
        offset = 0 if y0 < rows else height - 2*rows
        y0 -= offset; y1 -= offset
        xa, xb = max(0, int(x0)-1), min(width, int(np.ceil(x1))+1)
        ya, yb = max(0, int(y0)-1), min(2*rows, int(np.ceil(y1))+1)
        radius = min(x1-x0, y1-y0) * .18
        xx = np.abs(np.arange(xa, xb, dtype=np.float32)+.5-(x0+x1)/2) - ((x1-x0)/2-radius)
        yy = np.abs(np.arange(ya, yb, dtype=np.float32)+.5-(y0+y1)/2) - ((y1-y0)/2-radius)
        distance = (np.hypot(np.maximum(xx[None, :], 0), np.maximum(yy[:, None], 0))
                    + np.minimum(np.maximum(xx[None, :], yy[:, None]), 0) - radius)
        holes[ya:yb, xa:xb] = np.maximum(holes[ya:yb, xa:xb], np.clip(.5-distance, 0, 1))
    if config.loading_offset:
        # Observe a shifted window of the strip. The incoming portion carries
        # the neighbouring frame's identity; no wrapped copy of the same number.
        shift=config.loading_offset*width
        neighbour=config.frame_number
        if neighbour is not None:neighbour=max(0,min(9999,neighbour+(-1 if shift>0 else 1)))
        adjacent,_=_strip_masks(replace(config,loading_offset=0.,frame_number=neighbour),layout,label)
        transform=np.array([[1.,0.,shift],[0.,1.,0.]],np.float32)
        dose=cv2.warpAffine(dose,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        holes=cv2.warpAffine(holes,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_WRAP)
        transform[0,2]=shift-width if shift>0 else shift+width
        dose+=cv2.warpAffine(adjacent,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    return dose, holes


def form_film_border(medium, config, snapshot, program_kind):
    """Append persisted reference strips after image formation, before scanning."""
    if not snapshot or not snapshot.get('enabled'):
        return medium
    border = _border_config(snapshot)
    layout = resolve_film_border_layout(border, medium.frame_shape)
    from film_foundry.core.film_process import integration
    processors = {
        'bw_negative': integration.develop_bw_negative_reduced,
        'bw_reversal': integration.develop_bw_reversal_reduced,
        'color_negative': integration.develop_color_negative_reduced,
        'color_reversal': integration.develop_color_reversal_reduced,
    }
    if program_kind not in processors:
        raise ValueError('Film rebate formation requires a supported silver-halide process')
    label = ''.join(c for c in (border.stock_label or config.film.name or 'FILM FOUNDRY').upper()
                    if c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -').strip()[:14] or 'FILM FOUNDRY'
    dose, holes = _strip_masks(border, layout, label)
    # A fixed dose ramp samples nonlinear stock response through the actual
    # baths. Camera shutter duration/EV and in-picture printing do not alter
    # factory exposure. No additional random draws touch the image identity.
    count = 257
    factory_exposure=1.8*2.**border.factory_exposure_ev
    dose_range=1+.45*border.factory_exposure_variation
    exposure = np.repeat(np.linspace(0, factory_exposure*dose_range, count, dtype=np.float32)[None, :, None], 3, axis=2)
    response = processors[program_kind](exposure, config.film, config.chemistry,
                                       retain_latent_fraction=False, retain_grain_moments=False)
    position = np.clip(dose/dose_range,0,1) * (count - 1)
    lo = position.astype(np.int32); hi = np.minimum(lo+1, count-1)
    fraction = (position-lo)[..., None]
    def lookup(array):
        table = np.asarray(array)[0]
        strip = table[lo] * (1-fraction) + table[hi] * fraction
        return np.asarray(strip, dtype=np.float32)
    rows = layout.image_rect[1]
    def surround(array, strip):
        if array is None or np.asarray(array).size == 0:
            return array
        return np.concatenate((strip[:rows], array, strip[rows:]), axis=0)
    optical_strip = lookup(response.optical_density_rgb)
    # A perforation transmits air, including partially covered edge pixels.
    optical_strip = -np.log10(holes[..., None] + (1-holes[..., None]) * np.power(10., -optical_strip))
    layers = getattr(response, 'density_cmy', None)
    if layers is None:
        layers = response.density_rgb
    layer_strip = lookup(layers) * (1-holes[..., None])
    kwargs = {name: surround(getattr(medium, name), layer_strip)
              for name in ('density_cmy', 'density_grain')}
    history_strip = np.repeat((dose * factory_exposure)[..., None], 3, axis=2)
    kwargs.update({name: surround(getattr(medium, name), history_strip)
                   for name in ('linear_input', 'after_mtf', 'after_halation')})
    kwargs['optical_density_rgb'] = surround(medium.optical_density_rgb, optical_strip)
    if medium.spectral_medium is not None:
        from film_foundry.core.spectral_optics import final_pool_amounts
        spectrum = medium.spectral_medium
        _, components = final_pool_amounts(response.process_result.final_medium)
        strip_components = lookup(components) * (1-holes[..., None])
        zeros = np.zeros(dose.shape+(3,), dtype=np.float32)
        kwargs['spectral_medium'] = replace(
            spectrum, component_amounts=surround(spectrum.component_amounts, strip_components),
            post_process_density_rgb=surround(spectrum.post_process_density_rgb, zeros),
            emulsion_coverage=surround(spectrum.emulsion_coverage, np.ones_like(dose)),
            covered_density_rgb=surround(spectrum.covered_density_rgb, zeros))
    metadata = dict(medium.metadata)
    barcode_sides = {'text_code': {'bottom'}, 'compact': {'bottom'}, 'cinema': {'bottom'},
                     'code_text': {'top'}, 'text_only': set(), 'roll_markings': set()}.get(border.factory_layout, {'top','bottom'})
    generated_sides = [spec.side for spec in layout.factory_edges
                       if spec.side in barcode_sides and not spec.pattern_code and border.factory_barcode != 'none']
    metadata[FORMED_FILM_REBATE_KEY] = {
        'model': FORMED_FILM_REBATE_MODEL, 'image_height': layout.image_rect[3],
        'rebate_rows': rows, 'holes_per_row': len(layout.sprocket_holes)//(2 if border.sprocket_rows == 'both' else 1),
        'label': label, 'order': 'image_perforations_factory_marks',
        'spatial_reference': 'uniform_material_response_lut_v1',
        'factory_response': 'material_exposure_table_v1',
        'factory_exposure_ev': border.factory_exposure_ev,
        'factory_exposure_variation': border.factory_exposure_variation,
        'placement_model': 'strip_loading_offset_v1',
        'block_pattern_model': 'fixed_motif_sequence_v1' if border.factory_barcode in {'blocks','staggered_blocks','paired_blocks'} else None,
        'loading': {'offset_frames':border.loading_offset,
                    'adjacent_frame_number': max(0,min(9999,border.frame_number+(-1 if border.loading_offset>0 else 1))) if border.loading_offset and border.frame_number is not None else None},
        'barcode': {'symbology':border.factory_barcode,'payload':f'{border.factory_batch:04d}{border.frame_number or 0:04d}' if border.factory_barcode=='code128c' and generated_sides else None,
                    'generated_sides':generated_sides,
                    'identifier_namespace':'film_foundry_batch_frame',
                    'custom_stencils_override':bool(border.factory_pattern_top or border.factory_pattern_bottom)},
        'image_rect': list(layout.image_rect), 'config': snapshot,
        'process_surface_domain': 'image_gate',
        'limitations': ['no_rebate_spatial_accidents', 'no_rebate_grain'],
    }
    return replace(medium, metadata=metadata, **kwargs)


def image_gate_rows(medium, config=None):
    """Meter the photographed area only; archived edges remain visible."""
    record = medium.metadata.get(FORMED_FILM_REBATE_KEY)
    if record is None:
        record = medium.metadata.get('developed_medium', {}).get(FORMED_FILM_REBATE_KEY)
    if not isinstance(record, dict) or record.get('model') != FORMED_FILM_REBATE_MODEL:
        return None
    rows, height = record.get('rebate_rows'), record.get('image_height')
    if (not isinstance(rows, int) or not isinstance(height, int) or rows < 1 or height < 1
            or height+2*rows != medium.frame_shape[0]):
        return None
    offset = 0
    if config is not None and config.scanner.include_clear_base_border:
        from film_foundry.core.electronic_negative import scanner_raw_export_border_width
        offset = scanner_raw_export_border_width(medium.frame_shape,
                    config.output.scanner_raw_border_percent, config.output.scanner_raw_border_min_px)
    return (rows+offset, rows+height+offset, offset, medium.frame_shape[1]+offset)
