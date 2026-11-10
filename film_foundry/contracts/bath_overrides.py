"""Explicit solution conditions on an existing source bath, without reordering it."""
from copy import deepcopy
from film_foundry.contracts.process_steps import validate_process_conditions
from film_foundry.contracts.bath_solution import ACTIONS, validate_solution

CONDITIONS = ('time_min', 'temperature_c', 'concentration', 'agitation',
              'agitation_time_s', 'bath_transfer_s')


def validate_bath_overrides(value):
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 32:
        raise ValueError('bath_overrides must contain 1–32 bath definitions')
    result = []; seen = set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {'bath', 'action', 'solution', *CONDITIONS}:
            raise ValueError('Invalid bath override fields')
        bath = entry.get('bath')
        if isinstance(bath, bool) or not isinstance(bath, int) or not 1 <= bath <= 32 or bath in seen:
            raise ValueError('Bath overrides require unique source bath numbers from 1 to 32')
        if entry.get('action') not in ACTIONS or entry.get('solution') is None:
            raise ValueError('Bath override requires an explicit solution capability')
        validate_process_conditions(entry)
        clean = deepcopy(entry)
        clean['solution'] = validate_solution(entry['solution'], entry['action'])
        result.append(clean); seen.add(bath)
    return tuple(result)


def merge_bath_overrides(rows, overrides):
    rows = [deepcopy(row) for row in rows]
    for entry in validate_bath_overrides(overrides) or ():
        index = entry['bath'] - 1
        if index >= len(rows) or rows[index]['action'] != entry['action']:
            raise ValueError('Bath override source number/action does not match the process')
        rows[index].update({key: deepcopy(value) for key, value in entry.items() if key != 'bath'})
    return rows
