"""Mechanism inventory: stage ownership and currently implemented drivers.

This describes existing reduced operators, not physical calibration. Missing
causal models remain explicit, rather than silently interpreting effect knobs
as measured process quantities.
"""
MECHANISMS = {
    'development': {'stage':'development_formation','drivers':('time_min','temperature_c','concentration','developer_type','developer_exhaustion','agitation','agitation_time_s'),'principle':'implemented_reduced'},
    'retained_silver': {'stage':'material_pool','drivers':('bath_sequence','bleach_silver'),'expected':('silver_retention','silver_bleach_completion'),'principle':'implemented_reduced'},
    'retained_halide': {'stage':'material_pool','drivers':('bath_sequence','fixer_type','fixer_exhaustion'),'expected':('halide_fixing_completion',),'principle':'implemented_reduced'},
    'premature_fixing': {'stage':'material_pool','drivers':('process_events',),'events':('fixer_contact',),'principle':'implemented_selective_halide_loss'},
    'dye_loss': {'stage':'material_dye_pool','drivers':('material_process_compatibility','bleach_mask_dye','process_events'),'events':('dye_bath',),'expected':('dye_damage_strength',),'principle':'compatibility_mask_bleach_and_chemical_erosion'},
    'uneven_development': {'stage':'development_formation','drivers':('process_events',),'events':('bath_contact','rotary_contact','developer_inhibitor','wash'),'expected':('uneven_development',),'principle':'implemented_contact_and_mobile_inhibitor'},
    'chemical_stain': {'stage':'post_process_component_density','drivers':('process_events',),'events':('contaminate','wash'),'expected':('chemical_stain',),'principle':'implemented_soluble_and_bound_residue'},
    'silver_plating': {'stage':'post_process_surface_silver_density','drivers':('process_events',),'events':('silver_bath',),'expected':('silver_plating',),'principle':'implemented_external_mass_limited_deposition'},
    'reticulation': {'stage':'post_process_emulsion_structure_density','drivers':('process_events','bath_transfer_s','temperature_c'),'events':('temperature','harden','wash','silver_bath'),'expected':('reticulation_strength',),'principle':'implemented_reduced_thermal_damage'},
}


def process_control_trace(config, *, resolved_baths=None):
    from dataclasses import asdict
    from film_foundry.contracts.bath_domains import bath_domain_trace
    recipe=asdict(config.chemistry)
    drivers={name for item in MECHANISMS.values() for name in item['drivers'] if name in recipe}
    return {'version':1,'mode':config.process_control_mode,'bath_domains':bath_domain_trace(),
            'formation_environment':('first_development_bath_global_grain_fog' if config.process_control_mode=='principle' and recipe['process_steps'] else 'global_bath'),
            'bath_conditions':{name:recipe[name] for name in sorted(drivers)},
            'steps':recipe['process_steps'],
            'bath_overrides':recipe['bath_overrides'],
            'bath_kinetics':[{'step':i+1,'action':row['action'],'model':row['solution']['model'] if row.get('solution') else 'legacy_recipe_kinetics',
                'solution':row.get('solution')} for i,row in enumerate(resolved_baths if resolved_baths is not None else recipe['process_steps'] or ())],
            'mechanisms':{name:dict(item) for name,item in MECHANISMS.items()},
            'model_scope':'creative_reduced_not_calibrated_chemistry'}
