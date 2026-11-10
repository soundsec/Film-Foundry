"""Versioned, data-only recipes over the existing material session operations."""
from film_foundry.core.material_structure import from_record
from dataclasses import asdict
from pathlib import Path
from film_foundry.contracts.material_recipe import validate_recipe,MAX_BYTES
from film_foundry.core.atomic_io import strict_json_load,atomic_write_json
from film_foundry.app.material_operations import resolve_parameters


def bind_recipe(value):
    from film_foundry.app.material_lab_session import FAMILIES
    recipe=validate_recipe(value)
    match=next(((kind,module,definition) for kind,(module,definition) in FAMILIES.items() if module.FAMILY==recipe['family']),None)
    if match is None:raise ValueError('Unsupported recipe material family')
    kind,module,definition=match
    replay_versions=(1,2) if kind=='paper' else (2,3,4,5) if kind=='peel' else (2,3,4) if kind in ('integral','wet','plate') else (2,3)
    if recipe['material_version'] not in replay_versions:raise ValueError('Recipe material version cannot be recreated by this runtime')
    try:recipe['material']=asdict(from_record(definition,recipe['material']))
    except TypeError as exc:raise ValueError(f'Invalid recipe material: {exc}') from exc
    for step in recipe['steps']:
        step['parameters']=resolve_parameters(module,step['operation'],step['parameters'])
    return kind,validate_recipe(recipe)


def load_recipe(path):
    path=Path(path)
    if path.stat().st_size>MAX_BYTES:raise ValueError('Recipe exceeds 512 KiB')
    return bind_recipe(strict_json_load(path))[1]


def save_recipe(path,value):
    if not str(path).lower().endswith('.json'):raise ValueError('Recipe output requires a JSON path')
    recipe=bind_recipe(value)[1]
    atomic_write_json(path,recipe)


def run_recipe_file(input_path,recipe_path,output_path,*,stop='medium',long_edge=0,bit_depth=8,body_role=None,control=None):
    """Reference frontend: explicit image input, independent formation and output."""
    from film_foundry.app.material_lab_session import MaterialLabSession
    from film_foundry.core.atomic_io import atomic_path_set
    from film_foundry.core.material_family import ViewingConditions
    recipe=load_recipe(recipe_path);kind,_=bind_recipe(recipe)
    source=Path(input_path).resolve();target=Path(output_path).resolve()
    if stop not in ('state','medium','image'):raise ValueError('Unknown recipe endpoint')
    if target in (source,Path(recipe_path).resolve()):raise ValueError('Recipe output must not overwrite source or recipe')
    if stop=='image' and target.suffix.lower() not in ('.png','.tif','.tiff'):raise ValueError('Image endpoint requires PNG/TIFF')
    if stop!='image' and target.suffix.lower()!='.npz':raise ValueError('Material endpoint requires NPZ')
    if type(bit_depth) is not int or bit_depth not in (8,16):raise ValueError('Observation depth must be 8 or 16')
    medium=target.with_name(target.stem+'.formed.ffmaterial.npz') if stop=='image' else None
    outputs=(medium,target,target.with_suffix(target.suffix+'.json')) if stop=='image' else (target,)
    if any(path in (source,Path(recipe_path).resolve()) for path in outputs):
        raise ValueError('Recipe output set must not overwrite source or recipe')
    if control:control.report('material-replay','loading')
    session=MaterialLabSession(kind);session.open_image(source,long_edge);session.replay_recipe(recipe,control=control)
    if stop!='state' and session.group is None:raise ValueError('Recipe stops before a final medium; save state or complete the process')
    if stop=='image':
        bodies=session.group.bodies
        if body_role is None and len(bodies)!=1:raise ValueError('Choose a result body role for a multi-body recipe')
        indices=[i for i,body in enumerate(bodies) if body_role is None or body.role==body_role]
        if len(indices)!=1:raise ValueError('Result body role must identify one medium')
    if control:control.report('material-replay','saving')
    with atomic_path_set(outputs):
        if stop=='state':session.save(target)
        elif stop=='medium':session.save_products(target)
        else:
            session.save_products(medium);session.export_image(target,ViewingConditions(),indices[0],bit_depth=bit_depth)
        if control:control.report('material-replay','completed',1.)
    return {'output_path':str(target),'medium_path':str(medium if stop=='image' else target) if stop!='state' else None}
