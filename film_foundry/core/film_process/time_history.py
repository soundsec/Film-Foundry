"""Closed-form, stage-specific film ageing. Rates are creative proxies."""
from dataclasses import replace
import numpy as np
from film_foundry.contracts.film_time import validate_response,validate_history,RESPONSE_MODEL_V2


def vector(response,key,layers):
    values=np.asarray(response[key],np.float64)
    return np.array([values.mean()]) if layers==1 else values


def environment(row,response):
    return response['temperature_q10']**((row['temperature_c']-20.)/10.) * max(.1,1.+response['humidity_sensitivity']*(row['relative_humidity']-.5))


def storage_response(film,layers):
    history=validate_history(film.storage_history,'storage')
    if history is None:return None
    response=validate_response(film.time_response)
    if response is None:raise ValueError('Storage history requires material time response')
    years=sum(row['duration_days']/365.*environment(row,response) for row in history['segments'])
    if years==0:return None
    speed=np.exp2(-np.minimum(100.,vector(response,'storage_speed_loss_stops_per_year',layers)*years))
    fog=-np.expm1(-vector(response,'storage_fog_rate_per_year',layers)*years)
    return speed.astype(np.float32),fog.astype(np.float32)


def attach_time_operations(program,recipe):
    waiting=validate_history(recipe.latent_wait,'latent');light=validate_history(recipe.post_process_light,'light')
    if waiting is None and light is None:return program
    if not program.steps:raise ValueError('Film time history requires material pools')
    from film_foundry.core.film_process.operators import FilmProcessStep,FilmProcessAction
    steps=[replace(s,bath_index=i if s.bath_index is None else s.bath_index,
                   label=s.label or f'bath_{(i if s.bath_index is None else s.bath_index)+1}:{s.action.value}') for i,s in enumerate(program.steps)]
    last=max(s.bath_index for s in steps)
    if waiting is not None:steps.insert(0,FilmProcessStep(FilmProcessAction.AGE_LATENT,label='latent_storage_before_processing',bath_index=0,bath_entry=False,time_history=waiting))
    if light is not None:steps.append(FilmProcessStep(FilmProcessAction.LIGHT_RESIDUAL_HALIDE,label='residual_salt_light_after_processing',bath_index=last,bath_entry=False,time_history=light))
    return replace(program,steps=tuple(steps))


def age_latent(state,history,response):
    if response is None:raise ValueError('Latent waiting requires material time response')
    layers=state.halide.shape[-1];fade=vector(response,'latent_fade_rate_per_day',layers);fog=vector(response,'latent_fog_rate_per_day',layers)
    total=fade+fog;equilibrium=np.divide(fog,total,out=np.zeros_like(fog),where=total>0)
    changed=0.
    for row in history['segments']:
        days=row['duration_hours']/24.*environment(row,response)
        if days==0:continue
        fraction=(-np.expm1(-total*days)).astype(np.float32)
        delta=(equilibrium.astype(np.float32)-state.developability)*fraction
        changed+=float(np.sum(np.abs(delta)*state.halide,dtype=np.float64))
        state.developability+=delta
        np.clip(state.developability,0.,1.,out=state.developability)
    return changed


def printout_dose(history,response,layers):
    dose=np.zeros(layers,np.float64)
    for row in history['segments']:
        light=np.asarray(row['light_rgb'],np.float64)
        if layers==1:light=np.array([light.mean()])
        dose+=row['duration_hours']*row['relative_illuminance']*light
    dose*=vector(response,'printout_rate_per_light_hour',layers)
    return dose


def light_surface_halide(fields,history,response):
    """Convert the actual surface Ag-equivalent salt, retaining its ownership.

    The deposited salt is a non-image-selective, bleached silver pool. Its
    scalar response uses the mean layer dose, not a new exposure or dye pool.
    """
    response=validate_response(response)
    if response is None or response['model']!=RESPONSE_MODEL_V2 or history is None or 'surface_halide_mg_m2' not in fields:
        return None
    history=validate_history(history,'light')
    dose=float(np.mean(printout_dose(history,response,3)))*response['bleached_printout_ratio']
    fraction=float(-np.expm1(-dose))
    halide=fields['surface_halide_mg_m2']
    converted=halide*fraction
    halide-=converted
    fields['surface_silver_mg_m2']+=converted
    if 'emulsion_halide_mg_m2' in fields:
        owned=fields['emulsion_halide_mg_m2']*fraction
        fields['emulsion_halide_mg_m2']-=owned
        fields['emulsion_silver_mg_m2']+=owned
    return {'model':'surface_salt_printout_v1','converted_mg_m2':float(np.mean(converted,dtype=np.float64)),
            'converted_fraction':fraction,'clock':'explicit_durations_only',
            'response':'mean_layer_dose_times_bleached_ratio','ownership':'preserve_salt_attachment'}


def light_residual_halide(state,history,response):
    if response is None:raise ValueError('Residual light exposure requires material time response')
    from film_foundry.core.film_process.formation_history import BathFormationResponse,ensure_history,add_product
    dose=printout_dose(history,response,state.halide.shape[-1])
    if not np.any(dose):return 0.
    ensure_history(state)
    converted=np.zeros_like(state.halide)
    # Both pools are silver-equivalent amounts. No dye coupling occurs in
    # this dry, developer-free operation. Already fixed/removed salt is gone.
    for name,scale in (('halide',1.),('bleached_halide',response['bleached_printout_ratio'])):
        pool=getattr(state,name)
        if pool is None:continue
        delta=pool*(-np.expm1(-dose*scale)).astype(np.float32)
        pool-=delta;converted+=delta
    state.developability[state.halide<=0.]=0.
    if state.formation_history is not None:
        formed=BathFormationResponse(0.,response['printout_grain_factor'],response['printout_grain_radius_factor'],response['printout_grain_clump_factor'])
        add_product(state,'silver',converted,None,formed)
    state.metallic_silver+=converted
    return float(np.sum(converted,dtype=np.float64))
