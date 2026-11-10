"""Descriptive operation semantics, independent of numerical/platform runtimes.

These are causal domains, not a second implementation of bath chemistry or
parameter validation. Runtime call signatures and stage guards remain authoritative.
"""
MODEL='material_capabilities_v1'

# Targets name material components, not archive array names. Several fields may
# represent one component, and the same operation can affect several components.
COMMON={
    'prepare':('support','preparation','none'),
    'expose':('photosensitive_layer','exposure','input_dose'),
    'light_leak':('photosensitive_layer','exposure','relative_dose'),
    'handling':('surface_residue','surface','none'),
    'develop':('image_forming_material','development','incremental_seconds'),
    'fix':('unreacted_material','fixing','incremental_seconds'),
    'wash':('soluble_material,surface_residue','washing','incremental_seconds'),
    'sensitize':('photosensitive_layer','sensitization','incremental_seconds'),
    'dry':('paper_water','drying','incremental_seconds'),
}

TRANSFER={
    'environment':('donor,receiver,reagent','environment_conditions','future_intervals_only'),
    'contaminate':('accessible_surface,transfer_reaction','contamination','none'),
    'clean':('accessible_surface,soluble_inhibitor','surface_cleaning','none'),
    'spread':('reagent,donor,receiver','reagent_spreading','arrival_profile_seconds'),
    'advance':('donor,receiver,reagent','development_and_transfer','cumulative_fixed_environment'),
}

