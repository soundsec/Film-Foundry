"""Form optical grain from each developed coating, before combining its grain delta."""
from dataclasses import replace

import numpy as np

from .computation_control import checkpoint
from .density_grain import apply_density_grain
from .film_process.coatings import emulsions, record_indices
from .film_process.local_grain import LocalGrainResponse
from .film_process.formation_history import NAMES
from .silver_grain import apply_metallic_silver_grain, local_silver_parameters


def _seed(frame_seed, coating_id):
    return np.random.SeedSequence([int(frame_seed), *coating_id.encode("utf8")])


class CoatingGrain:
    def __init__(self, shape, film, emulsion_product, silver_enabled):
        self.shape = tuple(shape)
        self.film = film
        self.rows = emulsions(film)
        self.indices = record_indices(self.rows, film.effective_record_count)
        self.product = emulsion_product
        self.pools = {emulsion_product: {}}
        self.local = {}
        if silver_enabled:
            self.pools["silver"] = {}

    def capture(self, state, start):
        """Copy retained optical sources before the tile's chemical state is released."""
        for product, fields in self.pools.items():
            pool = state.dye if product == "dye" else state.metallic_silver
            if pool is None:
                continue
            for i in range(len(self.rows)):
                mass = pool[..., i]
                if not np.any(mass):
                    continue
                if i not in fields:
                    fields[i] = np.zeros(self.shape, np.float32)
                target = fields[i][start:start+mass.shape[0]]
                if state.emulsion_coverage is None:
                    target[:] = mass
                else:
                    np.divide(mass, state.emulsion_coverage, out=target, where=state.emulsion_coverage>0)
                history = state.formation_history
                if history is not None and f"{product}_grain_factor" in history:
                    key = product, i
                    if key not in self.local:
                        self.local[key] = [np.ones(self.shape,np.float32) for _ in NAMES[:3]]
                    for name, field in zip(NAMES[:3], self.local[key]):
                        part = field[start:start+mass.shape[0]]
                        np.divide(history[f"{product}_{name}"][...,i], mass, out=part, where=mass>0)

    def response(self, product, i):
        factors = self.local.get((product,i))
        if factors is not None:
            return LocalGrainResponse(product,*factors,self.pools[product][i])

    def emulsion_grain(self, formed, film, chemistry, rng, *, consume_input_as_effective_delta, **settings):
        development = settings.get("development_override")
        if development is None:
            from .development import build_effective_development
            development = build_effective_development(chemistry)
        # Work at unit coating amount so splitting a layer does not multiply
        # the frame variance: independent variances scale with coated amount.
        dmin = np.asarray(film.density_min, np.float32) + development.d_min_shift
        density_range = (np.asarray(film.density_max, np.float32) - np.asarray(film.density_min, np.float32)) * development.d_max_factor
        delta = np.zeros_like(formed)
        seed = rng.integers(2**32, dtype=np.uint32)
        mono = film.effective_record_count == 1
        for i, row in enumerate(self.rows):
            mass = self.pools[self.product].get(i)
            if mass is None:
                continue
            checkpoint()
            record = self.indices[i]
            amount = row["amount"]
            optical_mass = mass * np.float32(density_range[record])
            source = np.empty_like(formed)
            source[:] = (optical_mass / amount)[..., None]
            source += dmin
            options = dict(settings)
            local = self.response(self.product,i)
            if local is not None:
                options["local_response"] = local
            work = apply_density_grain(source, film, chemistry, np.random.default_rng(_seed(seed,row["id"])),
                                       consume_input_as_effective_delta=True, **options)
            noise = work[...,record]
            noise *= np.sqrt(amount)
            np.maximum(noise, -optical_mass, out=noise)
            noise[mass <= 0] = 0.
            if mono:
                delta += noise[...,None]
            else:
                delta[...,record] += noise
            del source, work, noise
        delta += formed
        np.maximum(delta, 0., out=delta)
        if consume_input_as_effective_delta:
            delta -= formed
        return delta

    def silver_grain(self, target, silver_density, plan):
        """Use the same layer-specific silver fields for RGB and spectral media."""
        fields = self.pools.get("silver", {})
        if not fields or plan is None:
            return False
        scale = np.zeros_like(silver_density)
        for field in fields.values():
            scale += field
        np.divide(silver_density, scale, out=scale, where=scale>0)
        applied = False
        for i,field in fields.items():
            local = self.response("silver",i)
            child = replace(plan, seed=int(_seed(plan.seed,self.rows[i]["id"]).generate_state(1)[0]),
                            local_parameters=plan.local_parameters if local is None else local_silver_parameters(self.film,local))
            for start in range(0,self.shape[0],plan.tile_rows):
                checkpoint()
                rows = slice(start,start+plan.tile_rows)
                mass = scale[rows] * field[rows]
                if target.ndim == 3:
                    applied |= apply_metallic_silver_grain(target[rows], mass, child, row_offset=start)
                else:
                    rgb = np.zeros((*mass.shape,3),np.float32)
                    applied |= apply_metallic_silver_grain(rgb, mass, child, row_offset=start)
                    target[rows] += rgb[...,0]
        return applied
