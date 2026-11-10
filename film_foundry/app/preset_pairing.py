"""Recommended observation companions, independent of any frontend."""
from film_foundry.app.seasonal_collection import matching_scanner

PAIRS={
    'warm_consumer_negative':'warm_consumer_scan', 'blue_highlight_negative':'violet_night_scan',
    'red_rich_negative':'red_cobalt_scan', 'cyan_shadow_warm_highlight_negative':'amber_cyan_scan',
    'deep_shadow_negative':'dense_shadow_scan', 'high_contrast_push':'dense_shadow_scan',
    'vivid_balanced_negative':'rich_color_scan', 'clear_modern_negative':'neutral_scan',
    'clean_digital_like':'clean_digital_scan', 'color_reversal_amber':'positive_transparency_warm',
    'color_reversal_soft':'positive_transparency_bright',
}

def recommended_scanner(material_key,config):
    from film_foundry.core.media import developed_medium_contract
    from film_foundry.app.workflow import material_polarity_category,material_color_category
    polarity=developed_medium_contract(config)['image_polarity']
    monochrome=material_color_category(config)=='monochrome'
    if polarity!=material_polarity_category(config):
        return 'positive_transparency_scan' if polarity=='positive' else 'bw_neutral_scan' if monochrome else 'neutral_scan'
    companion=matching_scanner(material_key)
    if companion:return companion
    if material_key in PAIRS:return PAIRS[material_key]
    if polarity=='positive':return 'positive_transparency_scan'
    if monochrome:return 'bw_neutral_scan'
    return 'neutral_scan'
