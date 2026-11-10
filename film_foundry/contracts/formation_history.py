"""Versioned film-family formation history; independent of observation."""
LEGACY = 'legacy_global_v1'
MODEL = 'bath_products_v1'
LOCAL_MODEL = 'bath_products_local_v2'
MODELS = (LEGACY, MODEL, LOCAL_MODEL)


def validate_model(value):
    if value not in MODELS:
        raise ValueError('Unsupported film formation history model')
    return value


def enabled(recipe):
    return validate_model(recipe.formation_history_model) != LEGACY


def local_enabled(recipe):
    return validate_model(recipe.formation_history_model) == LOCAL_MODEL


def capabilities(model):
    """Detached stage/field description for frontends; no numerical imports."""
    model=validate_model(model);local=model==LOCAL_MODEL
    return {'model':'film_formation_capabilities_v1','formation_model':model,
        'history_enabled':model!=LEGACY,'local_response':local,
        'stages':[
            {'id':'bath_formation','writes':['retained_product_moments'] if model!=LEGACY else [],
             'time':'reaction_boundary'},
            {'id':'grain_response','reads':['retained_products','retained_product_moments'],
             'writes':['local_grain_response'] if local else ['global_grain_response'],
             'time':'after_last_reaction_before_compaction'},
            {'id':'grain_realization','reads':['formed_medium','grain_response'],
             'writes':['optical_microstructure'],'time':'once_at_finalization'},
            {'id':'observation','reads':['final_medium'],'writes':[],'read_only':True}],
        'fields':[{'id':key,'unit':'relative_factor','products':['silver','dye'],
                   'resolution':'retained_material_pixels' if local else 'frame',
                   'lifetime':'formation_to_final_grain','editable':False}
                  for key in ('grain_factor','grain_radius_factor','grain_clump_factor')],
        'limits':['statistical_microstructure_not_individual_crystals',
                  'no_grain_growth_or_individual_particle_transport',
                  *(['radius_interpolates_three_shared_noise_scales'] if local else [])]}


def validate_fog_susceptibility(value):
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 10:
        raise ValueError('chemical_fog_susceptibility must be between 0 and 10')
    return float(value)
