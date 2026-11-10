"""Explicit film-family clocks. No wall-clock inference or scanner mutation."""
import math
from copy import deepcopy

RESPONSE_MODEL='film_time_response_v1'
RESPONSE_MODEL_V2='film_time_response_v2'
MODELS={'storage':'film_storage_history_v1','latent':'film_latent_wait_v1','light':'film_residual_light_v1'}
DURATION_LIMITS={'storage':36500.,'latent':876000.,'light':876000.}
VECTORS={
    'storage_speed_loss_stops_per_year':(.5,.35,.25),
    'storage_fog_rate_per_year':(.025,.020,.015),
    'latent_fade_rate_per_day':(.006,.004,.003),
    'latent_fog_rate_per_day':(.0004,.00035,.0003),
    'printout_rate_per_light_hour':(.04,.035,.03),
}
SCALARS={'temperature_q10':(2.,1.,4.),'humidity_sensitivity':(1.,0.,2.),
         'bleached_printout_ratio':(.3,0.,2.),'printout_grain_factor':(.35,0.,4.),
         'printout_grain_radius_factor':(.7,.05,4.),'printout_grain_clump_factor':(.5,.05,4.)}


def number(value,low,high,name):
    if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{name} must be finite between {low} and {high}')
    return float(value)


def validate_response(value):
    if value is None:return None
    if not isinstance(value,dict) or value.get('model') not in (RESPONSE_MODEL,RESPONSE_MODEL_V2) or set(value)-{'model',*VECTORS,*SCALARS}:
        raise ValueError('Invalid film time response')
    result={'model':value['model']}
    for key,default in VECTORS.items():
        vector=value.get(key,default)
        if not isinstance(vector,(tuple,list)) or len(vector)!=3:raise ValueError(f'{key} requires three material-layer values')
        result[key]=[number(v,0.,10.,key) for v in vector]
    for key,(default,low,high) in SCALARS.items():result[key]=number(value.get(key,default),low,high,key)
    return result


def validate_history(value,kind):
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'model','segments'} or value['model']!=MODELS[kind]:
        raise ValueError(f'Invalid film {kind} history')
    rows=value['segments']
    if not isinstance(rows,(list,tuple)) or not 1<=len(rows)<=32:raise ValueError('Time history requires 1–32 segments')
    fields=({'duration_days':(0.,DURATION_LIMITS[kind]),'temperature_c':(-40.,60.),'relative_humidity':(0.,1.)} if kind=='storage' else
            {'duration_hours':(0.,DURATION_LIMITS[kind]),'temperature_c':(-40.,60.),'relative_humidity':(0.,1.)} if kind=='latent' else
            {'duration_hours':(0.,DURATION_LIMITS[kind]),'relative_illuminance':(0.,10000.)})
    result=[]
    for row in rows:
        if not isinstance(row,dict) or set(row)!=set(fields)|({'light_rgb'} if kind=='light' else set()):raise ValueError('Invalid time segment fields')
        clean={key:number(row[key],*bounds,key) for key,bounds in fields.items()}
        if kind=='light':
            rgb=row['light_rgb']
            if not isinstance(rgb,(tuple,list)) or len(rgb)!=3:raise ValueError('light_rgb requires three values')
            clean['light_rgb']=[number(v,0.,4.,'light_rgb') for v in rgb]
        result.append(clean)
    return {'model':MODELS[kind],'segments':result}


def validate_config(config):
    response=validate_response(config.film.time_response)
    histories=(validate_history(config.film.storage_history,'storage'),validate_history(config.chemistry.latent_wait,'latent'),validate_history(config.chemistry.post_process_light,'light'))
    if any(histories) and response is None:raise ValueError('Explicit film time histories require an enabled material time response')
    if any(histories) and config.chemistry.program_key=='legacy_density':raise ValueError('Film time histories require a material-pool program')


def record(config):
    if not any((config.film.storage_history,config.chemistry.latent_wait,config.chemistry.post_process_light)):return None
    return deepcopy({'model':'film_time_history_v1','response':config.film.time_response,
        'storage':config.film.storage_history,'latent':config.chemistry.latent_wait,
        'post_process_light':config.chemistry.post_process_light,'clock':'explicit_durations_only',
        'observation':'readonly_final_medium'})
