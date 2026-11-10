"""Portable operation recipe shape; no numeric runtime or frontend dependency."""
import json
import math

MODEL='material_recipe_v1'
KEY='material_recipe'
STATUS_KEY='material_recipe_status'
MAX_STEPS=512
MAX_BYTES=512*1024


def validate_recipe_status(value):
    if (not isinstance(value,dict) or set(value)!={'model','complete','reason','operation'} or
        value['model']!='material_recipe_status_v1' or value['complete'] is not False or
        value['reason'] not in ('missing_history','nonportable_parameters','recipe_limit','inconsistent_history') or
        not isinstance(value['operation'],str) or not value['operation']):
        raise ValueError('Invalid incomplete recipe status')
    return dict(value)


def _json_value(value,depth=0):
    if depth>8:raise ValueError('Recipe nesting is too deep')
    if value is None or type(value) in (bool,str):return value
    if type(value) in (int,float):
        if not math.isfinite(value):raise ValueError('Recipe numbers must be finite')
        return value
    if isinstance(value,(list,tuple)):return [_json_value(item,depth+1) for item in value]
    if isinstance(value,dict) and all(isinstance(k,str) for k in value):
        return {key:_json_value(item,depth+1) for key,item in value.items()}
    raise ValueError('Recipe parameters must be JSON values, not live arrays or objects')


def validate_recipe(value):
    if not isinstance(value,dict) or set(value)!={'model','family','material_version','material','seed','steps'}:
        raise ValueError('Invalid material recipe fields')
    clean=_json_value(value)
    if clean['model']!=MODEL or not isinstance(clean['family'],str) or not clean['family']:
        raise ValueError('Unknown material recipe model/family')
    if type(clean['material_version']) is not int or clean['material_version']<1:raise ValueError('Recipe needs a material version')
    if type(clean['seed']) is not int or not 0<=clean['seed']<=4294967295:raise ValueError('Invalid recipe seed')
    if not isinstance(clean['material'],dict):raise ValueError('Recipe material must be an object')
    steps=clean['steps']
    if not isinstance(steps,list) or not 1<=len(steps)<=MAX_STEPS:raise ValueError('Recipe must contain 1 to 512 ordered steps')
    for step in steps:
        if not isinstance(step,dict) or set(step)!={'operation','parameters'} or not isinstance(step['operation'],str) or not step['operation'] or not isinstance(step['parameters'],dict):
            raise ValueError('Invalid recipe operation')
    if len(json.dumps(clean,ensure_ascii=False,allow_nan=False).encode('utf-8'))>MAX_BYTES:raise ValueError('Recipe exceeds 512 KiB')
    return clean
