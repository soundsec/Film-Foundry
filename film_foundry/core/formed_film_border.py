"""Form film rebates from small material/process reference exposures.

The image gate is never recalculated or resampled. Rebate references use the
same reaction operators and stock coefficients. Incident leak light continues
from the gate; local bath defects, surface damage and grain remain gate-only. They become part of the stored optical/spectral medium, not a scan UI
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


REBATE_STRAY_RGB = np.array((.06, .003, .0008), dtype=np.float32)


def border_snapshot(config, edge_rgb=None):
    """Freeze only edge settings; in-picture imprint has already been exposed."""
    record = asdict(validate_film_border_config(config))
    for key in tuple(record):
        if key.startswith('imprint_'):
            del record[key]
    if record.get('factory_composition') is None:
        record.pop('factory_composition', None)
    if not record['rebate_glow']:
        del record['rebate_glow']
    if record['factory_mark_type'] != 'ink':
        del record['factory_ink_transmission_rgb']
    if edge_rgb is not None and np.any(edge_rgb):
        # Freeze the incident light alone, before clipping or mixing with the image.
        width = edge_rgb.shape[1]
        x = np.linspace(0, width-1, min(width,256)).round().astype(int)
        record['edge_leak_rgb_v1'] = edge_rgb[:, x].tolist()
    record['model'] = FILM_REBATE_EXPOSURE_MODEL
    return record


def validate_border_snapshot(record):
    if record is None:
        return None  # old latent: no edge exposure was recorded
    result = border_snapshot(_border_config(record))
    if 'edge_leak_rgb_v1' in record:
        rgb = np.asarray(record['edge_leak_rgb_v1'], dtype=np.float32)
        if (rgb.ndim != 3 or rgb.shape[0] != 2 or not 1 <= rgb.shape[1] <= 256 or rgb.shape[2] != 3
                or not np.isfinite(rgb).all() or rgb.min() < 0 or rgb.max() > 4):
            raise ValueError("Invalid frozen rebate light-leak exposure")
        result['edge_leak_rgb_v1'] = rgb.tolist()
    return result


def _border_config(record):
    values = dict(record)
    values.pop("edge_leak_rgb_v1", None)
    if values.pop('model', FILM_REBATE_EXPOSURE_MODEL) != FILM_REBATE_EXPOSURE_MODEL:
        raise ValueError('Unsupported film rebate exposure model')
    values.setdefault("lettering_size", "legacy" if "formation_mode" not in values else "format_mm")
    values.setdefault("factory_repeat", "per_gate")
    values.setdefault("transport_axis", "horizontal")
    values.setdefault("strip_layout", "frame_layout_v1")
    return validate_film_border_config(FilmBorderConfig(**values))


def _strip_number(config):
    return config.frame_number if config.frame_number is not None else config.strip_frame_index


def _adjacent_number(value, delta):
    return None if value is None else max(0,min(9999,value+delta))


def aperture_coverage(rect, width, height, *, rounding=.18, hard_edges=False):
    """Rasterize a rounded open window in its clipped pixel bounds."""
    x0, y0, x1, y1 = rect
    xa, xb = max(0, int(np.floor(x0))-1), min(width, int(np.ceil(x1))+1)
    ya, yb = max(0, int(np.floor(y0))-1), min(height, int(np.ceil(y1))+1)
    if xa >= xb or ya >= yb or x1 <= x0 or y1 <= y0:
        return None
    radius = min(x1-x0, y1-y0)*rounding
    xx = np.abs(np.arange(xa, xb, dtype=np.float32)+.5-(x0+x1)/2) - ((x1-x0)/2-radius)
    yy = np.abs(np.arange(ya, yb, dtype=np.float32)+.5-(y0+y1)/2) - ((y1-y0)/2-radius)
    distance = (np.hypot(np.maximum(xx[None, :], 0), np.maximum(yy[:, None], 0))
                + np.minimum(np.maximum(xx[None, :], yy[:, None]), 0) - radius)
    alpha = distance <= 0 if hard_edges else np.clip(.5-distance, 0, 1)
    return slice(ya, yb), slice(xa, xb), alpha


def half_frame_plate(border):
    if border.format_key != "half_frame" or border.factory_repeat != "stock_frame":
        return None
    slot = _strip_number(border)
    slot = 1 if slot is None else slot
    return (slot+1)//2, (slot-1)%2


def _strip_masks(config, layout, label):
    return _strip_fields(config, layout, label)[:2]


def ink_density(coverage, transmission):
    """Unresolved ink coverage mixes in transmitted light before conversion to density."""
    absorbed = 1 - np.asarray(transmission,dtype=np.float32)
    return -np.log10(np.maximum(1e-6,1-np.clip(coverage,0,1)[...,None]*absorbed))


def _glow_field(config, dose, holes, rows):
    """Two narrow strips; box integration is independent of the filter radius."""
    glow = np.empty_like(dose)
    radius = max(1, round(rows*.055))
    edge = np.maximum(0, 1-(np.arange(rows, dtype=np.float32)+.5)/max(1.,rows*.065))**2
    laser = config.factory_mark_type == 'laser' and config.factory_mark_spread > 0
    beam = 2.**config.factory_exposure_ev * config.factory_light_color_rgb[0]
    def scatter(band, r):
        return cv2.blur(band, (2*r+1, 2*r+1), borderType=cv2.BORDER_REPLICATE)
    for start in (0,rows):
        band = slice(start,start+rows)
        aperture = holes[band]
        rim = scatter(aperture, radius)*(1-aperture)*1.6
        field = rim + .55*(edge if start==0 else edge[::-1])[:,None]
        if laser and beam:
            marks = dose[band]
            skirt = scatter(marks,max(1,round(radius*config.factory_mark_spread)))
            field += beam*np.maximum(0,skirt-marks)
        glow[band] = np.clip(field,0,1)
    return glow


def _strip_fields(config, layout, label, *, with_glow=False):
    """Rasterize only two rebates, not a full-size temporary canvas."""
    width, height = layout.canvas_size
    rows = layout.image_rect[1]
    half = half_frame_plate(config)
    if half is not None:
        number, side = half
        # Plate percentages, lettering, blur and variation use the full eight-perf pitch.
        # The loading offset is entered in photographed gates, here half a factory repeat.
        stock = replace(config, format_key="135", transport_axis="horizontal",
                        frame_number=number if config.frame_number is not None else None,
                        strip_frame_index=number, loading_offset=config.loading_offset/2,
                        sprocket_count=8 if config.sprocket_count is None else config.sprocket_count*2)
        full = resolve_film_border_layout(stock, (layout.image_rect[3],2*width), strip_coordinates=True)
        plate, holes, glow = _strip_fields(stock, full, label, with_glow=with_glow)
        window = slice(side*width,(side+1)*width)
        return plate[:,window], holes[:,window], None if glow is None else glow[:,window]
    dose = np.zeros((2 * rows, width), dtype=np.float32)
    holes = np.zeros_like(dose)
    proportional = (config.lettering_size == 'format_mm' or config.factory_barcode != 'legacy' or
                    config.factory_layout not in {'legacy','text_code','combined','text_only'})
    def paste(stamp,x,y,side):
        low=0 if side=='top' else rows;high=low+rows
        xa=max(0,x);xb=min(width,x+stamp.shape[1]);ya=max(low,y);yb=min(high,y+stamp.shape[0])
        if xa<xb and ya<yb:
            np.maximum(dose[ya:yb,xa:xb],stamp[ya-y:yb-y,xa-x:xb-x],out=dose[ya:yb,xa:xb])
    # The factory plate is registered before the film moves. Archived v1
    # layouts retain their original individual mark offsets.
    fixed = config.strip_layout == 'registered_strip_v2'
    for spec in layout.factory_edges:
        if config.factory_composition is not None:
            from film_foundry.core.composed_film_plate import expose_slots
            _, y0, _, y1 = spec.normalized_rect
            offset = 0 if spec.side == "top" else height - 2 * rows
            top, bottom = round(y0 * height) - offset, round(y1 * height) - offset
            expose_slots(dose, config, spec, label, _strip_number(config), top, bottom - top)
            continue
        pattern = (EdgeMarkPattern.decode(spec.pattern_code) if spec.pattern_code
                   else factory_edge_pattern(label,config.factory_batch,_strip_number(config),config.factory_layout,config.factory_barcode,spec.side,config.factory_barcode_seed))
        grid = np.array([[pattern.ink(x, y) for x in range(pattern.width)]
                         for y in range(pattern.height)], dtype=np.float32)
        if spec.flip:
            grid = grid[::-1, ::-1]
        x0, y0, x1, y1 = spec.normalized_rect
        x0, x1 = round(x0 * width), round(x1 * width)
        offset = 0 if spec.side == 'top' else height - 2 * rows
        y0, y1 = round(y0 * height) - offset, round(y1 * height) - offset
        standalone_code = ((spec.side == 'bottom' and config.factory_layout in {'text_code','compact'})
                           or (spec.side == 'top' and config.factory_layout == 'code_text'))
        if fixed and standalone_code and not spec.pattern_code and config.factory_barcode not in {'legacy','none'}:
            x1 = min(x1, x0 + round(.64 * width))
        elif proportional:
            # New plates retain glyph/module proportions. Legacy layouts with
            # legacy bars retain the original full-band stretch for replay.
            x1 = min(x1, x0 + max(1, round((y1-y0)*pattern.width/pattern.height)))
        # Supersample the lettering footprint only; image pixels stay intact.
        if x1 > x0 and y1 > y0:
            stamp = cv2.resize(grid, ((x1-x0)*4, (y1-y0)*4), interpolation=cv2.INTER_NEAREST)
            stamp = cv2.resize(stamp, (x1-x0, y1-y0), interpolation=cv2.INTER_AREA)
            dx,dy=(0.,0.) if fixed else getattr(config,'factory_offset_'+spec.side)
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
                dx,dy=(0.,0.) if fixed else config.advance_marker_offset
                paste(mark,xa+round(dx*width),y0+round(dy*rows),'bottom')
    if config.factory_mark_type == 'laser' and config.factory_mark_spread > 0:
        sigma = max(.25, rows * .012) * config.factory_mark_spread
        if not fixed and any(value for offset in (config.factory_offset_top,config.factory_offset_bottom,config.advance_marker_offset) for value in offset):
            # The packed strips are not physically adjacent. A moved mark may
            # reach the inner edge, but must never diffuse into the other strip.
            dose=np.concatenate([cv2.GaussianBlur(dose[start:start+rows],(0,0),sigma,borderType=cv2.BORDER_CONSTANT) for start in (0,rows)])
        else:
            dose = cv2.GaussianBlur(dose, (0, 0), sigma, borderType=cv2.BORDER_CONSTANT)
    if config.factory_exposure_variation>0 and config.factory_mark_type in ('hard','laser'):
        # Factory lamp / mask exposure, not a random scanner overlay. Independent
        # deterministic identity leaves the photographed image's RNG untouched.
        seed=7183+10007*config.factory_batch+37*(_strip_number(config) or 0)
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
        footprint = aperture_coverage((x0, y0, x1, y1), width, 2*rows)
        if footprint is not None:
            ys, xs, alpha = footprint
            np.maximum(holes[ys, xs], alpha, out=holes[ys, xs])
    glow = _glow_field(config,dose,holes,rows) if with_glow and config.rebate_glow else None
    if config.loading_offset:
        # Observe a shifted window of the strip. The incoming portion carries
        # the neighbouring frame's identity; no wrapped copy of the same number.
        shift=config.loading_offset*width
        delta=-1 if shift>0 else 1
        neighbour=replace(config,loading_offset=0.,frame_number=_adjacent_number(config.frame_number,delta),
                          strip_frame_index=_adjacent_number(config.strip_frame_index,delta))
        adjacent,_,adjacent_glow=_strip_fields(neighbour,layout,label,with_glow=with_glow)
        transform=np.array([[1.,0.,shift],[0.,1.,0.]],np.float32)
        dose=cv2.warpAffine(dose,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        holes=cv2.warpAffine(holes,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_WRAP)
        if glow is not None:
            glow=cv2.warpAffine(glow,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        transform[0,2]=shift-width if shift>0 else shift+width
        dose+=cv2.warpAffine(adjacent,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
        if glow is not None:
            glow+=cv2.warpAffine(adjacent_glow,transform,(width,2*rows),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
    return dose, holes, glow


def form_film_border(medium, config, snapshot, program_kind):
    """Append persisted reference strips after image formation, before scanning."""
    if not snapshot or not snapshot.get('enabled'):
        return medium
    border = _border_config(snapshot)
    native = border.formation_mode != "attached"
    from .film_border import vertical_transport
    vertical = vertical_transport(border, medium.frame_shape[1], medium.frame_shape[0])
    layout = resolve_film_border_layout(border, medium.frame_shape, formed_shape=native, strip_coordinates=True)
    label = factory_label(border, config.film)
    dose, holes, glow = _strip_fields(border, layout, label, with_glow=not native)
    if native:
        if medium.optical_density_rgb is None:
            raise ValueError("Native film rebates require a silver-halide optical master")
        from .native_film_strip import finish_strip
        optical = finish_strip(medium, config, border, dose, holes)
        return record_strip(optical, border, layout, label, snapshot)
    from film_foundry.core.film_process import integration
    processors = {
        'bw_negative': integration.develop_bw_negative_reduced,
        'bw_reversal': integration.develop_bw_reversal_reduced,
        'color_negative': integration.develop_color_negative_reduced,
        'color_reversal': integration.develop_color_reversal_reduced,
    }
    if program_kind not in processors:
        raise ValueError('Film rebate formation requires a supported silver-halide process')
    # A fixed dose ramp samples nonlinear stock response through the actual
    # baths. Camera shutter duration/EV and in-picture printing do not alter
    # factory exposure. No additional random draws touch the image identity.
    etched = border.factory_mark_type == "laser_etched"
    ink = border.factory_mark_type == "ink"
    count = 1 if etched or ink else 257
    factory_exposure=1.8*2.**border.factory_exposure_ev
    dose_range=1+.45*border.factory_exposure_variation
    exposure = np.repeat(np.linspace(0, factory_exposure*dose_range, count, dtype=np.float32)[None, :, None], 3, axis=2)
    lamp=np.asarray(border.factory_light_color_rgb,dtype=np.float32)
    exposure*=lamp
    leak = snapshot.get('edge_leak_rgb_v1')
    if leak is not None:
        leak = np.asarray(leak, np.float32)
        exposure = exposure + leak.reshape(-1, 1, 3)
    if glow is not None:
        exposure = np.concatenate((exposure, exposure + border.rebate_glow*REBATE_STRAY_RGB), axis=1)
    chemistry = config.chemistry
    timeline = chemistry.process_events
    if timeline and any(event['kind'] in ('emulsion_wear', 'emulsion_shear') for event in timeline['events']):
        # Mechanical damage needs material coordinates. The reference ramp
        # describes only the common baths; the image keeps its original timeline.
        baths = [event for event in timeline['events'] if event['kind'] not in ('emulsion_wear', 'emulsion_shear')]
        chemistry = replace(chemistry, process_events={**timeline, 'events': baths} if baths else None)
    response = processors[program_kind](exposure, config.film, chemistry,
                                       retain_latent_fraction=False, retain_grain_moments=False)
    position = np.clip(dose/dose_range,0,1) * (count - 1)
    lo = position.astype(np.int32); hi = np.minimum(lo+1, count-1)
    fraction = (position-lo)[..., None]
    rows = layout.image_rect[1]
    if leak is not None:
        # Each sample column has its own material response across the factory lamp ramp.
        # Gate boundary irradiance continues outwards on the corresponding emulsion strip.
        n = leak.shape[1]
        xx = np.linspace(0, n-1, dose.shape[1], dtype=np.float32)
        ix = xx.astype(np.intp)
        jx = np.minimum(ix+1, n-1)
        dx = (xx-ix).astype(np.float32)[None, :, None]
    def interpolate(array):
        if leak is not None:
            table = np.asarray(array)
            out = np.empty((*dose.shape, table.shape[-1]), dtype=np.float32)
            for side in range(2):
                for start in range(side*rows, (side+1)*rows, 32):
                    sl = slice(start, min(start+32, (side+1)*rows))
                    a, b, f = lo[sl], hi[sl], fraction[sl]
                    def column(x):
                        left = table[side*n+x,a]
                        return left + (table[side*n+x,b]-left)*f
                    left = column(ix)
                    out[sl] = left + (column(jx)-left)*dx
            return out
        table = np.asarray(array)[0]
        strip = table[lo] * (1-fraction) + table[hi] * fraction
        return np.asarray(strip, dtype=np.float32)
    def lookup(array):
        normal = interpolate(array[:, :count])
        if glow is not None:
            if leak is None:
                active = glow > 0
                table = array[0,count:]
                low = table[lo[active]]
                warm = low + (table[hi[active]]-low)*fraction[active]
                normal[active] += (warm-normal[active])*glow[active,None]
            else:
                normal += (interpolate(array[:, count:])-normal)*glow[..., None]
        return normal
    def surround(array, strip):
        if array is None or np.asarray(array).size == 0:
            return array
        if vertical:
            return np.concatenate((np.rot90(strip[rows:],-1), array, np.rot90(strip[:rows],-1)), axis=1)
        return np.concatenate((strip[:rows], array, strip[rows:]), axis=0)
    optical_strip = lookup(response.optical_density_rgb)
    pigment = ink_density(dose,border.factory_ink_transmission_rgb) if ink else None
    if ink:
        optical_strip += pigment
    coverage = 1 - np.clip(dose, 0, 1) if etched else None
    if etched:
        from film_foundry.core.emulsion_integrity import mix_density
        final = response.process_result.final_medium
        support = np.asarray(final.clear_support_density_rgb if final.clear_support_density_rgb is not None
                             else final.base_density_rgb) + np.asarray(final.auxiliary_density_rgb) * final.auxiliary_remaining
        optical_strip = mix_density(optical_strip, support, coverage)
    # A perforation transmits air, including partially covered edge pixels.
    optical_strip = -np.log10(holes[..., None] + (1-holes[..., None]) * np.power(10., -optical_strip))
    layers = getattr(response, 'density_cmy', None)
    if layers is None:
        layers = response.density_rgb
    layer_strip = lookup(layers) * (1-holes[..., None])
    if etched:
        layer_strip *= coverage[..., None]
    kwargs = {name: surround(getattr(medium, name), layer_strip)
              for name in ('density_cmy', 'density_grain')}
    history_strip = np.repeat((dose * factory_exposure)[..., None], 3, axis=2)
    history_strip*=lamp
    if etched or ink:
        history_strip.fill(0)
    if glow is not None:
        history_strip += glow[...,None]*(border.rebate_glow*REBATE_STRAY_RGB)
    if leak is not None:
        for side in range(2):
            row = leak[side,ix] + (leak[side,jx]-leak[side,ix])*dx[0]
            history_strip[side*rows:(side+1)*rows] += row
    if leak is not None or glow is not None:
        if etched:
            history_strip *= coverage[..., None]
        history_strip *= 1-holes[..., None]
    kwargs.update({name: surround(getattr(medium, name), history_strip)
                   for name in ('linear_input', 'after_mtf', 'after_halation')})
    kwargs['optical_density_rgb'] = surround(medium.optical_density_rgb, optical_strip)
    if medium.spectral_medium is not None:
        from film_foundry.core.spectral_optics import final_pool_amounts
        spectrum = medium.spectral_medium
        _, components = final_pool_amounts(response.process_result.final_medium)
        strip_components = lookup(components) * (1-holes[..., None])
        zeros = np.zeros(dose.shape+(3,), dtype=np.float32)
        gate_coverage = spectrum.emulsion_coverage
        if etched and gate_coverage is None:
            gate_coverage = np.ones(medium.frame_shape[:2], dtype=np.float32)
        surface = spectrum.post_process_density_rgb
        if ink and surface is None:
            surface = np.zeros(medium.frame_shape, dtype=np.float32)
        kwargs['spectral_medium'] = replace(
            spectrum, component_amounts=surround(spectrum.component_amounts, strip_components),
            post_process_density_rgb=surround(surface, pigment*(1-holes[...,None]) if ink else zeros),
            emulsion_coverage=surround(gate_coverage, coverage if etched else np.ones_like(dose)),
            covered_density_rgb=surround(spectrum.covered_density_rgb, zeros))
    return record_strip(replace(medium, **kwargs), border, layout, label, snapshot)


def factory_label(border, film):
    max_label = 32 if border.factory_composition is not None else 14
    return ''.join(c for c in (border.stock_label or film.name or 'FILM FOUNDRY').upper()
                    if c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -').strip()[:max_label] or 'FILM FOUNDRY'


def record_strip(medium, border, layout, label, snapshot):
    rows = layout.image_rect[1]
    native = border.formation_mode != "attached"
    leak = snapshot.get("edge_leak_rgb_v1")
    etched = border.factory_mark_type == "laser_etched"
    metadata = dict(medium.metadata)
    barcode_sides = {'text_code': {'bottom'}, 'compact': {'bottom'}, 'cinema': {'bottom'},
                     'code_text': {'top'}, 'text_only': set(), 'roll_markings': set()}.get(border.factory_layout, {'top','bottom'})
    generated_sides = [spec.side for spec in layout.factory_edges
                       if spec.side in barcode_sides and not spec.pattern_code and border.factory_barcode != 'none']
    if border.factory_composition is not None:
        generated_sides = [spec.side for spec in layout.factory_edges
                           if border.factory_barcode != "none" and
                           any(slot[0] == "barcode" for slot in border.factory_composition[spec.side])]
    from .film_border import clockwise_window
    vertical = border.transport_axis == "vertical"
    windows = [h.normalized_rect for h in layout.sprocket_holes]
    rect = layout.image_rect
    if vertical:
        windows = [clockwise_window(rect) for rect in windows]
        rect = (rows, 0, rect[3], rect[2])
    half = half_frame_plate(border)
    factory_number = half[0] if half else _strip_number(border)
    metadata[FORMED_FILM_REBATE_KEY] = {
        **({'factory_window': {'frame': half[0], 'half': half[1]+1}} if half else {}),
        'transport_axis': border.transport_axis,
        'model': FORMED_FILM_REBATE_MODEL, 'image_height': layout.image_rect[3],
        'rebate_rows': rows, 'holes_per_row': len(layout.sprocket_holes)//(2 if border.sprocket_rows == 'both' else 1),
        'perforation_windows_v1': [list(rect) for rect in windows],
        'label': label, 'order': 'image_perforations_factory_marks',
        'formation_mode': border.formation_mode,
        'spatial_reference': ('full_strip_v1' if native else 'edge_irradiance_continuation_v1' if leak is not None
                              else 'uniform_material_response_lut_v1'),
        'factory_response': 'surface_ink_transmission_v1' if border.factory_mark_type == 'ink' else 'bare_support_ablation_v1' if etched else 'full_strip_exposure_v1' if native else 'material_exposure_table_v1',
        'factory_exposure_ev': border.factory_exposure_ev,
        'factory_light_color_rgb': list(border.factory_light_color_rgb),
        'factory_exposure_variation': border.factory_exposure_variation,
        'placement_model': 'strip_loading_offset_v1',
        'block_pattern_model': 'fixed_motif_sequence_v1' if border.factory_barcode in {'blocks','staggered_blocks','paired_blocks'} else None,
        'loading': {'offset_frames':border.loading_offset,
                    'adjacent_frame_number': _adjacent_number(_strip_number(border),-1 if border.loading_offset>0 else 1) if border.loading_offset else None},
        'barcode': {'symbology':border.factory_barcode,'payload':f'{border.factory_batch:04d}{factory_number or 0:04d}' if border.factory_barcode=='code128c' and generated_sides else None,
                    'generated_sides':generated_sides,
                    'identifier_namespace':'film_foundry_batch_frame',
                    'custom_stencils_override':border.factory_composition is None and bool(border.factory_pattern_top or border.factory_pattern_bottom)},
        'image_rect': list(rect), 'config': snapshot,
        'process_surface_domain': 'full_strip' if native else 'image_gate',
        'light_leak_domain': 'full_strip' if native else 'edge_irradiance_continuation_v1' if leak is not None else 'none',
        'limitations': [] if native else ['no_rebate_spatial_bath_or_surface_accidents', 'no_rebate_grain'],
    }
    return replace(medium, metadata=metadata)


def image_gate_rows(medium, config=None):
    """Meter the photographed area only; archived edges remain visible."""
    record = medium.metadata.get(FORMED_FILM_REBATE_KEY)
    if record is None:
        record = medium.metadata.get('developed_medium', {}).get(FORMED_FILM_REBATE_KEY)
    if not isinstance(record, dict) or record.get('model') != FORMED_FILM_REBATE_MODEL:
        return None
    rows, height = record.get('rebate_rows'), record.get('image_height')
    vertical = record.get('transport_axis') == 'vertical'
    if (not isinstance(rows, int) or not isinstance(height, int) or rows < 1 or height < 1
            or height+2*rows != medium.frame_shape[1 if vertical else 0]):
        return None
    offset = 0
    if config is not None and config.scanner.include_clear_base_border:
        from film_foundry.core.electronic_negative import scanner_raw_export_border_width
        offset = scanner_raw_export_border_width(medium.frame_shape,
                    config.output.scanner_raw_border_percent, config.output.scanner_raw_border_min_px)
    if vertical:
        return (offset,medium.frame_shape[0]+offset,rows+offset,rows+height+offset)
    return (rows+offset, rows+height+offset, offset, medium.frame_shape[1]+offset)
