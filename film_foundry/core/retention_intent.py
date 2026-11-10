"""Invert bleach activity for a reachable retained-metal quantity, without pixels."""
from copy import deepcopy
from dataclasses import replace
import numpy as np
from film_foundry.contracts.bath_solution import MODEL, validate_solution
from film_foundry.contracts.bath_overrides import validate_bath_overrides
from film_foundry.core.film_process.recipe import program_from_develop_recipe


def compile_retention(config, amount, program, rows, audit):
    from film_foundry.core.film_process.integration import _compatibility_for_program
    from film_foundry.core.media import is_monochrome_material
    mono = is_monochrome_material(config); layers = 1 if mono else 3
    context = dict(mode='bw_negative' if mono else config.mode, material_process=config.film.medium_process, layer_count=layers)
    profile = _compatibility_for_program(config.film,program.key,layers,program=program)
    last_development = max((i for i,row in enumerate(rows) if row['action'] in ('develop_color','develop_silver')), default=-1)
    baths = [i for i,row in enumerate(rows) if i>last_development and row['action']=='bleach_silver']
    if last_development<0 or not baths:
        audit['targets']['retention_unreachable']='No silver bleach follows the final development stage'
        return
    def remaining(candidate):
        metal=np.ones(layers, dtype=np.float64)
        balance=np.asarray(profile.layer_balance if profile.layer_balance is not None else np.ones(layers))
        for index,step in enumerate(candidate.steps):
            bath=step.bath_index if step.bath_index is not None else index
            if bath<=last_development: continue
            if step.action.value not in ('bleach_silver','remove_silver'): continue
            efficiency=profile.silver_bleach if step.action.value=='bleach_silver' else profile.silver_removal
            selectivity=np.asarray(step.layer_selectivity if step.layer_selectivity is not None else np.ones(layers))
            metal*=1-np.clip(step.strength*efficiency*balance*selectivity,0.,1.)
        return float(metal.mean())
    baseline=remaining(program)
    overrides={entry['bath']:deepcopy(entry) for entry in config.chemistry.bath_overrides or ()}
    templates={}
    for index in baths:
        row=rows[index]
        # Existing solution composition (including same-bath fixer) is retained.
        # A legacy ideal bleach gets a declared, bounded rate, never a direct
        # requested metal multiplier. Its actual dose is then solved below.
        solution=deepcopy(row.get('solution')) or validate_solution({'model':MODEL,'action':'bleach_silver','rate_per_minute':20.},'bleach_silver')
        entry=deepcopy(overrides.get(index+1,{}))
        entry.update(bath=index+1,action='bleach_silver',solution=solution)
        templates[index+1]=entry
    def candidate(activity):
        current=deepcopy(overrides)
        for bath,entry in templates.items():
            item=deepcopy(entry);item['solution']['activity_remaining']*=activity
            current[bath]=item
        changes=validate_bath_overrides(tuple(current[k] for k in sorted(current)))
        recipe=replace(config.chemistry,bath_overrides=changes)
        return program_from_develop_recipe(recipe,**context),changes
    full,_=candidate(1.);zero,_=candidate(0.)
    minimum=remaining(full);maximum=remaining(zero)
    if maximum<=baseline+1e-10:
        audit['targets']['retention_unreachable']='Later silver removal or inactive bleach leaves no reachable increase'
        return
    origin=max(baseline,minimum)
    target=origin+(maximum-origin)*amount
    low=0.;high=1.
    for _ in range(36):
        mid=(low+high)/2;trial,_=candidate(mid)
        if remaining(trial)>target:low=mid
        else:high=mid
    _,changes=candidate(0. if amount==1. else (low+high)/2)
    config.chemistry.bath_overrides=changes
    audit['generated_bath_overrides']=[deepcopy(entry) for entry in changes if entry['bath'] in templates]
    audit['targets'].update(retained_silver_fraction=target,retention_reference_fraction=baseline,retention_reachable_range=[origin,maximum])
    audit['targets']['retention_note']='Calibrated to metal present after final development; local access and layer-selective histories can alter the image-weighted fraction'
