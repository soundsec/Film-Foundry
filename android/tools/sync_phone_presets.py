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
from phone_preset_fields import allowed

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
        entries.append(dict(id=key, label_zh=zh, label_en=en, source=source.as_posix(), recipe=recipe))
    if args.catalog_only:
        entries += [entry for key,entry in previous.items() if key not in {e[0] for e in items}]
    output[group] = entries
if not args.scanners_only:
    output['daily_material_ids'] = [item[0] for item in definitions['materials'] if item[0] not in {'blue_highlight_negative', 'red_rich_negative', 'high_contrast_push'}]

# New optional RGB proxies; no claim of measured spectral sensitivity or infrared support.
if not args.scanners_only and not args.catalog_only:
    from copy import deepcopy
    base=next(e for e in output['materials'] if e['id']=='monochrome_fine_negative')
    for key,zh,en,weights in [
        ('monochrome_ortho_negative','黑白负片 · 正色响应','B&W negative · Orthochromatic response',[.02,.68,.30]),
        ('monochrome_blue_sensitive_negative','黑白负片 · 蓝敏响应','B&W negative · Blue-sensitive response',[.01,.09,.90])]:
        entry=deepcopy(base);entry.update(id=key,label_zh=zh,label_en=en,source='phone_rgb_response_approximation_v1')
        entry['recipe']['film'].update(name=en,layer_sensitivity_matrix=[weights[:] for _ in range(3)])
        output['materials'].append(entry);output['daily_material_ids'].append(key)
if args.catalog_only:
    known={item[0] for item in definitions['materials']}
    output['daily_material_ids'] += [entry['id'] for entry in output['materials'] if entry['id'] not in known]
if not args.catalog_only and not args.scanners_only:
    output['revision']='phone_reduced_materials_v4'

# Embedded creative materials must follow the same catalog as newly loaded rolls.
pair_path=destination.parent/'phone_creative_pairings.json'
if not args.scanners_only and not args.catalog_only and pair_path.exists():
    pairs=json.loads(pair_path.read_text(encoding='utf-8'))
    materials={e['id']:e['recipe']['film'] for e in output['materials']}
    for pair in pairs['entries']:
        pair['recipe']['film']=materials[pair['material_id']]
    pair_path.write_text(json.dumps(pairs,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

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
