"""Resolve control intent before the single material-processing pipeline."""
from copy import deepcopy
from film_foundry.model.config import DevelopRecipeConfig,LookAdjustConfig
from film_foundry.contracts.process_steps import validate_process_steps,validate_process_conditions

RAW_CONDITIONS=('time_min','temperature_c','concentration','agitation','agitation_time_s','developer_exhaustion','fixer_exhaustion','bath_transfer_s')
RAW_CHEMISTRY=('developer_type','fixer_type')
BATH_ACTIONS=('develop_silver','develop_color','activate_remaining_halide','bleach_silver','fix_halide','remove_silver','remove_auxiliary','bleach_mask_dye')


def condition_driven_config(config):
    """Keep actual bath inputs; reset legacy desired-result controls privately.

    Existing kinetics and compatibility operators remain authoritative. This
    does not turn a hot bath into an invented temperature shock or stain map.
    """
    result=deepcopy(config);old=result.chemistry
    if old.program_key=='legacy_density':raise ValueError('Roll darkroom requires a material process program')
    recipe=DevelopRecipeConfig()
    from film_foundry.contracts.process_events import validate_process_events
    recipe.process_events=validate_process_events(old.process_events)
    from film_foundry.contracts.effect_intent import validate_effect_intent
    recipe.effect_intent=validate_effect_intent(old.effect_intent)
    from film_foundry.contracts.bath_overrides import validate_bath_overrides
    recipe.bath_overrides=validate_bath_overrides(old.bath_overrides)
    recipe.latent_wait=deepcopy(old.latent_wait)
    recipe.post_process_light=deepcopy(old.post_process_light)
    for name in (*RAW_CONDITIONS,*RAW_CHEMISTRY,'program_key','process_mode','frame_size','frame_long_edge_mm','incident_identity_seed','formation_history_model'):
        setattr(recipe,name,deepcopy(getattr(old,name)))
    validate_process_conditions({name:getattr(recipe,name) for name in (*RAW_CONDITIONS,*RAW_CHEMISTRY)})
    from film_foundry.core.development import DEVELOPER_PROFILES,FIXER_PROFILES
    def check_chemistry(values):
        for key,allowed in (('developer_type',set(DEVELOPER_PROFILES)|{'hardening','hardener'}),('fixer_type',set(FIXER_PROFILES)|{'fresh_rapid','hardener'})):
            if key in values and str(values[key]).strip().lower().replace('-','_').replace(' ','_') not in allowed:
                raise ValueError(f'Unknown {key}: {values[key]}')
    check_chemistry({key:getattr(recipe,key) for key in RAW_CHEMISTRY})
    for name in old.__dataclass_fields__:
        if name.startswith(('light_leak_','exposure_occlusion_')):
            setattr(recipe,name,deepcopy(getattr(old,name)))
    if old.process_steps is None and recipe.program_key=='color_negative_bleach_bypass' and not recipe.bath_overrides:
        # Omit the bath itself, rather than request a retained-silver strength.
        old.process_steps=({'action':'develop_color'},{'action':'fix_halide'},{'action':'remove_auxiliary'})
    if old.process_steps is not None:
        rows=[]
        for row in validate_process_steps(old.process_steps):
            if row['action']=='destroy_dye' and row.get('dye_destruction_basis')=='compatibility':continue
            if row['action'] not in BATH_ACTIONS:raise ValueError('Direct dye-damage commands are not bath conditions')
            check_chemistry(row)
            bath={name:deepcopy(getattr(recipe,name)) for name in (*RAW_CONDITIONS,*RAW_CHEMISTRY)}
            bath.update({k:deepcopy(v) for k,v in row.items() if k in {'action','label','solution',*RAW_CONDITIONS,*RAW_CHEMISTRY}})
            rows.append(bath)
        recipe.process_steps=validate_process_steps(rows)
        # Retain the legacy aggregate and auxiliary environment. The current
        # bath-product model derives fog/grain from real operators/products
        # and no longer applies this environment's final D-min/D-max gain.
        first=next((row for row in rows if row['action'] in ('develop_silver','develop_color')),None)
        if first:
            for name in (*RAW_CONDITIONS,*RAW_CHEMISTRY):setattr(recipe,name,first[name])
        fixing=next((row for row in reversed(rows) if row['action']=='fix_halide'),None)
        if fixing:
            for name in ('fixer_type','fixer_exhaustion'):setattr(recipe,name,fixing[name])
    if recipe.process_steps and any(row['action']=='develop_color' for row in recipe.process_steps):
        # This is a material compatibility consequence, never a requested dye-loss strength.
        recipe.process_steps=(*recipe.process_steps,{'action':'destroy_dye','dye_destruction_basis':'compatibility','label':'material_process_dye_stability'})
    validate_process_steps(recipe.process_steps)
    result.chemistry=recipe
    neutral=LookAdjustConfig()
    for name in ('negative_contrast','halation_multiplier','halation_sensitivity','grain_multiplier','grain_size_multiplier','look_strength','emulsion_mtf_strength','digital_artifact_suppression','halation_edge_compensation'):
        setattr(result.look,name,getattr(neutral,name))
    result.process_control_mode='principle'
    return result
