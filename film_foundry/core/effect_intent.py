"""Compile desired material quantities into ordinary, editable operations.

No scene pixels, result blending or secondary image-processing pipeline.
Amounts are linear in material quantities; display RGB is not linear density.
"""
from copy import deepcopy
import math
import numpy as np
from film_foundry.contracts.effect_intent import validate_effect_intent,EFFECTS
from film_foundry.contracts.process_events import EVENT_MODEL_V5,EVENT_MODEL_V6,EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15,validate_process_events,validate_process_response


def compile_effect_intent(config,shape=(512,512)):
    intent=validate_effect_intent(config.chemistry.effect_intent)
    if intent is None:return config,None
    result=deepcopy(config);recipe=result.chemistry;recipe.effect_intent=None
    for key in intent['amounts']:setattr(recipe,key,0.)
    audit={'intent':intent,'targets':{},'generated_events':[],'scope':'material_quantities_not_display_rgb'}
    if not intent['enabled'] or not any(intent['amounts'].values()):return result,audit
    if recipe.program_key=='legacy_density':raise ValueError('Process effect intent requires material-pool processing')
    from film_foundry.core.process_operations import form_process_surface,control_grid,frame_extent,_footprint,source_bath_rows
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    from film_foundry.core.media import is_monochrome_material
    program=program_from_develop_recipe(recipe,mode='bw_negative' if is_monochrome_material(result) else result.mode,material_process=result.film.medium_process)
    rows=source_bath_rows(result,program)
    if intent['amounts'].get('silver_retention',0.):
        from film_foundry.core.retention_intent import compile_retention
        compile_retention(result,intent['amounts']['silver_retention'],program,rows,audit)
        program=program_from_develop_recipe(recipe,mode='bw_negative' if is_monochrome_material(result) else result.mode,material_process=result.film.medium_process)
        rows=source_bath_rows(result,program)
    prior=validate_process_events(recipe.process_events)
    events=deepcopy(prior['events'] if prior else [])
    generated=[];amounts=intent['amounts'];response=validate_process_response(result.film.process_response)
    seed=int(recipe.incident_identity_seed if recipe.incident_identity_seed is not None else result.random_seed or 0)%2147483647
    def spread(offset):return {'distribution_variation':1.,'distribution_scale_mm':2.,'distribution_seed':(seed+offset)%2147483647}
    if amounts['uneven_development']:
        bath=next((i+1 for i,row in enumerate(rows) if row['action'] in ('develop_silver','develop_color')),None)
        if bath is None:raise ValueError('Uneven development needs a developing bath')
        if any(e['kind'] in ('developer_inhibitor','wash','harden') and (e.get('before_bath',0)<=bath if e.get('before_bath') else e.get('after_bath',0)<bath and e.get('after_bath',0)>0) for e in events):
            audit['targets']['uneven_note']='Existing mobile load or washing can alter the isolated-bath calibration'
        event={'kind':'developer_inhibitor','before_bath':bath,'retained_ml_m2':20.,'radius_mm':0.,**spread(11)}
        footprint=_footprint(control_grid(shape),frame_extent(result,shape),event)
        half=response.get('inhibitor_half_response_mmol_m2',.2)
        # Solve in units of the material's half response. Fixed absolute-load
        # precision fails for highly sensitive materials; weak responses may
        # hit the permitted concentration limit before reaching 60% loss.
        def rate_loss(relative):return float(np.mean(1-1/(1+footprint*relative),dtype=np.float64))
        limit=200./half;high=min(2.,limit)
        while high<limit and rate_loss(high)<.6:high=min(high*2,limit)
        maximum=min(.6,rate_loss(high));target=maximum*amounts['uneven_development']
        if maximum<.6:audit['targets']['uneven_reachable_maximum']=maximum
        low=0.
        for _ in range(24):
            load=(low+high)/2
            loss=rate_loss(load)
            if loss<target:low=load
            else:high=load
        event['concentration_mmol_l']=(low+high)/2*half*50
        generated.append(event);audit['targets']['mean_development_rate_loss']=target
    # Sample only the bounded pre-existing surface history, never a scene image.
    needs_history=amounts['silver_plating'] or amounts['reticulation_strength'] or amounts.get('dye_damage_strength',0.)
    _,history=form_process_surface(result,(32,32),np.random.default_rng(seed),program=program) if needs_history else (None,None)
    wet_rows=[row for row in rows if row['action'] not in ('destroy_dye','activate_remaining_halide')]
    temperature=float(wet_rows[-1].get('temperature_c',recipe.temperature_c)) if wet_rows else float(recipe.temperature_c)
    wet=sum(float(row.get('time_min',recipe.time_min))*60 for row in wet_rows) or recipe.time_min*60
    wet+=sum(e['duration_s']+e['transfer_s'] for e in events if e['kind']=='dye_bath')
    hardening=response['initial_hardening']
    if history:
        temperature=history['final_temperature_c'];wet=history['wet_seconds'];hardening=history['final_hardening_fraction']
    if amounts['chemical_stain']:
        mass=20.*amounts['chemical_stain']
        generated.append({'kind':'contaminate','concentration_mg_l':mass*50,'retained_ml_m2':20.,'contact_s':0.,'radius_mm':0.,**spread(23)})
        audit['targets']['residue_mg_m2']=mass
    if amounts['silver_plating']:
        fraction=-math.expm1(-response['silver_reduction_l_mmol_s']*2.*120.*2**((temperature-20)/20))
        capacity=min(2*.1*107.8682*fraction,10000*.1*fraction)
        maximum=min(12.,capacity);mass=maximum*amounts['silver_plating']
        audit['targets']['surface_silver_mg_m2']=mass
        if fraction>0:
            generated.append({'kind':'silver_bath','silver_mg_l':mass/(.1*fraction),'solution_ml_m2':100.,'reducer_mmol_l':2.,
                'duration_s':120.,'temperature_c':temperature,'transfer_s':0.,'radius_mm':0.,**spread(37)})
            wet+=120.
        else:audit['targets']['silver_unreachable']='Material reduction response is zero'
    if amounts.get('dye_damage_strength',0.):
        from film_foundry.core.bath_kinetics import dye_attack_fraction
        rate=response.get('dye_attack_l_mmol_s',.002)
        if is_monochrome_material(result) or not any(row['action']=='develop_color' for row in rows):
            audit['targets']['dye_unreachable']='This material/process does not form dye'
        elif rate==0:audit['targets']['dye_unreachable']='Material dye-attack response is zero'
        else:
            maximum=min(.75,dye_attack_fraction(rate,1000.,120.,temperature))
            target=maximum*amounts['dye_damage_strength']
            concentration=-math.log1p(-target)/(rate*120.*2**((temperature-20.)/20.))
            generated.append({'kind':'dye_bath','concentration_mmol_l':min(1000.,concentration),'duration_s':120.,'temperature_c':temperature,'transfer_s':0.})
            audit['targets']['remaining_dye_loss_fraction']=target;wet+=120.
    if amounts['reticulation_strength']:
        endpoint=60. if temperature<=30 else 0.
        coefficient=response['thermal_expansion_per_c']*(1-.7*hardening)
        threshold=response['yield_strain']*(1+hardening)
        hydrated=-math.expm1(-wet/response['hydration_s'])
        excess=max(0.,abs(endpoint-temperature)*coefficient-threshold)
        maximum=min(.35,-math.expm1(-(excess/response['damage_strain'])**2*hydrated))
        target=maximum*amounts['reticulation_strength'];audit['targets']['additional_damage_fraction']=target
        if target>0 and coefficient>0 and hydrated>0:
            delta=(threshold+response['damage_strain']*math.sqrt(-math.log1p(-target)/hydrated))/coefficient
            generated.append({'kind':'temperature','temperature_c':min(60.,max(0.,temperature+(delta if endpoint==60. else -delta))), 'transfer_s':0.,'hold_s':0.})
        else:audit['targets']['damage_unreachable']='Material threshold exceeds available temperature range'
    # Existing explicit operations retain their order; synthesized after-bath
    # effects occur last. Before-bath inhibitor is scheduled by its bath identity.
    model=EVENT_MODEL_V6 if any(e['kind']=='dye_bath' for e in events+generated) or prior and prior['model']==EVENT_MODEL_V6 else EVENT_MODEL_V5
    if prior and prior['model'] in (EVENT_MODEL_V7,EVENT_MODEL_V8,EVENT_MODEL_V9,EVENT_MODEL_V10,EVENT_MODEL_V11,EVENT_MODEL_V12,EVENT_MODEL_V13,EVENT_MODEL_V14,EVENT_MODEL_V15):model=prior['model']
    recipe.process_events=validate_process_events({'model':model,'events':events+generated})
    audit['generated_events']=deepcopy(recipe.process_events['events'][len(events):])
    return result,audit
