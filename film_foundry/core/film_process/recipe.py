"""Map darkroom-facing recipe controls to reduced film process programs."""

from __future__ import annotations

from dataclasses import replace, asdict

import numpy as np

from film_foundry.core.development import build_effective_development
from film_foundry.core.film_process.operators import (
    FilmProcessAction,
    FilmProcessProgram,
    FilmProcessStep,
)
from film_foundry.core.film_process.programs import (
    black_and_white_negative_program,
    black_and_white_reversal_program,
    color_negative_program,
    color_reversal_program,
)
from film_foundry.model.config import DevelopRecipeConfig


FILM_PROCESS_PROGRAM_KEYS = (
    "auto",
    "legacy_density",
    "bw_negative",
    "color_negative",
    "color_negative_bleach_bypass",
    "bw_reversal",
    "color_reversal",
)


def _normalized(value: str | None) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def resolve_program_key(
    recipe: DevelopRecipeConfig,
    *,
    mode: str = "color_negative",
    material_process: str = "negative",
    coupled_intervals: int = 8,
) -> str:
    """Resolve ``auto`` without treating the program name as the final state."""
    if getattr(recipe, "process_steps", None) is not None:
        from film_foundry.contracts.process_steps import validate_process_steps
        rows = validate_process_steps(recipe.process_steps)
        color = any(row["action"] == "develop_color" for row in rows)
        positive = program_from_develop_recipe(recipe,coupled_intervals=coupled_intervals).output_polarity == "positive"
        return ("color_" if color else "bw_") + ("reversal" if positive else "negative")
    configured = _normalized(getattr(recipe, "program_key", "auto")) or "auto"
    if configured != "auto":
        if configured not in FILM_PROCESS_PROGRAM_KEYS:
            raise ValueError(f"Unsupported silver-halide film process program: {configured}")
        return configured
    normalized_mode = _normalized(mode)
    normalized_material_process = _normalized(material_process)
    monochrome = normalized_mode in {"bw", "bw_negative", "bw_positive", "monochrome"}
    reversal = normalized_material_process in {"slide", "reversal", "positive"} or "reversal" in normalized_mode
    if reversal:
        return "bw_reversal" if monochrome else "color_reversal"
    return "bw_negative" if monochrome else "color_negative"


def _base_program(key: str, recipe: DevelopRecipeConfig) -> FilmProcessProgram:
    if key == "bw_negative":
        return black_and_white_negative_program()
    if key == "color_negative":
        return color_negative_program()
    if key == "color_negative_bleach_bypass":
        return color_negative_program(bleach_strength=float(recipe.silver_bleach_completion))
    if key == "bw_reversal":
        return black_and_white_reversal_program()
    if key == "color_reversal":
        return color_reversal_program()
    raise ValueError(f"Unsupported silver-halide film process program: {key}")


def program_from_develop_recipe(
    recipe: DevelopRecipeConfig, *, mode: str = 'color_negative',
    material_process: str = 'negative', layer_count: int = 3,
    coupled_intervals: int = 8,
) -> FilmProcessProgram:
    from film_foundry.core.film_process.time_history import attach_time_operations
    return attach_time_operations(_program_with_events(recipe,mode=mode,material_process=material_process,layer_count=layer_count,coupled_intervals=coupled_intervals),recipe)


