"""Daily preset selection, independent of numerical models and old-key lookup.

This is a presentation catalog, not a migration or an alias table. Variants
remain separate recipes: loading an old key must retain its original values.
"""
from pathlib import Path

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


def is_internal_preset_name(name: str) -> bool:
    return name.lower().startswith(("diagnostic_", "accident_", "experimental_"))


def is_listed_builtin(kind: str, key: str) -> bool:
    if kind == "develop" and key in CORE_DEVELOP_PRESETS:
        return True
    # high_contrast_push represents this pair in the extended picker.
    # Both original grain structures remain addressable by key.
    if kind == "film" and key == "deep_shadow_negative":
        return False
    return not is_internal_preset_name(key)


def public_builtin_preset_names(directory: Path, kind: str | None = None) -> set[str]:
    return {path.stem for path in directory.glob("*.json")
            if is_listed_builtin(kind or directory.name, path.stem)}
