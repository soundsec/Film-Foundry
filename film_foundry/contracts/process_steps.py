"""Ordered creative process controls shared with independent phone implementations."""
from __future__ import annotations

import math
from collections.abc import Mapping

ACTIONS = ("develop_silver", "develop_color", "activate_remaining_halide", "bleach_silver",
           "fix_halide", "remove_silver", "destroy_dye", "bleach_mask_dye", "remove_auxiliary")
CONDITION_RANGES = {"time_min": (.1, 120.), "temperature_c": (0., 60.), "concentration": (.05, 4.),'bath_transfer_s':(0.,3600.),
                    "agitation": (0., 3.5), "agitation_time_s": (0., 7200.), "push_stops": (-6., 6.),
                    "developer_exhaustion": (0., 1.), "fixer_exhaustion": (0., 1.), "compensation": (0., 1.),
                    "silver_retention": (0., 1.), "dye_damage_strength": (0., 1.)}
CHEMISTRY_FIELDS = ("developer_type", "fixer_type")


def validate_process_conditions(values):
    for key in CHEMISTRY_FIELDS:
        value = values.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value)):
            raise ValueError(f"invalid {key}")
    for key, (low, high) in CONDITION_RANGES.items():
        value = values.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int))
                                  or not math.isfinite(value) or not low <= value <= high):
            raise ValueError(f"{key} must be between {low} and {high}")
    seconds, minutes = values.get("agitation_time_s"), values.get("time_min")
    if seconds is not None and minutes is not None and seconds > minutes * 60:
        raise ValueError("agitation time exceeds bath duration")


def validate_process_steps(raw):
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 32:
        raise ValueError("process_steps must contain 1–32 steps")
    result = []
    for row in raw:
        if not isinstance(row, Mapping) or set(row) - {*CONDITION_RANGES, *CHEMISTRY_FIELDS, "action", "label", "strength", "dye_destruction_basis", "solution"}:
            raise ValueError("invalid process step fields")
        if row.get("action") not in ACTIONS:
            raise ValueError("unknown process action")
        if 'dye_destruction_basis' in row and (row['action']!='destroy_dye' or row['dye_destruction_basis'] not in ('explicit','compatibility')):
            raise ValueError('invalid dye destruction basis')
        strength = row.get("strength", 1.)
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
            raise ValueError("step strength must be between 0 and 1")
        label = row.get("label", "")
        if not isinstance(label, str) or len(label) > 80 or any(ord(ch) < 32 for ch in label):
            raise ValueError("invalid process label")
        validate_process_conditions(row)
        clean=dict(row)
        if 'solution' in clean:
            from film_foundry.contracts.bath_solution import validate_solution
            clean['solution']=validate_solution(clean['solution'],clean['action'])
        result.append(clean)
    return tuple(result)
