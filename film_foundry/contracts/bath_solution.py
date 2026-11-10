"""Portable explicit bath capability, independent of UI and recipe filenames."""
import math
from film_foundry.contracts.bath_domains import BATH_DOMAINS

MODEL='bath_solution_v1'
MODEL_V2='bath_solution_v2'
MODEL_V3='bath_solution_v3'
DEVELOPING_ACTIONS=('develop_silver','develop_color')
FORMATION_RANGES={'granularity_factor':(0.,4.),'grain_radius_factor':(.25,4.),
                  'grain_clumping_factor':(.25,4.),'chemical_fog_factor':(0.,4.)}
FORMATION_DEFAULTS={key:1. for key in FORMATION_RANGES}
COUPLED_ACTIONS=('bleach_silver','develop_silver')
ACTIONS=tuple(key for key in BATH_DOMAINS if key not in ('activate_remaining_halide','destroy_dye'))
RANGES={'rate_per_minute':(0.,20.),'q10':(.1,5.),'reference_temperature_c':(0.,60.),
        'activity_remaining':(0.,1.),'hardener_g_l':(0.,100.)}
DEFAULTS={'rate_per_minute':.5,'q10':2.,'reference_temperature_c':20.,'activity_remaining':1.,'hardener_g_l':0.}

def validate_solution(value,action):
    if value is None:return None
    if action not in ACTIONS:raise ValueError('This step does not accept a bath solution')
    if not isinstance(value,dict):raise ValueError('Invalid bath solution')
    allowed={'model','action',*RANGES}
    if value.get('model') in (MODEL_V2,MODEL_V3):allowed.add('secondary')
    if value.get('model')==MODEL_V3:allowed.add('formation_response')
    if set(value)-allowed:raise ValueError('Unknown bath solution fields')
    if value.get('model') not in (MODEL,MODEL_V2,MODEL_V3) or value.get('action')!=action:raise ValueError('Bath solution model/action mismatch')
    result={'model':MODEL,'action':action,**DEFAULTS,**value}
    for key,(low,high) in RANGES.items():
        number=result[key]
        if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or not low<=number<=high:
            raise ValueError(f'{key} must be finite between {low} and {high}')
        result[key]=float(number)
    if value['model']==MODEL_V2 or 'secondary' in value:
        secondary=value.get('secondary')
        if action not in COUPLED_ACTIONS or not isinstance(secondary,dict) or set(secondary)-{'action',*(key for key in RANGES if key!='hardener_g_l')} or secondary.get('action')!='fix_halide':
            raise ValueError('v2 supports silver development/fixing or silver bleach/fixing in one bath')
        checked=validate_solution({'model':MODEL,**secondary},'fix_halide')
        result['secondary']={key:val for key,val in checked.items() if key not in ('model','hardener_g_l')}
    if value['model']==MODEL_V3:
        response=value.get('formation_response')
        if action not in DEVELOPING_ACTIONS or not isinstance(response,dict) or set(response)-set(FORMATION_RANGES):
            raise ValueError('v3 requires a development formation_response')
        response={**FORMATION_DEFAULTS,**response}
        for key,(low,high) in FORMATION_RANGES.items():
            number=response[key]
            if isinstance(number,bool) or not isinstance(number,(int,float)) or not math.isfinite(number) or not low<=number<=high:
                raise ValueError(f'{key} must be finite between {low} and {high}')
            response[key]=float(number)
        result['formation_response']=response
    return result

def reaction_capability(solution,action):
    """Resolve one declared component as a v1 scalar rate definition."""
    clean=validate_solution(solution,solution['action'])
    if action==clean['action']:
        return {key:value for key,value in clean.items() if key in ('action',*RANGES)} | {'model':MODEL}
    if clean.get('secondary',{}).get('action')==action:
        return validate_solution({'model':MODEL,**clean['secondary']},action)
    raise ValueError('Reaction is not declared by this solution')
