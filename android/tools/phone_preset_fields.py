"""Exported capabilities, not the desktop complete parameter set."""
allowed = {
    "film": {"grain_strength", "grain_scales", "grain_scale_weights", "name", "color_process", "image_polarity", "contrast", "film_base_density_rgb",
             "retained_halide_density_rgb", "layer_sensitivity_matrix", "dye_absorption_matrix",
             "hd_gamma", "log_exposure_toe", "log_exposure_shoulder", "hd_toe_width", "hd_shoulder_width",
             "density_min", "density_max", "extreme_exposure_reversal_strength", "extreme_exposure_reversal_start_loge",
             "extreme_exposure_reversal_width", "clear_support_density_rgb", "chemical_fog_susceptibility",
             "dir_material_response_matrix", "dir_diffusion_radius",
             "silver_grain_strength", "silver_grain_radius", "silver_grain_clump_mix",
             "light_piping_strength", "light_piping_depth", "light_piping_edge_mode", "light_piping_layer_weights",
             "halation_return_model", "halation_layer_return_weights", "halation_spread_scale_weights",
             "emulsion_highlight_spread_strength", "emulsion_highlight_spread_radius", "emulsion_highlight_spread_threshold",
             "emulsion_highlight_spread_softness", "emulsion_highlight_spread_layer_radius_scales", "emulsion_highlight_spread_layer_strengths",
             "auxiliary_layer_amount", "auxiliary_layer_density_rgb", "experimental_mask_bleach_susceptibility", "experimental_mask_bleach_dye_damage",
             "cross_process_silver_development", "cross_process_dye_coupling", "cross_process_activation", "cross_process_silver_bleach",
             "cross_process_halide_fixing", "cross_process_silver_removal", "cross_process_dye_stability", "cross_process_auxiliary_removal", "cross_process_layer_balance"},
    "scanner": {"scanner_light_color", "transmission_light_ev", "transmission_light_temperature_k", "print_mapping_mode",
                "remove_base_mask", "invert_transmission", "scan_saturation", "scan_normalize",
                "scan_normalize_strength", "scan_normalize_mode", "scan_black_percentile",
                "scan_white_percentile", "tone_curve", "color_curve_rgb"},
}
# Only fields already consumed by the independent phone runtime.
allowed['film'].update({
    'medium_family','medium_process','emulsion_mtf_strength','emulsion_blur_radius',
    'halation_strength','halation_threshold','halation_softness','halation_core_radius','halation_outer_radius',
    'halation_core_mix','halation_color','halation_peak_threshold','halation_area_suppression',
    'time_response','storage_history','reciprocity_response'})
allowed['scanner'].update({'color_mixer','scanner_response_matrix','print_reference_density','print_gamma','positive_scan_color_control_strength'})
allowed['film'].update({
    'positive_density_contrast', 'positive_density_bias', 'positive_latitude_compression',
    'positive_dye_saturation', 'positive_midtone_density', 'positive_shadow_toe',
    'positive_shadow_toe_width', 'positive_highlight_shoulder', 'positive_highlight_shoulder_width',
    'positive_highlight_chroma_retention', 'positive_shadow_chroma_retention',
})