FAMILIES={
    'peel_apart_transfer_v1':{
        'current_version':5,
        'operations':{**TRANSFER,
            'peel':('donor,receiver','separation','stop_transfer'),
            'clear_negative':('donor_surface,donor_image_layer','cleanup','none')},
        'limits':['normalized_finite_developer_and_acid_not_measured_ph','bounded_illuminated_competition_quadrature',
                  'no_reagent_transport_between_pixels_or_bath_mixing','optical_residue_not_measured_mass',
                  'negative_requires_declared_silver_donor_and_cleanup','fractional_layer_ownership_not_mesh_tear'],
        'v4_limits':['piecewise_constant_environment','no_bath_mixing_or_inhibitor_inventory',
                  'negative_requires_declared_silver_donor_and_cleanup','fractional_layer_ownership_not_mesh_tear',
                  'optical_residue_not_measured_mass'],
        'v3_limits':['piecewise_constant_environment','no_mechanical_separation_or_independent_cleanup',
                     'negative_requires_declared_silver_donor_and_cleanup'],
        'v2_limits':['fixed_environment_after_spread','surface_residue_is_optical'],
        'legacy_limits':['v1_no_handling_or_negative_cleanup','v1_no_reagent_leak_or_spread_unevenness'],
    },
    'integral_instant_v1':{
        'current_version':4,
        'operations':{**TRANSFER,
            'eject':('sealed_sheet','ejection','none'),
            'advance':('donor,receiver,reagent,opacifier','development_and_transfer','cumulative_fixed_environment'),
            'stabilize':('donor,receiver,opacifier','development_and_transfer','remaining_to_material_limit'),
            'finish':('sealed_sheet,residual_reagent','explicit_final_snapshot','freeze_current_state')},
        'limits':['normalized_finite_developer_and_acid_not_measured_ph','bounded_illuminated_competition_quadrature',
                  'insufficient_acid_does_not_stabilize','finished_snapshot_may_retain_active_reagent',
                  'sealed_donor_not_separate_product','no_reagent_transport_between_pixels_or_bath_mixing'],
        'v3_limits':['piecewise_constant_environment','sealed_donor_not_separate_product',
                  'internal_contamination_only_before_sealing','no_bath_mixing_or_inhibitor_inventory'],
        'v2_limits':['fixed_environment_after_spread','light_response_applied_at_spread','surface_residue_is_optical'],
        'legacy_limits':['v1_no_handling','v1_no_solar_warming_or_unshielded_development'],
    },
    'daguerreotype_surface_v1':{
        'current_version':4,
        'operations':{
            'polish':('metal_surface','surface_preparation','none'),
            'sensitize':('photosensitive_surface','sensitization','none'),
            'develop':('latent_surface,image_particles','development','cumulative_fixed_environment'),
            'fix':('surface_sensitivity','fixing','none'),
            'gild':('gold_surface','surface_treatment','none'),
            'surface':('metal_surface','surface_assignment','none'),
            'weather':('metal_surface,image_particles','surface_wear','none'),
            'load_gilding':('gold_inventory','bath_loading','none'),
            'rinse_gilding':('soluble_gold,removed_gold','rinsing','none'),
            'finish':('plate,residual_halide,gold_inventory','explicit_final_snapshot','freeze_current_state')},
        'limits':['normalized_finite_local_gold_not_measured_alloy','gold_adhesion_not_sulfide_barrier',
                  'no_bath_mixing_or_spatial_gold_transport','finished_snapshot_does_not_complete_fixing',
                  'no_post_finish_halide_light_conversion','fix_completion_threshold'],
        'v3_limits':['normalized_surface_not_measured_alloy','gild_target_not_finite_bath_inventory','fix_completion_threshold'],
        'v2_limits':['fix_clears_sensitivity_without_bath_kinetics','gild_assigns_amount_without_bath_kinetics'],
        'legacy_limits':['v1_polish_direction_unavailable'],
    },
    'photographic_paper_v1':{
        'current_version':2,
        'operations':{
            'stop':('development_stage','stop','none'),
            'bleach_fix':('silver,silver_halide','bleaching_and_fixing','incremental_seconds')},
        'limits':['reduced_rc_fiber_hydration','local_wash_contact_not_finite_water_stock','surface_residue_is_optical','no_curl_or_fiber_deformation'],
        'v1_limits':['no_rc_fiber_base_separation','no_drying_tone_model','surface_residue_is_optical'],
        'legacy_limits':[],
    },
    'wet_collodion_v1':{
        'current_version':4,
        'operations':{
            'prepare':('support,collodion','preparation','none'),
            'wait':('collodion_moisture','drying','incremental_seconds'),
            'dry':('collodion_moisture','drying','incremental_seconds'),
            'varnish':('varnish_surface','surface_treatment','none'),
            'load_enhancer':('enhancement_silver_inventory','finite_bath_charge','none'),
            'intensify':('existing_silver,enhancement_silver_inventory','physical_intensification','incremental_seconds')},
        'limits':['normalized_silver_not_measured_concentration','no_finite_reducing_agent_or_ph_model',
                  'drying_access_loss_not_collodion_fluid_simulation','washing_ends_intensifier_activity',
                  'no_implicit_exposure_duration_or_wall_clock','no_cross_pixel_silver_transport'],
        'v3_limits':['finite_sensitizer_silver_carryover','no_separate_intensification_bath','residue_filters_initial_exposure_only',
                  'material_exposure_delay_applied_inside_expose'],
        'v2_limits':['no_external_developer_silver_pool','residue_filters_initial_exposure_only_in_v2','material_exposure_delay_applied_inside_expose'],
        'legacy_limits':['v1_no_pour_unevenness_or_residue_exposure_filter'],
    },
    'cyanotype_v1':{
        'current_version':3,
        'operations':{
            'wash':('iron_salts,reduced_pigment,blue_pigment,surface_residue','washing_and_pigment_formation','incremental_seconds'),
            'oxidize':('reduced_pigment,blue_pigment','oxidation','incremental_seconds'),
            'alkali_contact':('blue_pigment','alkaline_degradation','incremental_seconds')},
        'limits':['local_wash_contact_not_finite_water_stock','air_drying_couples_oxidation','surface_residue_is_optical','relative_rgb_not_uv_spectrum'],
        'v2_limits':['no_local_washing_input','surface_residue_is_optical','relative_rgb_not_uv_spectrum'],
        'legacy_limits':['v1_no_binding_or_fiber_fields'],
    },
    'platinum_palladium_v1':{
        'current_version':3,
        'operations':{
            'develop':('iron,platinum_salt,palladium_salt,platinum,palladium','development','incremental_seconds'),
            'clear':('iron_residue,unreacted_metal_salts','clearing','incremental_seconds'),
            'wash':('soluble_iron,unreacted_metal_salts,surface_residue','washing','incremental_seconds')},
        'limits':['local_clearing_not_finite_bath_stock','reduced_paper_hydration','surface_residue_is_optical','relative_rgb_not_uv_spectrum'],
        'v2_limits':['no_local_clearing_input','surface_residue_is_optical','relative_rgb_not_uv_spectrum'],
        'legacy_limits':['v1_no_absorption_clear_access_or_fiber_fields'],
    },
}


def operation_semantics(family,operation):
    target,domain,time=FAMILIES[family]['operations'].get(operation) or COMMON[operation]
    return {'targets':target.split(','),'domain':domain,'time_model':time}
