"""Canonical declarations for persisted film execution dependencies."""
from copy import deepcopy
import json
from film_foundry.contracts.bath_solution import validate_solution


def canonical_wet_definition(value):
    if not isinstance(value,dict):return value
    result=deepcopy(value)
    # Explicit and omitted solution defaults have the same operator semantics.
    # Old checkpoint definitions may precede the config loader's expansion.
    for row in result.get('baths',()):
        if row.get('solution') is not None:
            row['solution']=validate_solution(row['solution'],row['action'])
    return json.loads(json.dumps(result,allow_nan=False))