def _program_with_events(recipe, *, mode, material_process, layer_count, coupled_intervals):
    program=_program_with_bath_overrides(recipe,mode=mode,material_process=material_process,layer_count=layer_count,coupled_intervals=coupled_intervals)
    from film_foundry.core.film_process.formation_history import attach_responses
    program=attach_responses(program,recipe)
    raw=recipe.process_events
    if raw is not None and raw['model'] in ('film_process_events_v10','film_process_events_v11','film_process_events_v12','film_process_events_v13','film_process_events_v14','film_process_events_v15'):
        from film_foundry.contracts.process_events import validate_process_events
        from film_foundry.core.film_process.ordered_events import inject_events
        return inject_events(program,validate_process_events(raw)['events'])
    if raw is None or not any(row.get('kind')=='dye_bath' for row in raw['events']):return program
    from film_foundry.contracts.process_events import validate_process_events
    events=validate_process_events(raw)['events']
    if not program.steps:raise ValueError('Dye baths require material-pool processing')
    last=len(recipe.process_steps)-1 if recipe.process_steps is not None else max((s.bath_index if s.bath_index is not None else i) for i,s in enumerate(program.steps))
    from film_foundry.contracts.process_events import EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9
    if raw['model'] in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9):
        additions={}
        for i,event in enumerate(events):
            if event['kind']!='dye_bath':continue
            source=event.get('after_bath',0)-1 if event.get('after_bath',0) else last
            if source>last:raise ValueError('Dye operation refers to a nonexistent bath')
            if event['concentration_mmol_l']==0 or event['duration_s']==0:continue
            additions.setdefault(source,[]).append(FilmProcessStep(FilmProcessAction.DESTROY_DYE,
                label=f'chemical_dye_attack_{i+1}',dye_destruction_basis='chemical',bath_index=source,bath_entry=False,
                chemical_contact=(event['concentration_mmol_l'],event['duration_s'],event['temperature_c']),
                chemical_placement=(event['center_x'],event['center_y'],event['radius_mm']) if event['radius_mm']>0 else None))
        steps=[]
        for i,step in enumerate(program.steps):
            source=step.bath_index if step.bath_index is not None else i
            steps.append(replace(step,bath_index=source))
            following=program.steps[i+1] if i+1<len(program.steps) else None
            next_source=(following.bath_index if following.bath_index is not None else i+1) if following else None
            if next_source!=source:steps.extend(additions.pop(source,()))
        return replace(program,steps=tuple(steps))
    extra=tuple(FilmProcessStep(FilmProcessAction.DESTROY_DYE,label=f'chemical_dye_attack_{i+1}',
        dye_destruction_basis='chemical',bath_index=last,bath_entry=False,
        chemical_contact=(e['concentration_mmol_l'],e['duration_s'],e['temperature_c']))
        for i,e in enumerate(events) if e['kind']=='dye_bath' and e['concentration_mmol_l']>0 and e['duration_s']>0)
    return replace(program,steps=(*program.steps,*extra))


def _program_with_bath_overrides(recipe, **context):
    """Keep native stage semantics; only explicitly selected baths change kinetics."""
    from film_foundry.contracts.bath_overrides import merge_bath_overrides, CONDITIONS
    from film_foundry.core.bath_kinetics import solution_completion, coupled_reaction_plan
    if not recipe.bath_overrides:
        return _base_program_from_develop_recipe(recipe, **context)
    if recipe.process_steps is not None:
        rows = merge_bath_overrides(recipe.process_steps, recipe.bath_overrides)
        return _base_program_from_develop_recipe(replace(recipe, process_steps=tuple(rows), bath_overrides=None), **context)
    program = _base_program_from_develop_recipe(recipe, **context)
    rows = merge_bath_overrides([{'action':s.action.value} for s in program.steps], recipe.bath_overrides)
    adjusted = []
    for index, (step, row) in enumerate(zip(program.steps, rows)):
        step = replace(step, bath_index=index)
        if 'solution' not in row:
            adjusted.append(step); continue
        local = replace(recipe, **{key:row[key] for key in CONDITIONS if key in row})
        from film_foundry.contracts.process_steps import validate_process_conditions
        validate_process_conditions({key:getattr(local,key) for key in CONDITIONS})
        if step.action in (FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR):
            effective = build_effective_development(local)
            selective = step.label in ('exposure_selective_development','color_coupling_development','first_development')
            step = replace(step, developability_gamma=effective.gamma_factor if selective else 1.,
                           highlight_compensation=effective.highlight_compensation if selective else 0.)
        solution = row['solution']
        plan = coupled_reaction_plan(solution, local, intervals=context.get('coupled_intervals',8)) if solution.get('secondary') else [(row['action'],0.,1.,solution_completion(solution,local))]
        if not plan: plan = [(row['action'],0.,1.,0.)]
        for subindex,(action,start,end,completion) in enumerate(plan):
            developing = action in ('develop_silver','develop_color')
            adjusted.append(replace(step, action=FilmProcessAction(action), strength=completion,
                bath_entry=subindex==0, reaction_window=(start,end) if solution.get('secondary') else None,
                developability_gamma=step.developability_gamma if developing else 1.,
                highlight_compensation=step.highlight_compensation if developing else 0.))
    return replace(program, steps=tuple(adjusted))


