"""Cheap, read-only causal disclosures for a recipe; never an image renderer.

Codes describe linked responses, not independent random accident probabilities.
The final medium stores the same codes that the front end can explain.
"""
from film_foundry.contracts.effect_intent import validate_effect_intent
from film_foundry.contracts.process_events import validate_process_events,validate_process_response


def describe_process_couplings(config):
    from film_foundry.core.film_process.recipe import program_from_develop_recipe
    from film_foundry.core.film_process.integration import _compatibility_for_program
    from film_foundry.core.media import is_monochrome_material
    recipe=config.chemistry;intent=validate_effect_intent(recipe.effect_intent)
    amounts=intent['amounts'] if intent else {}
    def desired(key):
        return (amounts[key] if intent['enabled'] else 0.) if key in amounts else float(getattr(recipe,key,0.))
    events=(validate_process_events(recipe.process_events) or {'events':[]})['events']
    kinds={e['kind'] for e in events}
    mono=is_monochrome_material(config)
    program=program_from_develop_recipe(recipe,mode='bw_negative' if mono else config.mode,material_process=config.film.medium_process)
    actions={step.action.value for step in program.steps}
    from film_foundry.core.process_operations import source_bath_rows
    rows=source_bath_rows(config,program)
    response=validate_process_response(config.film.process_response)
    findings=[]
    def add(code,**details):findings.append({'code':code,'details':details})
    from film_foundry.contracts.carbon_backing import validate_carbon_backing
    backing=validate_carbon_backing(config.film.carbon_backing)
    if backing and backing['initial_mg_m2']>0:add('carbon_backing_routing')
    from film_foundry.core.carbon_backing import exposure_return
    backing_return=exposure_return(backing)
    if backing_return is not None:add('carbon_backing_halation', **backing_return)
    if 'emulsion_shear' in kinds:add('emulsion_shear')
    if 'emulsion_wear' in kinds:add('emulsion_area_loss')
    if intent and not intent['enabled']:add('intent_bypassed')
    if any(row['action'] in ('develop_silver','develop_color') for row in rows):add('development_environment')
    from film_foundry.contracts.formation_history import enabled as history_enabled
    if history_enabled(recipe) and any(s.formation_response is not None for s in program.steps):
        add('retained_bath_products', chemical_fog_susceptibility=config.film.chemical_fog_susceptibility)
    if config.film.storage_history or recipe.latent_wait or recipe.post_process_light:add('film_time_stages')
    if config.film.storage_history:add('film_storage_time')
    if recipe.latent_wait:add('film_latent_time')
    if recipe.post_process_light:add('film_light_time')
    if recipe.post_process_light and (config.film.time_response or {}).get('model')=='film_time_response_v2':add('surface_salt_light')
    if config.film.reciprocity_response:
        add('film_reciprocity', duration_seconds=config.exposure.duration_seconds)
    if desired('uneven_development')>0 or kinds.intersection({'developer_inhibitor','bath_contact','rotary_contact'}):add('access_and_density')
    thermal=desired('reticulation_strength')>0 or bool(kinds&{'temperature','wash','silver_bath','dye_bath','backing_condition','backing_wash','emulsion_wear','emulsion_shear'}) or len({row.get('temperature_c',recipe.temperature_c) for row in rows if row['action'] not in ('activate_remaining_halide','destroy_dye')})>1
    hardening='harden' in kinds or any(row.get('solution') and row['solution'].get('hardener_g_l',0)>0 for row in rows)
    if hardening or (thermal and response['initial_hardening']>0):add('hardening_wash_damage')
    if thermal:add('thermal_damage_threshold')
    if desired('chemical_stain')>0 or 'contaminate' in kinds:add('residue_density_and_colour')
    if desired('silver_plating')>0 or 'silver_bath' in kinds:
        add('surface_silver_reflection' if response['model']=='creative_process_response_v4' else 'surface_silver_density')
    if 'silver_bath' in kinds and (recipe.process_events or {}).get('model') in ('film_process_events_v7','film_process_events_v8','film_process_events_v9','film_process_events_v10','film_process_events_v11','film_process_events_v12','film_process_events_v13','film_process_events_v14','film_process_events_v15'):add('surface_silver_clearing')
    if desired('dye_damage_strength')>0 or 'dye_bath' in kinds:add('dye_loss_remaining_image' if not mono and 'develop_color' in actions else 'no_dye_pool')
    if any(row.get('solution',{}).get('secondary') for row in rows if row.get('solution')):add('shared_bath_reactions')
    if desired('silver_retention')>0 or recipe.silver_bleach_completion<1 or recipe.program_key=='color_negative_bleach_bypass' or any(s.action.value=='bleach_silver' and s.strength<1 for s in program.steps):add('retained_silver')
    if any(row['action']=='bleach_mask_dye' for row in rows):add('mask_and_image_dye')
    if 'fixer_contact' in kinds:add('premature_fixing')
    has_fixer='fix_halide' in actions
    if not has_fixer or recipe.fixer_exhaustion>0 or recipe.halide_fixing_completion<1:add('residual_halide')
    profile=_compatibility_for_program(config.film,program.key,1 if mono else 3,program=program)
    if mono and {'develop_color','bleach_silver','fix_halide'}<=actions:add('silver_only_colour_clearing')
    rates=('silver_development','dye_coupling','activation','silver_bleach','halide_fixing','silver_removal','dye_stability','auxiliary_removal')
    if any(getattr(profile,key)!=1. for key in rates) or profile.layer_balance is not None:add('material_process_mismatch',profile={key:getattr(profile,key) for key in rates})
    legacy=[key for key in ('chemical_stain','uneven_development','silver_plating') if key not in amounts and desired(key)>0]
    if legacy:add('legacy_incident_multipliers',controls=legacy)
    if desired('process_variation')>0:add('condition_variation')
    return {'model':'process_couplings_v1','scope':'condition_links_not_probability_predictions','findings':findings}
