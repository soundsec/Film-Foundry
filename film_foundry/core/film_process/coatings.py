"""Ordered camera-side transport, independent emulsion pools and optical records."""
from dataclasses import replace
from functools import lru_cache

import numpy as np

from film_foundry.contracts.coating_stack import RECORDS
from ..computation_control import checkpoint
from ..spectral_exposure import compile_spectral_exposure, map_layer_exposure, map_spectral_coefficients


def emulsions(film):
    return [row for row in film.coating_stack["layers"] if row["kind"] == "emulsion"]


def record_indices(rows, count):
    return [0 if count == 1 else RECORDS.index(row["record"]) for row in rows]


@lru_cache(maxsize=128)
def receptor_coefficients(profile, record, band, curve, filters):
    source = profile.wavelengths_nm
    if (not curve and not band and 380 <= source[0] and source[-1] <= 780 and
        all(all(v == points[0][1] for _,v in points) for points in filters)):
        scale = np.prod([points[0][1] for points in filters])
        return (scale * np.asarray(compile_spectral_exposure(profile)[record], np.float32)).astype(np.float32)[None,:]
    knots = set(source).union(band, (x for x,_ in curve))
    for points in filters:
        knots.update(x for x,_ in points)
    lo, hi = band or (source[0], source[-1])
    edges = np.array(sorted(x for x in knots if lo <= x <= hi and source[0] <= x <= source[-1]))
    if len(edges) < 2:
        return None
    # Integrate inside each knot interval: two crossing ramps can overlap
    # throughout an interval even when their product is zero at both ends.
    nodes, weights = np.polynomial.legendre.leggauss(min(8, (len(filters)+4)//2))
    half_width = np.diff(edges)[:,None] * .5
    wave = (edges[:-1,None] + half_width * (1.+nodes)).ravel()
    weights = (half_width * weights).ravel()
    def sample(points):
        nm, values = zip(*points)
        return np.interp(wave, nm, values, left=0., right=0.)
    if curve:
        response = sample(curve)
        x,y = np.array(curve).T
        area = np.dot(np.diff(x), (y[1:] + y[:-1]) * .5)
        if area == 0:
            return None
        gain = np.max(y) / area
    elif band:
        response = np.ones_like(wave)
        gain = 1. / (hi-lo)
    else:
        response = np.interp(wave, source, profile.layer_sensitivity[record])
        gain = profile.layer_gain[record]
    for points in filters:
        response *= sample(points)
    basis = np.array([np.interp(wave, source, values) for values in profile.rgb_basis])
    weighted = basis * response * gain
    return (weighted @ weights).astype(np.float32)[None,:]


def received_light(light, film, row, count, record):
    profile = film.spectral_exposure_profile
    curve = tuple(map(tuple, row.get("spectrum", ())))
    band = tuple(row.get("band_nm", ()))
    filters = row.get("_filters", ())
    if profile is not None and (curve or band or filters and "sensitivity_rgb" not in row):
        indices = (0,) if profile.layer_count == 1 or curve or band else range(3) if count == 1 else (record,)
        result = np.zeros(light.shape[:-1], np.float32)
        for i in indices:
            mapped = receptor_coefficients(profile, i, band, curve, filters)
            if mapped is not None:
                result += map_spectral_coefficients(light, mapped, profile.model)[..., 0]
        return result / len(indices)
    if "sensitivity_rgb" in row:
        return np.einsum("...c,c->...", np.maximum(light, 0.), np.asarray(row["sensitivity_rgb"], np.float32))
    if count == 1:
        return map_layer_exposure(light, film).mean(axis=-1)
    return map_layer_exposure(light, film, layer=record)[..., 0]


def optical_path(film, side):
    """Mid-depth transmission to each receptor, full coating OD to the next."""
    gain = np.ones(3, np.float32)
    fallback = np.ones(3, np.float32)
    filters = ()
    layers = film.coating_stack["layers"]
    for row in (reversed(layers) if side == "base" else layers):
        density = row["density_rgb"]
        if row["kind"] == "support":
            from .integration import _base_and_mask_density
            density, _ = _base_and_mask_density(film)
        transmission = np.power(10., -np.asarray(density, np.float32))
        if row["kind"] == "filter" and "spectrum" in row and film.spectral_exposure_profile is not None:
            filters += (tuple(map(tuple, row["spectrum"])),)
            fallback *= transmission
            continue
        if row["kind"] == "emulsion":
            local_gain = gain * np.sqrt(transmission)
            if filters and "sensitivity_rgb" in row and "spectrum" not in row:
                local_gain *= fallback
            yield ({**row, "_filters": filters} if filters else row), local_gain
        gain *= transmission


def physical_material(material, rows):
    indices = record_indices(rows, material.layer_count)
    def mapped(values):
        return None if values is None else tuple(values[i] for i in indices)
    vectors = {key: mapped(getattr(material, key)) for key in (
        "latent_gamma", "latent_density_range", "latent_log_exposure_toe",
        "latent_log_exposure_shoulder", "latent_extreme_reversal_start_loge",
        "silver_density_per_layer", "residual_halide_density_per_layer", "bleached_halide_density_per_layer")}
    for key in ("halide_capacity", "coupler_capacity"):
        values = mapped(getattr(material, key))
        vectors[key] = None if values is None else tuple(v*r["amount"] for v, r in zip(values, rows))
    if vectors["coupler_capacity"] is not None:
        vectors["coupler_capacity"] = tuple(v*r["coupler_fraction"] for v, r in zip(vectors["coupler_capacity"], rows))
    matrix = material.dye_absorption_matrix
    return replace(material, layer_count=len(rows), coating_ids=tuple(row["id"] for row in rows), **vectors,
                   dye_absorption_matrix=None if matrix is None else tuple(mapped(v) for v in matrix))


def _physical_doses(image, film, material, side, addition, duration, rows, indices):
    from .integration import _material_degradation_state
    from .reciprocity import layer_efficiency
    from .time_history import storage_response
    pool_index = {row["id"]: i for i, row in enumerate(rows)}
    dose = np.empty((*image.shape[:2], len(rows)), np.float32)
    _, speed, balance, _ = _material_degradation_state(film)
    balance = [np.mean(balance)] if material.layer_count == 1 else balance
    efficiency = layer_efficiency(film.reciprocity_response, duration, material.layer_count)
    stored = storage_response(film, material.layer_count)
    if addition is not None:
        if hasattr(addition, "add_scaled_to"):
            extra = np.zeros((*image.shape[:2], material.layer_count), np.float32)
            addition.add_scaled_to(extra, np.ones(material.layer_count, np.float32))
        else:
            extra = np.asarray(addition, np.float32)
        if extra.shape != (*image.shape[:2], material.layer_count) or not np.isfinite(extra).all() or np.min(extra) < 0:
            raise ValueError("Invalid stock-record exposure addition")
    for row, gain in optical_path(film, side):
        checkpoint()
        i = pool_index[row["id"]]
        record = indices[i]
        for start in range(0, image.shape[0], 128):
            checkpoint()
            exposure = received_light(image[start:start+128] * gain, film, row, material.layer_count, record)
            if addition is not None:
                exposure += extra[start:start+128, :, record]
            dose[start:start+128, :, i] = exposure * (2.**row["speed_ev"] * speed * balance[record])
        if efficiency is not None:
            dose[..., i] *= efficiency[record]
        if stored is not None:
            dose[..., i] *= stored[0][record]
    return dose, None if stored is None else stored[1][indices]


def _stock_doses(image, film, material, side, addition, duration, rows, indices):
    """Reuse the calibrated stock records for coatings receiving the same light."""
    from .integration import stock_record_exposure
    dose = np.empty((*image.shape[:2], len(rows)), np.float32)
    pool_index = {row["id"]: i for i, row in enumerate(rows)}
    previous = None
    for row, gain in optical_path(film, side):
        checkpoint()
        sensitivity = tuple(row.get("sensitivity_rgb", ()))
        key = tuple(gain), sensitivity
        if key != previous:
            source = image if np.all(gain == 1.) else image * gain
            receptor = (replace(film, spectral_exposure_profile=None, layer_sensitivity_matrix=(sensitivity,)*3,
                                coating_stack=None)
                        if sensitivity else film)
            records, fog = stock_record_exposure(source, receptor, material.layer_count, addition, duration)
            previous = key
        i = pool_index[row["id"]]
        np.multiply(records[..., indices[i]], 2.**row["speed_ev"], out=dose[..., i])
    return dose, None if fog is None else fog[indices]


def coating_exposure(image, film, material, side, addition, duration):
    film.require_separated_coatings()
    rows = emulsions(film)
    indices = record_indices(rows, material.layer_count)
    mapper = _physical_doses if film.physical_coating_optics else _stock_doses
    dose, fog = mapper(image, film, material, side, addition, duration, rows, indices)
    return physical_material(material, rows), dose, fog


def sum_by_record(pool, indices, count):
    if pool is None:
        return None
    out = np.zeros((*pool.shape[:-1], count), np.float32)
    for i, record in enumerate(indices):
        out[..., record] += pool[..., i]
    return out


class CoatingRates:
    def __init__(self, source, indices):
        self.source = source
        self.indices = indices

    def __getattr__(self, name):
        return getattr(self.source, name)

    def _mapped(self, value):
        if value is not None and np.ndim(value) == 3 and value.shape[-1] > 1:
            return np.take(value, self.indices, axis=-1)
        return value

    def rate_for_step(self, label, action):
        fn = getattr(self.source, "rate_for_step", None)
        return self._mapped(fn(label, action) if fn else self.source)

    def fog_rate_for_step(self, label, action):
        fn = getattr(self.source, "fog_rate_for_step", None)
        return self._mapped(fn(label, action)) if fn else self.rate_for_step(label, action)


def bath_parameters(film, material, program, compatibility, rates):
    from .operators import FilmProcessAction
    rows = emulsions(film)
    indices = record_indices(rows, material.layer_count)
    def mapped(values):
        return None if values is None else tuple(values[i] for i in indices)
    def physical_step(step):
        selectivity = mapped(step.layer_selectivity)
        if step.action in (FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR):
            source = selectivity or (1.,)*len(rows)
            selectivity = tuple(v*r["development_rate"] for v,r in zip(source, rows))
        return replace(step, layer_selectivity=selectivity, dye_coupling_ratio=mapped(step.dye_coupling_ratio))
    steps = tuple(physical_step(step) for step in program.steps)
    physical = physical_material(material, rows)
    if physical.time_response is not None:
        compatibility = replace(compatibility, time_response={**physical.time_response, "_coating_records": indices,
                                                               "_mono_record": material.layer_count == 1})
        physical = replace(physical, time_response=None)
    if rates is not None:
        rates = CoatingRates(rates, indices)
    return physical, replace(program, steps=steps), replace(compatibility, layer_balance=mapped(compatibility.layer_balance)), rates


def develop(film, material, state, program, compatibility, **options):
    from .operators import apply_process_program
    if not film.ordered_coatings:
        return apply_process_program(material, state, program, compatibility, **options)
    indices = record_indices(emulsions(film), material.layer_count)
    physical, program, compatibility, options["local_development_rate"] = bath_parameters(
        film, material, program, compatibility, options.get("local_development_rate"))
    result = apply_process_program(physical, state, program, compatibility, **options)
    # Finalization may have normalized covered areas. Fold its optical sources
    # independently of the actual surviving chemical inventory.
    final = result.final_medium
    def fold(pool):
        return sum_by_record(pool, indices, material.layer_count)
    return replace(result, final_medium=replace(final,
                   metallic_silver=fold(final.metallic_silver), dye=fold(final.dye),
                   residual_halide=fold(final.residual_halide), bleached_halide=fold(final.bleached_halide),
                   dye_absorption_matrix=material.dye_absorption_matrix, silver_density_per_layer=material.silver_density_per_layer,
                   residual_halide_density_per_layer=material.residual_halide_density_per_layer,
                   bleached_halide_density_per_layer=material.bleached_halide_density_per_layer))
