"""Non-reactive annotations on already validated, immutable material states."""
from copy import copy
import json
from film_foundry.contracts.material_recipe import KEY as RECIPE_KEY,STATUS_KEY,validate_recipe,validate_recipe_status
from film_foundry.contracts.optical_cascade import CASCADE_KEY,validate_cascade


class ProcessAnnotations:
    def with_annotations(self,**annotations):
        # No stage, material, fields, temperature or other processing parameter
        # can enter this path. Those still go through the state constructor.
        if set(annotations)-{RECIPE_KEY,STATUS_KEY,CASCADE_KEY,'source_input'}:
            raise ValueError('Only recipe, source and optical provenance annotations may be replaced')
        if RECIPE_KEY in annotations and STATUS_KEY in annotations:raise ValueError('Recipe completeness cannot conflict')
        if RECIPE_KEY in annotations:validate_recipe(annotations[RECIPE_KEY])
        if STATUS_KEY in annotations:validate_recipe_status(annotations[STATUS_KEY])
        if CASCADE_KEY in annotations:validate_cascade(annotations[CASCADE_KEY])
        if 'source_input' in annotations and not isinstance(annotations['source_input'],dict):
            raise ValueError('Source annotation must be an object')
        process=json.loads(self.process_json)
        if RECIPE_KEY in annotations:process.pop(STATUS_KEY,None)
        if STATUS_KEY in annotations:process.pop(RECIPE_KEY,None)
        snapshot=json.dumps({**process,**annotations},allow_nan=False)
        result=copy(self)
        # Arrays and all formation values remain the exact validated objects.
        object.__setattr__(result,'process_json',snapshot)
        return result
