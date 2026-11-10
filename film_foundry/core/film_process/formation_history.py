"""Film bath products carry their formation response until they are removed.

The four moment fields per product are mass*grain gain/radius/clumping and
the chemical-fog subset. They are provenance, never extra material inventory.
One final grain pass uses retained-mass-weighted moments; this is not a
microscopic grain simulation or a new texture pass for every bath.
"""
from dataclasses import dataclass, replace
import numpy as np
from film_foundry.contracts.formation_history import enabled, MODEL
from film_foundry.core.development import build_effective_development, REACTION_RATE_PER_MINUTE

NAMES = ('grain_factor', 'grain_radius_factor', 'grain_clump_factor', 'fog')
POOLS = {'silver': 'metallic_silver', 'dye': 'dye'}


@dataclass(frozen=True, slots=True)
class BathFormationResponse:
    fog_dose: float
    grain_factor: float
    grain_radius_factor: float
    grain_clump_factor: float


def material_development(recipe):
    result = build_effective_development(recipe)
    # Chemical fog is real converted halide in the new path. Base material
    # density and the separately versioned ageing approximation stay intact.
    # Pool conversion already applies each bath's activity and depletion.
    # Multiplying all survivors by the first bath's D-max would impose that
    # bath again, including on products made only by a later developer.
    return replace(result, d_min_shift=0., developer_fog_shift=0., d_max_factor=1.) if enabled(recipe) else result


def attach_responses(program, recipe):
    if not enabled(recipe):
        if any((row.get('solution') or {}).get('formation_response') is not None
               for row in (*(recipe.process_steps or ()),*(recipe.bath_overrides or ()))):
            raise ValueError('药液颗粒与灰雾响应需要逐浴形成模型 / Solution formation response requires a bath-product model')
        return program
    if not program.steps:
        return program
    from film_foundry.contracts.process_steps import CONDITION_RANGES, CHEMISTRY_FIELDS
    from film_foundry.contracts.bath_overrides import merge_bath_overrides
    from film_foundry.core.bath_kinetics import solution_rate_per_second
    from film_foundry.contracts.bath_solution import reaction_capability,validate_solution,FORMATION_DEFAULTS
    from film_foundry.core.development import _developer_profile_for
    if recipe.process_steps is not None:
        rows = merge_bath_overrides(recipe.process_steps, recipe.bath_overrides)
    else:
        # Source bath indices survive optional coupled-operator expansion.
        rows = [{} for _ in range(1 + max(s.bath_index if s.bath_index is not None else i for i, s in enumerate(program.steps)))]
        for entry in recipe.bath_overrides or ():
            rows[entry['bath'] - 1].update(entry)
    steps = []
    for index, step in enumerate(program.steps):
        source = step.bath_index if step.bath_index is not None else index
        if not step.label:
            step = replace(step, label=f'bath_{source+1}:{step.action.value}')
        if step.action.value not in ('develop_silver', 'develop_color'):
            steps.append(step); continue
        row = rows[source]
        local = replace(recipe, **{k: row[k] for k in (*CONDITION_RANGES, *CHEMISTRY_FIELDS) if k in row})
        # Surface residues / retained-silver amount are not formation grain.
        intrinsic = replace(local, fixer_exhaustion=0., silver_retention=0., chemical_stain=0., uneven_development=0.)
        effect = build_effective_development(intrinsic)
        _, profile = _developer_profile_for(intrinsic)
        rate = REACTION_RATE_PER_MINUTE / 60. * max(effect.activity, 0.)
        solution=validate_solution(row.get('solution'),step.action.value)
        if solution:
            rate = solution_rate_per_second(reaction_capability(solution, step.action.value), local)
        start, end = step.reaction_window or (0., 1.)
        stress = (1. + 4.*max(local.developer_exhaustion, 0.)
                  + .10*max(local.temperature_c-20., 0.)
                  + .5*max(local.concentration-1., 0.) + .35*max(local.push_stops, 0.))
        dose = (.002 * profile.fog_bias * stress * rate * max(local.time_min, 0.) * 60.
                * max(end-start, 0.) * float(row.get('strength', 1.)))
        if step.strength <= 0.: dose = 0.
        response_factors = (solution or {}).get('formation_response',FORMATION_DEFAULTS)
        response = BathFormationResponse(
            dose * response_factors['chemical_fog_factor'],
            effect.grain_factor * response_factors['granularity_factor'],
            effect.grain_radius_factor * response_factors['grain_radius_factor'],
            effect.grain_clump_factor * response_factors['grain_clumping_factor'])
        steps.append(replace(step, formation_response=response))
    return replace(program, steps=tuple(steps))