def _base_program_from_develop_recipe(
    recipe: DevelopRecipeConfig,
    *,
    mode: str = "color_negative",
    material_process: str = "negative",
    layer_count: int = 3,
    coupled_intervals: int = 8,
) -> FilmProcessProgram:
    """Build an executable reduced program from existing recipe controls."""
    if getattr(recipe, "process_steps", None) is not None:
        from film_foundry.contracts.process_steps import validate_process_steps, CONDITION_RANGES, CHEMISTRY_FIELDS, validate_process_conditions
        rows = validate_process_steps(recipe.process_steps)
        adjusted = []
        for bath_index,row in enumerate(rows):
            controls = {key: row.get(key, getattr(recipe, key)) for key in (*CONDITION_RANGES, *CHEMISTRY_FIELDS)}
            validate_process_conditions(controls)
            local = replace(recipe, **controls)
            effective = build_effective_development(local)
            strength = float(row.get("strength", 1.0)) * float(np.clip(effective.progress_ratio, 0., 1.))
            action = FilmProcessAction(row["action"])
            if row.get('solution') is not None:
                from film_foundry.core.bath_kinetics import solution_completion
                strength=float(row.get('strength',1.))*solution_completion(row['solution'],local)
            elif action == FilmProcessAction.FIX_HALIDE:
                strength *= 1.0 - effective.clearing_failure
            elif action == FilmProcessAction.BLEACH_SILVER:
                strength *= 1.0 - float(local.silver_retention)
            step=FilmProcessStep(action, strength=strength, label=str(row.get("label", "")),bath_index=bath_index,
                developability_gamma=float(effective.gamma_factor) if action in {FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR} else 1.0,
                highlight_compensation=float(effective.highlight_compensation) if action in {FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR} else 0.0,
                dye_destruction_basis=str(row.get('dye_destruction_basis','explicit')) if action == FilmProcessAction.DESTROY_DYE else "compatibility")
            if row.get('solution',{} ) and row['solution'].get('secondary'):
                from film_foundry.core.bath_kinetics import coupled_reaction_plan
                plan=coupled_reaction_plan(row['solution'],local,intervals=coupled_intervals)
                if not plan:plan=[(row['action'],0.,1.,0.)]
                for subindex,(reaction,start,end,completion) in enumerate(plan):
                    developing=reaction in ('develop_silver','develop_color')
                    adjusted.append(replace(step,action=FilmProcessAction(reaction),strength=float(row.get('strength',1.))*completion,
                        label=f'{step.label or "bath_"+str(bath_index+1)}:{reaction}',
                        bath_entry=subindex==0,reaction_window=(start,end),
                        developability_gamma=step.developability_gamma if developing else 1.,
                        highlight_compensation=step.highlight_compensation if developing else 0.))
            else:adjusted.append(step)
            if float(row.get("dye_damage_strength", 0.0)) > 0:
                adjusted.append(FilmProcessStep(FilmProcessAction.DESTROY_DYE, strength=float(row["dye_damage_strength"]), label="bath_dye_damage", dye_destruction_basis="explicit",bath_index=bath_index,bath_entry=False))
        if float(recipe.dye_damage_strength) > 0:
            adjusted.append(FilmProcessStep(FilmProcessAction.DESTROY_DYE, strength=float(recipe.dye_damage_strength), label="explicit_process_dye_damage", dye_destruction_basis="explicit",bath_index=len(rows)-1,bath_entry=False))
        # The polarity probe uses a compatible reference material. Compatibility
        # loss must not be treated as an explicit command to erase all dye.
        positive = _custom_positive_reference([{"action": step.action.value, "strength": step.strength} for step in adjusted
            if not (step.action == FilmProcessAction.DESTROY_DYE and step.dye_destruction_basis == 'compatibility')])
        return FilmProcessProgram(key="custom", steps=tuple(adjusted), output_polarity="positive" if positive else "negative",
            compatible_interpreters=("positive_transparency_scan" if positive else "negative_scan",))
    key = resolve_program_key(recipe, mode=mode, material_process=material_process)
    if key == "custom":
        raise ValueError("custom program requires process_steps")
    if key == "legacy_density":
        normalized_process = _normalized(material_process)
        positive = normalized_process in {"slide", "reversal", "positive"}
        return FilmProcessProgram(
            key="legacy_density",
            steps=(),
            output_polarity="positive" if positive else "negative",
            compatible_interpreters=(
                ("positive_transparency_scan",) if positive else ("negative_scan",)
            ),
        )
    program = _base_program(key, recipe)
    mask_bleach_completion = float(np.clip(recipe.mask_bleach_completion, 0.0, 1.0))
    if key == "color_reversal" and mask_bleach_completion > 1e-6:
        steps: list[FilmProcessStep] = []
        inserted = False
        for existing_step in program.steps:
            if existing_step.action == FilmProcessAction.DESTROY_DYE and not inserted:
                steps.append(
                    FilmProcessStep(
                        FilmProcessAction.BLEACH_MASK_DYE,
                        strength=mask_bleach_completion,
                        label="experimental_mask_dye_bleach",
                    )
                )
                inserted = True
            steps.append(existing_step)
        program = replace(program, steps=tuple(steps))
    effective = build_effective_development(recipe)
    development_completion = float(np.clip(effective.progress_ratio, 0.0, 1.0))
    first_development_completion = development_completion * float(
        np.clip(recipe.first_development_completion, 0.0, 1.0)
    )
    second_development_completion = development_completion * float(
        np.clip(recipe.second_development_completion, 0.0, 1.0)
    )
    fixing_completion = float(
        np.clip(
            float(recipe.halide_fixing_completion) * (1.0 - effective.clearing_failure),
            0.0,
            1.0,
        )
    )
    bleach_completion = float(
        np.clip(
            float(recipe.silver_bleach_completion) * (1.0 - float(recipe.silver_retention)),
            0.0,
            1.0,
        )
    )
    configured_balance = tuple(float(np.clip(v, 0.0, 2.0)) for v in recipe.process_layer_balance)
    if int(layer_count) == 1:
        layer_balance = (float(np.mean(configured_balance)),)
    elif int(layer_count) == len(configured_balance):
        layer_balance = configured_balance
    else:
        layer_balance = tuple([1.0] * int(layer_count))
    dye_ratio = tuple(
        [float(np.clip(float(recipe.dye_coupling_efficiency), 0.0, 2.0))] * int(layer_count)
    )
    explicit_dye_damage = float(
        np.clip(getattr(recipe, "dye_damage_strength", 0.0), 0.0, 1.0)
    )
    adjusted: list[FilmProcessStep] = []
    for step in program.steps:
        if (
            step.action == FilmProcessAction.REMOVE_AUXILIARY
            and key in {"color_negative", "color_negative_bleach_bypass", "color_reversal"}
            and explicit_dye_damage > 1e-6
        ):
            # Compatibility loss runs first when present; explicit damage then
            # removes a bounded fraction of the remaining dye. Sequential
            # application gives 1-(1-a)(1-b) instead of unbounded addition.
            adjusted.append(
                FilmProcessStep(
                    FilmProcessAction.DESTROY_DYE,
                    strength=explicit_dye_damage,
                    label="explicit_process_dye_damage",
                    layer_selectivity=layer_balance,
                    dye_destruction_basis="explicit",
                )
            )
        if step.action in {FilmProcessAction.DEVELOP_SILVER, FilmProcessAction.DEVELOP_COLOR}:
            stage_completion = development_completion
            if step.label == "first_development":
                stage_completion = first_development_completion
            elif step.label in {"second_development", "color_development"}:
                stage_completion = second_development_completion
            exposure_selective_stage = step.label in {
                "exposure_selective_development",
                "color_coupling_development",
                "first_development",
            }
            step = replace(
                step,
                strength=stage_completion,
                layer_selectivity=layer_balance,
                dye_coupling_ratio=dye_ratio if step.action == FilmProcessAction.DEVELOP_COLOR else None,
                developability_gamma=(
                    float(effective.gamma_factor) if exposure_selective_stage else 1.0
                ),
                highlight_compensation=(
                    float(effective.highlight_compensation)
                    if exposure_selective_stage
                    else 0.0
                ),
            )
        elif step.action == FilmProcessAction.ACTIVATE_REMAINING_HALIDE:
            step = replace(step, strength=float(np.clip(recipe.reversal_activation, 0.0, 1.0)))
        elif step.action == FilmProcessAction.REMOVE_SILVER:
            step = replace(step, strength=float(np.clip(recipe.first_silver_removal, 0.0, 1.0)))
        elif step.action == FilmProcessAction.BLEACH_SILVER:
            step = replace(step, strength=bleach_completion)
        elif step.action == FilmProcessAction.BLEACH_MASK_DYE:
            step = replace(
                step,
                strength=mask_bleach_completion,
            )
        elif step.action == FilmProcessAction.FIX_HALIDE:
            step = replace(step, strength=fixing_completion)
        elif step.action == FilmProcessAction.REMOVE_AUXILIARY:
            step = replace(step, strength=float(np.clip(recipe.auxiliary_removal, 0.0, 1.0)))
        adjusted.append(step)
    resolved_key = key
    if key == "color_negative" and bleach_completion < 1.0 - 1e-6:
        resolved_key = "color_negative_bleach_bypass"
    return replace(program, key=resolved_key, steps=tuple(adjusted))


