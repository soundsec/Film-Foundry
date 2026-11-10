"""One recipe recorder for sessions, routes and optical family exposures.

Only metadata changes here. Formation, defaults and random fields remain owned
by the original family implementation. Missing history is never reconstructed.
"""
from film_foundry.core.material_structure import from_record
from dataclasses import asdict
import json

from film_foundry.app.material_operations import resolve_parameters
from film_foundry.contracts.material_recipe import MODEL,KEY,STATUS_KEY,validate_recipe


def _gap(state,operation,reason,previous=None):
    status=(previous or {}).get(STATUS_KEY) or {'model':'material_recipe_status_v1',
        'complete':False,'reason':reason,'operation':operation}
    return state.with_annotations(**{STATUS_KEY:status})


def _identity(state,module):
    return {'model':MODEL,'family':module.FAMILY,'material_version':state.schema_version,
            'material':asdict(state.material),'seed':state.seed}


def record_transition(module,before,after,operation,parameters,*,strict=True):
    """Append actual defaults. Non-portable legacy route inputs retain execution.

    Route callers may historically pass spatial arrays. Such steps invalidate
    a complete JSON recipe rather than silently retaining a stale prefix.
    """
    previous=json.loads(before.process_json) if before is not None else {}
    if before is not None and KEY not in previous:return _gap(after,operation,'missing_history',previous)
    identity=_identity(after,module)
    if before is None:recipe={**identity,'steps':[]}
    else:
        try:
            recipe=validate_recipe(previous[KEY])
            # Defaults added for a newer model are inert in legacy versions.
            # Normalize their material record without changing its version.
            recipe['material']=asdict(from_record(type(after.material),recipe['material']))
            if any(recipe[key]!=value for key,value in identity.items()):raise ValueError('Recipe does not match the frozen material')
        except ValueError:
            if strict:raise
            return _gap(after,operation,'inconsistent_history')
    try:
        resolved=resolve_parameters(module,operation,parameters)
        resolved=json.loads(json.dumps(resolved,allow_nan=False))
    except (TypeError,ValueError):
        if strict:raise
        return _gap(after,operation,'nonportable_parameters')
    recipe={**recipe,'steps':[*recipe['steps'],{'operation':operation,'parameters':resolved}]}
    try:return after.with_annotations(**{KEY:recipe})
    except ValueError:
        if strict:raise
        return _gap(after,operation,'recipe_limit')


def record_initial_exposure(module,state,*,strict=False):
    """Record the explicit defaults hidden by the family's convenience expose."""
    recipe={**_identity(state,module),'steps':[{'operation':op,'parameters':resolve_parameters(module,op,{})}
        for op in getattr(module,'EXPOSURE_STEPS',('expose',))]}
    try:return state.with_annotations(**{KEY:recipe})
    except ValueError:
        if strict:raise
        return _gap(state,'expose','recipe_limit')
