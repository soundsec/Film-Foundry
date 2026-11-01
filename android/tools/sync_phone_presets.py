"""Export only existing parameters consumed by the independent phone model."""
import json
import sys
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scanners-only', action='store_true', help='Update bundled scanner definitions without regenerating materials or spectral resources')
parser.add_argument('--catalog-only', action='store_true', help='Refresh material names and daily visibility; retain existing numeric definitions and Smits resources')
args = parser.parse_args()
if args.catalog_only and args.scanners_only:
    parser.error('choose one refresh scope')
destination = root / "android/app/src/main/assets/phone_builtin_recipes.json"
definitions = {
    "materials": [
        ('clear_modern_negative', '彩色负片 · 清透', 'Color negative · Clear'),
        ('warm_consumer_negative', '彩色负片 · 暖调', 'Color negative · Warm'),
        ('vivid_balanced_negative', '彩色负片 · 浓彩', 'Color negative · Rich'),
        ('monochrome_push', '黑白负片 · 硬调', 'B&W negative · Hard tone'),
        ('color_reversal_transparency', '彩色反转片 · 标准', 'Color reversal · Standard'),
        ('monochrome_reversal_transparency', '黑白反转片 · 细调', 'B&W reversal · Fine tone'),
        ('blue_highlight_negative', '彩色负片 · 冷蓝高光（兼容）', 'Color negative · Blue highlights (legacy)'),
        ('cyan_shadow_warm_highlight_negative', '彩色负片 · 青影暖光', 'Color negative · Cyan & amber'),
        ('red_rich_negative', '彩色负片 · 浓红（兼容）', 'Color negative · Rich red (legacy)'),
        ('high_contrast_push', '彩色负片 · 迫冲硬调（兼容）', 'Color negative · Push contrast (legacy)'),
        ('color_reversal_vivid', '彩色反转片 · 浓彩', 'Color reversal · Vivid'),
        ('monochrome_fine_negative', '黑白负片 · 微粒柔调', 'B&W negative · Fine grain'),
        ('color_reversal_amber', '彩色反转片 · 琥珀暖光', 'Color reversal · Amber'),
        ('color_reversal_cobalt', '彩色反转片 · 钴蓝冷光', 'Color reversal · Cobalt'),
        ('monochrome_reversal_graphic', '黑白反转片 · 硬调粗粒', 'B&W reversal · Graphic'),
    ],
    "scanners": [
        ("neutral_scan", "中性扫描", "Neutral scan"),
        ("bw_neutral_scan", "黑白扫描", "Black & white scan"),
        ("reference_soft_tone_scan", "柔和影调", "Soft tone"),
        ("reference_deep_tone_scan", "深浓影调", "Deep tone"),
        ("chrome_saturated_scan", "铬彩硬调", "Chrome vivid"),
        ("amber_cyan_scan", "青影暖光", "Cyan & amber"),
        ("violet_night_scan", "紫夜", "Violet night"),
        ("acid_green_scan", "酸绿", "Acid green"),
        ("red_cobalt_scan", "钴蓝红光", "Cobalt & red"),
    ],
}
allowed = {
    "film": {"grain_strength", "grain_scales", "grain_scale_weights", "name", "color_process", "image_polarity", "contrast", "film_base_density_rgb",
             "retained_halide_density_rgb", "layer_sensitivity_matrix", "dye_absorption_matrix",
             "hd_gamma", "log_exposure_toe", "log_exposure_shoulder", "density_max", "clear_support_density_rgb",
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
output = json.loads(destination.read_text(encoding='utf-8')) if args.scanners_only or args.catalog_only else {"model": "phone_builtin_recipes_v1"}
for group, items in definitions.items():
    if args.catalog_only and group != 'materials':
        continue
    if args.scanners_only and group != 'scanners':
        continue
    section = "film" if group == "materials" else "scanner"
    entries = []
    previous = {item["id"]: item for item in output.get(group, [])}
    for key, zh, en in items:
        source = Path("film_foundry/presets") / section / (key + ".json")
        raw = json.loads((root / source).read_text(encoding="utf-8"))[section]
        recipe = {section: {k: v for k, v in raw.items() if k in allowed[section]}}
        if args.catalog_only and key in previous:
            recipe = previous[key]["recipe"]
        if section == 'scanner':
            # Bound reciprocal negative signals before optional black/white normalization.
            # Curves retain each style; old saved/custom maps are not rewritten.
            recipe[section]['print_mapping_mode'] = 'sigmoid'
            recipe[section]['scan_normalize_strength'] = 1.0
        entries.append(dict(id=key, label_zh=zh, label_en=en, source=source.as_posix(), recipe=recipe))
    output[group] = entries
if not args.scanners_only:
    output['daily_material_ids'] = [item[0] for item in definitions['materials'] if item[0] not in {'blue_highlight_negative', 'red_rich_negative', 'high_contrast_push'}]

destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {len(output['materials'])} materials and {len(output['scanners'])} scanners")
if args.scanners_only or args.catalog_only:
    sys.exit(0)

# Reuse the project's own declared analytic basis; never import an external fitted table.
sys.path.insert(0, str(root))
from film_foundry.contracts.spectral_exposure import make_smits_exposure_profile
smits = {str(layers): make_smits_exposure_profile(layers).as_dict() for layers in (1, 3)}
(destination.parent / "phone_smits_profiles.json").write_text(
    json.dumps(smits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Wrote existing analytic Smits profiles for one and three layers")