def process_program_payload(program: FilmProcessProgram) -> dict[str, object]:
    return {
        "key": program.key,
        "output_polarity": program.output_polarity,
        "view_mode": program.view_mode,
        "compatible_interpreters": list(program.compatible_interpreters),
        "steps": [
            {
                "action": step.action.value,
                "label": step.label,
                "strength": float(step.strength),
                "bath_index": step.bath_index,
                "bath_entry": step.bath_entry,
                "reaction_window": step.reaction_window,
                "chemical_contact": step.chemical_contact,
                "chemical_placement": step.chemical_placement,
                **({"process_event_index":step.process_event_index} if step.process_event_index is not None else {}),
                **({'formation_response':asdict(step.formation_response)} if step.formation_response is not None else {}),
                **({'time_history':step.time_history} if step.time_history is not None else {}),
                "layer_selectivity": (
                    None if step.layer_selectivity is None else list(step.layer_selectivity)
                ),
                "dye_coupling_ratio": (
                    None if step.dye_coupling_ratio is None else list(step.dye_coupling_ratio)
                ),
                "dye_destruction_basis": (
                    str(step.dye_destruction_basis)
                    if step.action == FilmProcessAction.DESTROY_DYE
                    else None
                ),
            }
            for step in program.steps
        ],
    }

def _custom_positive_reference(steps):
    # A small reference material determines interpretation; zero-strength or
    # wrongly ordered reversal steps must not force a positive label.
    color = any(step["action"] == "develop_color" for step in steps)
    def density(activation):
        halide, silver, coupler, dye, bleached = 1., 0., 1., 0., 0.
        for step in steps:
            action, f = step["action"], float(step.get("strength", 1.))
            if action in {"develop_silver", "develop_color"}:
                n = halide * activation * f
                halide -= n; silver += n
                if action == "develop_color":
                    c = min(coupler, n); coupler -= c; dye += c
            elif action == "activate_remaining_halide": activation += (1 - activation) * f
            elif action == "bleach_silver":
                n = silver * f; silver -= n; bleached += n
            elif action == "fix_halide": halide *= 1 - f; bleached *= 1 - f
            elif action == "remove_silver": silver *= 1 - f
            elif action == "destroy_dye": dye *= 1 - f
        return dye * color + silver * .85 + (halide + bleached) * .12
    return density(.01) > density(.99) + 1e-8
