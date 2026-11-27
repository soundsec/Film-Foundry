"""Read the open scanner lamp through the punched windows of a formed film."""
import numpy as np

from film_foundry.contracts.data_kernel import FORMED_FILM_REBATE_KEY, FORMED_FILM_REBATE_MODEL
from film_foundry.core.formed_film_border import aperture_coverage
from film_foundry.core.color import linear_to_srgb
from film_foundry.core.scanner import capture_optical_density, transmission_illuminant_rgb


def _legacy_windows(medium, record, width, height, rebate):
    if record.get("config"):
        from film_foundry.core.formed_film_border import _border_config
        from film_foundry.core.film_border import resolve_film_border_layout
        layout = resolve_film_border_layout(_border_config(record["config"]), (height - 2*rebate, width, 3))
        return [hole.normalized_rect for hole in layout.sprocket_holes]
    # Old phone archives omit the punched side. Read the actual clear runs
    # through the centre of each perforation belt, including clipped end holes.
    windows = []
    for top in (.38*rebate, height - .88*rebate):
        bottom = top + .5*rebate
        row = medium.optical_density_rgb[int((top+bottom)/2)]
        clear = np.all(row == 0, axis=-1)
        bounds = np.flatnonzero(np.diff(np.r_[False, clear, False])).reshape(-1, 2)
        if len(bounds) >= 2 and bounds[0, 0] == 0 and bounds[-1, 1] == width:
            # A single hole straddles the window. Recover its original curvature.
            bounds[-1, 1] += bounds[0, 1]
            bounds = bounds[1:]
        for left, right in bounds:
            if right-left <= max(2, width*.60/record["holes_per_row"]):
                windows.append((left/width, top/height, right/width, bottom/height))
    return windows


def show_open_gate(output, medium, scanner):
    """Composite only the aperture footprint before output crop/straightening."""
    if medium is None:
        return output
    record = medium.metadata.get(FORMED_FILM_REBATE_KEY) or medium.metadata.get(
        "developed_medium", {}).get(FORMED_FILM_REBATE_KEY)
    if not record or record.get("model") != FORMED_FILM_REBATE_MODEL or not record.get("holes_per_row"):
        return output
    height, width = medium.frame_shape[:2]
    rebate = record["rebate_rows"]
    vertical = record.get("transport_axis") == "vertical"
    if (width if vertical else height) != 2*rebate + record["image_height"]:
        return output
    windows = record.get("perforation_windows_v1")
    shift = record.get("perforation_shift_frames", record.get("loading", {}).get("offset_frames", 0.))
    if windows is None:
        windows = _legacy_windows(medium, record, width, height, rebate)
        if "config" not in record:
            shift = 0.  # measured in the already registered optical master
    lamp = linear_to_srgb(capture_optical_density(np.zeros((1, 1, 3), np.float32),
                          scanner, illuminant_rgb=transmission_illuminant_rgb(scanner)))[0, 0]
    dy, dx = (output.shape[0]-height)//2, (output.shape[1]-width)//2
    phone = "config" not in record
    film_output = output[dy:dy+height, dx:dx+width]
    for left, top, right, bottom in windows:
        for wrap in (-1, 0, 1):
            dx, dy = (0, shift+wrap) if vertical else (shift+wrap, 0)
            x0, x1 = (left+dx)*width, (right+dx)*width
            y0, y1 = (top+dy)*height, (bottom+dy)*height
            footprint = aperture_coverage((x0, y0, x1, y1), width, height,
                                          rounding=.20 if phone else .18, hard_edges=phone)
            if footprint is not None:
                ys, xs, alpha = footprint
                region = film_output[ys, xs]
                region += (lamp-region)*alpha[..., None]
    return output
