"""Explicit fast formation policy: normal baths, simple stain and real fog.

Only a private formation recipe is projected. Requested settings remain in the
result recipe for replay; the omission record explains the executed subset.
This policy never runs in a scanner or changes an already exposed latent.
"""
from copy import deepcopy
from dataclasses import replace

from film_foundry.contracts.effect_intent import validate_effect_intent


OMITTED_FIELDS = ('uneven_development', 'silver_plating', 'silver_retention',
                  'dye_damage_strength', 'reticulation_strength')


def basic_formation_config(config):
    recipe = config.chemistry
    intent = validate_effect_intent(recipe.effect_intent)
    stain = float(recipe.chemical_stain)
    if intent is not None:
        stain = float(intent['amounts']['chemical_stain']) if intent['enabled'] else 0.
    omitted_events = [event['kind'] for event in (recipe.process_events or {}).get('events', ())]
    secondary_baths = []
    def rows(values):
        if values is None:
            return None
        result = deepcopy(values)
        for i, row in enumerate(result):
            for key in OMITTED_FIELDS:
                if key in row:
                    row[key] = 0.
            solution = row.get('solution')
            if solution and solution.get('secondary') is not None:
                secondary_baths.append(row.get('bath', i+1))
                solution.pop('secondary')
                if solution['model'] == 'bath_solution_v2':
                    solution['model'] = 'bath_solution_v1'
            # Local hardening and wet surface history are outside this profile.
            if solution and 'hardener_g_l' in solution:
                solution['hardener_g_l'] = 0.
        return result
    chemistry = replace(recipe, **{key:0. for key in OMITTED_FIELDS},
        chemical_stain=stain, process_events=None, effect_intent=None,
        process_steps=rows(recipe.process_steps), bath_overrides=rows(recipe.bath_overrides))
    audit = {'model':'basic_formation_policy_v1', 'omitted_event_kinds':sorted(set(omitted_events)),
             'omitted_controls':list(OMITTED_FIELDS), 'omitted_secondary_baths':secondary_baths,
             'stain_model':'reduced_density_stain_v1', 'chemical_stain':stain,
             'surface_history_retained':False, 'scanner_changed':False}
    return replace(config, chemistry=chemistry), audit
