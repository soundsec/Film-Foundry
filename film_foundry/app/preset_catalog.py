"""Daily preset selection, independent of numerical models and old-key lookup.

This is a presentation catalog, not a migration or an alias table. Variants
remain separate recipes: loading an old key must retain its original values.
"""

CORE_FILM_PRESETS = frozenset({
    "clear_modern_negative",
    "warm_consumer_negative",
    "vivid_balanced_negative",
    "cyan_shadow_warm_highlight_negative",
    "monochrome_push",
    "color_reversal_transparency",
    "monochrome_reversal_transparency",
    "color_reversal_vivid",
    "color_reversal_amber",
    "color_reversal_cobalt",
    "monochrome_fine_negative",
    "monochrome_reversal_graphic",
})

CORE_SCANNER_PRESETS = frozenset({
    "neutral_scan",
    "bw_neutral_scan",
    "warm_consumer_scan",
    "color_restored_scan",
    "positive_transparency_scan",
    "chrome_saturated_scan",
    "amber_cyan_scan",
    "violet_night_scan",
    "acid_green_scan",
    "red_cobalt_scan",
})

# Four standard programs and one explicitly nonstandard negative-to-positive
# process. Other useful baths are opt-in variants, not deleted recipes.
CORE_DEVELOP_PRESETS = frozenset({
    "standard_color_negative", "standard_bw_negative",
    "standard_color_reversal", "standard_bw_reversal",
    "experimental_color_negative_clear_reversal",
})

# These are tone choices on any current scan, not additional scanner identities.
# The original scanner JSONs remain the single source for their measured points
# and remain directly loadable for historical sessions, CLI and sharing.
REFERENCE_TONE_PRESETS = (
    "reference_soft_tone_scan",
    "reference_deep_tone_scan",
)
