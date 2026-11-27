"""Expose a scene, factory lamp and aperture on a common piece of stock."""
from dataclasses import replace

import numpy as np

from .film_border import apply_edge_imprint_to_linear
from .formed_film_border import REBATE_STRAY_RGB, _strip_fields, factory_label, ink_density
from .emulsion_integrity import mix_density


def bands(height, rows):
    return ((slice(0, rows), slice(0, rows)),
            (slice(height-rows, height), slice(rows, 2*rows)))


def expose_strip(image, config, layout):
    border = config.film_border
    rows = layout.image_rect[1]
    actual_image = image
    vertical = border.transport_axis == "vertical"
    if vertical:
        image = np.rot90(image)
    scene = image if border.formation_mode == "sprocket_panorama" else image[rows:-rows]
    if vertical:
        scene = np.rot90(scene, -1)
    imprinted, imprint = apply_edge_imprint_to_linear(scene, border)
    scene[:] = imprinted
    dose, holes, glow = _strip_fields(border, layout, factory_label(border, config.film), with_glow=True)
    lamp = np.asarray(border.factory_light_color_rgb, np.float32)*(1.8*2**border.factory_exposure_ev)
    for target, belt in bands(len(image), rows):
        if border.factory_mark_type in ("hard", "laser"):
            image[target] += dose[belt, :, None]*lamp
        if glow is not None:
            image[target] += glow[belt, :, None]*(border.rebate_glow*REBATE_STRAY_RGB)
        image[target] *= 1-holes[belt, :, None]
    return actual_image, imprint, holes


def punch_latent(state, holes, start=0, full_height=None, *, vertical=False):
    count = state.halide.shape[0]
    def clear(target, air):
        for field in (state.halide, state.developability, state.original_developability, state.coupler):
            if field is not None:
                field[target][air] = 0.
    if vertical:
        rows = len(holes)//2
        local = np.rot90(holes[:, start:start+count], -1) >= 1.
        clear((slice(None), slice(0, rows)), local[:, :rows])
        clear((slice(None), slice(-rows, None)), local[:, rows:])
    else:
        for target, belt in bands(full_height or count, len(holes)//2):
            a, b = max(start, target.start), min(start+count, target.stop)
            if a < b:
                offset = belt.start-target.start
                clear(slice(a-start,b-start), holes[a+offset:b+offset] >= 1.)
    return state


def finish_strip(medium, config, border, dose, holes):
    """Retain the developed strip; remove punched support and etched emulsion."""
    rows = len(dose)//2
    etched = border.factory_mark_type == "laser_etched"
    ink = border.factory_mark_type == "ink"
    support = None
    if etched:
        from .film_process.integration import _base_and_mask_density
        base, _ = _base_and_mask_density(config.film)
        remaining = medium.metadata["film_process_model"]["pool_totals"]["auxiliary_remaining"]
        support = np.asarray(base) + np.asarray(config.film.auxiliary_layer_density_rgb)*remaining
    optical = medium.optical_density_rgb.copy()
    fields = {}
    for name in ("density_cmy", "density_grain"):
        array = getattr(medium, name)
        if array.size:
            fields[name] = array.copy()
    spectrum = medium.spectral_medium
    components = coverage = covered = surface = None
    if spectrum is not None:
        components = spectrum.component_amounts.copy()
        if spectrum.post_process_density_rgb is not None:
            surface = spectrum.post_process_density_rgb.copy()
        elif ink:
            surface = np.zeros_like(optical)
        if spectrum.covered_density_rgb is not None:
            covered = spectrum.covered_density_rgb.copy()
        if etched:
            coverage = (np.ones(optical.shape[:2], np.float32) if spectrum.emulsion_coverage is None
                        else spectrum.emulsion_coverage.copy())
    def strip(array):
        return np.rot90(array) if border.transport_axis == "vertical" else array
    for target, belt in bands(len(strip(optical)), rows):
        material = 1-holes[belt]
        intact = 1-np.clip(dose[belt], 0, 1) if etched else 1.
        if ink:
            pigment = ink_density(dose[belt],border.factory_ink_transmission_rgb)
            strip(optical)[target] += pigment
            if surface is not None:
                strip(surface)[target] += pigment
        if etched:
            strip(optical)[target] = mix_density(strip(optical)[target], support, intact)
            if coverage is not None:
                strip(coverage)[target] *= intact
        strip(optical)[target] = mix_density(strip(optical)[target], 0., material)
        for array in fields.values():
            strip(array)[target] *= (material*intact)[..., None]
        if components is not None:
            strip(components)[target] *= material[..., None]
        if surface is not None:
            strip(surface)[target] *= (material*intact)[..., None]
        if covered is not None:
            strip(covered)[target] *= material[..., None]
    if spectrum is not None:
        fields["spectral_medium"] = replace(spectrum, component_amounts=components,
            post_process_density_rgb=surface, covered_density_rgb=covered,
            emulsion_coverage=spectrum.emulsion_coverage if coverage is None else coverage)
    return replace(medium, optical_density_rgb=optical, **fields)


def expose_unused_plate(blank, border, film):
    """An unused roll slot has its own factory number on unphotographed emulsion."""
    from .engine import expose_linear_to_latent
    from .film_border import resolve_film_border_layout
    from film_foundry.model.config import DarkroomConfig
    layout = resolve_film_border_layout(border, blank.halide.shape, formed_shape=True, strip_coordinates=True)
    dose, holes, glow = _strip_fields(border, layout, factory_label(border, film), with_glow=True)
    exposed = border.factory_mark_type in ("hard","laser")
    if (exposed and np.any(dose)) or glow is not None:
        lamp = np.asarray(border.factory_light_color_rgb, np.float32)*(1.8*2**border.factory_exposure_ev)
        stock = DarkroomConfig(film=film, enable_grain=False, enable_halation=False, enable_mtf=False)
        light = dose[...,None]*lamp if exposed else np.zeros((*dose.shape,3),np.float32)
        if glow is not None:
            light += glow[...,None]*(border.rebate_glow*REBATE_STRAY_RGB)
        stamp = expose_linear_to_latent(light, stock, np.random.default_rng(0))
        developability = np.rot90(blank.developability) if border.transport_axis == "vertical" else blank.developability
        for target, belt in bands(len(developability), len(holes)//2):
            active = np.any(light[belt] > 0, axis=-1)
            developability[target][active] = stamp.developability[belt][active]
    punch_latent(blank, holes, vertical=border.transport_axis == "vertical")
