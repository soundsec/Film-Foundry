"""Generate original creative scanner definitions and explicit material/process pairings."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
base = dict(remove_base_mask=True, print_mapping_mode="printlike", scan_normalize=True,
            scan_normalize_strength=1.0, scan_normalize_mode="luma", scan_black_percentile=.25, scan_white_percentile=99.7)
tone = [[0, 0], [.18, .10], [.5, .46], [.8, .88], [1, 1]]
def curves(r, g, b):
    return [[[0, 0], [.2, y[0]], [.5, y[1]], [.8, y[2]], [1, 1]] for y in (r, g, b)]
styles = [
    ("chrome_saturated_scan", "铬彩硬调", "Chrome vivid", 1.45, None),
    ("amber_cyan_scan", "青影暖光", "Cyan & amber", 1.35, curves((.15,.54,.90),(.22,.52,.83),(.30,.50,.71))),
    ("violet_night_scan", "紫夜", "Violet night", 1.50, curves((.24,.60,.90),(.10,.38,.72),(.29,.64,.90))),
    ("acid_green_scan", "酸绿", "Acid green", 1.60, curves((.12,.37,.86),(.32,.67,.94),(.18,.33,.68))),
    ("red_cobalt_scan", "钴蓝红光", "Cobalt & red", 1.55, curves((.14,.58,.95),(.09,.39,.76),(.35,.51,.65))),
]
scanners = {}
for key, zh, en, saturation, color in styles:
    scanner = base | dict(scan_saturation=saturation, tone_curve=tone)
    if color is not None:
        scanner['color_curve_rgb'] = color
    scanners[key] = scanner
    (root/f'film_foundry/presets/scanner/{key}.json').write_text(json.dumps({'scanner': scanner}, indent=2)+'\n', encoding='utf-8')

# Shared recipe files are directly loadable on PC. Phone uses the same process/scanner values
# and the already exported, independently supported material definition.
pairings = [
    ('chrome_day', '铬彩日光', 'Chrome daylight', 'color_reversal_vivid', 'chrome_saturated_scan',
     dict(program_key='color_reversal', chemistry='native', time_min=8.0), 'positive', '鲜彩反转片 · 常规反转 · 铬彩硬调', 'Vivid reversal · native reversal · chrome'),
    ('amber_city', '暖城青影', 'Amber city', 'cyan_shadow_warm_highlight_negative', 'amber_cyan_scan',
     dict(program_key='color_negative', chemistry='native', developer_type='standard', time_min=8.0), 'negative', '青影暖光材料 · 常规彩负 · 青影暖光', 'Cyan/warm stock · native negative · cyan & amber'),
    ('violet_cross', '紫夜负冲', 'Violet cross', 'color_reversal_transparency', 'violet_night_scan',
     dict(program_key='color_negative', chemistry='cross', developer_type='high_contrast', time_min=9.0), 'negative', '彩色正片 · 交叉负冲 · 紫夜', 'Reversal stock · cross negative · violet'),
    ('acid_reversal', '酸绿逆冲', 'Acid reversal', 'warm_consumer_negative', 'acid_green_scan',
     dict(program_key='color_reversal', chemistry='cross', time_min=8.0), 'positive', '暖调彩负 · 交叉反转（保留片基）· 酸绿', 'Warm negative · cross reversal (base retained) · acid green'),
    ('cobalt_red', '钴蓝红潮', 'Cobalt red', 'red_rich_negative', 'red_cobalt_scan',
     dict(program_key='color_negative', chemistry='native', developer_type='push', push_stops=.5, time_min=10.0), 'negative', '浓红材料 · 迫冲药浴 · 钴蓝红光', 'Red-rich stock · push bath · cobalt & red'),
    ('silver_noir', '银盐黑场', 'Silver noir', 'monochrome_push', 'chrome_saturated_scan',
     dict(program_key='bw_negative', chemistry='native', developer_type='high_acutance', time_min=9.0), 'negative', '银盐黑白 · 高锐度药浴 · 硬调', 'Silver B&W · acutance bath · hard tone'),
]
catalog = json.loads((root/'android/app/src/main/assets/phone_builtin_recipes.json').read_text(encoding='utf-8'))
materials = {entry['id']: entry['recipe']['film'] for entry in catalog['materials']}
phone = []
for key, zh, en, material, scan, develop, polarity, summary_zh, summary_en in pairings:
    scanner = scanners[scan] | dict(invert_transmission=polarity=='negative')
    film = json.loads((root/f'film_foundry/presets/film/{material}.json').read_text(encoding='utf-8'))['film']
    recipe = dict(film=film, develop=develop, scanner=scanner)
    path = f'film_foundry/presets/creative_{key}.json'
    (root/path).write_text(json.dumps(recipe, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    phone.append(dict(id=key,label_zh=zh,label_en=en,material_id=material,source=path,
                      summary_zh=summary_zh,summary_en=summary_en,
                      recipe=dict(film=materials[material],develop=develop,scanner=scanner)))
(root/'android/app/src/main/assets/phone_creative_pairings.json').write_text(
    json.dumps(dict(model='phone_creative_pairings_v1',entries=phone),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(f'Generated {len(styles)} scanner styles and {len(pairings)} explicit pairings')