def ensure_history(state):
    if state.formation_history is None:
        state.formation_history = {}
        for product, pool_name in POOLS.items():
            pool = getattr(state, pool_name)
            if pool is None: continue
            for name in NAMES:
                state.formation_history[f'{product}_{name}'] = (np.zeros_like(pool) if name == 'fog' else pool.copy())
        state.formation_created = {'silver_fog': 0., 'dye_fog': 0.}


def add_product(state, product, amount, fog, response):
    from film_foundry.core.execution_topology import reference_execution_enabled
    targets=[(state.formation_history[f'{product}_{name}'],getattr(response,name))
             for name in NAMES[:3] if f'{product}_{name}' in state.formation_history]
    blocked=(bool(targets) and not reference_execution_enabled() and amount.dtype==np.float32
             and amount.flags.c_contiguous and amount.size>=262144
             and all(value.dtype==np.float32 and value.flags.c_contiguous and value.shape==amount.shape
                     and not np.may_share_memory(value,amount) for value,_ in targets)
             and all(not np.may_share_memory(a,b) for i,(a,_) in enumerate(targets) for b,_ in targets[i+1:]))
    if blocked:
        # Walk the three moments together while the source block is hot in
        # CPU cache. Keep the original float32 multiply then add (no FMA).
        source=amount.reshape(-1);flat=[(value.reshape(-1),factor) for value,factor in targets]
        scratch=np.empty(131072,np.float32)
        for start in range(0,source.size,scratch.size):
            end=min(start+scratch.size,source.size);buffer=scratch[:end-start]
            for value,factor in flat:
                np.multiply(source[start:end],factor,out=buffer)
                np.add(value[start:end],buffer,out=value[start:end])
    else:
        for value,factor in targets:value+=amount*factor
    if fog is not None:
        key = f'{product}_fog'
        state.formation_history[key] += fog
        state.formation_created[key] += float(np.sum(fog, dtype=np.float64))


def prepare_fog_only_history(state, recipe):
    """Explicit fast formation: keep real chemical fog, omit grain moments.

    No change to material pools. The fast renderer uses the first developer's
    global grain response and cannot express local/product-weighted grain history.
    """
    if enabled(recipe) and state.formation_history is None:
        state.formation_history = {f'{p}_fog': np.zeros_like(getattr(state, pool))
                                   for p, pool in POOLS.items() if getattr(state, pool) is not None}
        state.formation_created = {'silver_fog': 0., 'dye_fog': 0.}


def first_developer_response(recipe, program):
    result = material_development(recipe)
    response = next((s.formation_response for s in program.steps if s.formation_response is not None), None)
    if response is not None:
        result = replace(result, **{name:getattr(response,name) for name in NAMES[:3]})
    return result


def retain_product(state, product, fraction):
    if state.formation_history is None: return
    for name in NAMES:
        value = state.formation_history.get(f'{product}_{name}')
        if value is not None: value *= fraction


def history_totals(state):
    if state.formation_history is None: return {}
    values = {f'formation_{key}': float(np.sum(value, dtype=np.float64)) for key, value in state.formation_history.items()}
    values.update({f'formation_created_{key}': value for key, value in state.formation_created.items()})
    return values


def response_from_totals(totals, product):
    mass = float(totals.get(POOLS[product], 0.))
    return {name: (float(totals.get(f'formation_{product}_{name}', 0.))/mass if mass > 1e-12 else (0. if name == 'grain_factor' else 1.)) for name in NAMES[:3]}


def retained_development(recipe, totals, product):
    return replace(material_development(recipe), **response_from_totals(totals, product))


def audit(totals,recipe=None):
    from film_foundry.contracts.formation_history import local_enabled
    local=recipe is not None and local_enabled(recipe)
    return {'model': recipe.formation_history_model if recipe is not None else MODEL,
            'grain_resolution': 'retained_mass_weighted_local_v2' if local else 'retained_mass_weighted_global_v1',
            'chemical_optics': 'material_coefficients_after_pool_conversion_v1',
            'silver': response_from_totals(totals, 'silver'), 'dye': response_from_totals(totals, 'dye'),
            'retained_fog': {p: float(totals.get(f'formation_{p}_fog', 0.)) for p in POOLS},
            'created_fog': {p: float(totals.get(f'formation_created_{p}_fog', 0.)) for p in POOLS}}
