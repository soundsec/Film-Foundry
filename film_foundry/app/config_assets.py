"""Read existing portable recipe/profile values for any frontend."""

import json
import copy
from pathlib import Path

from film_foundry.contracts.spectral_exposure import SpectralExposureProfile
from film_foundry.contracts.spectral_optics import SpectralMaterialProfile, SpectralObservationProfile
from film_foundry.model.config import DarkroomConfig, normalize_scanner_color_curve_rgb, normalize_scanner_tone_curve
from film_foundry.app.workflow import config_from_share_code
from film_foundry.app.paths import preset_root
from film_foundry.app.preset_catalog import REFERENCE_TONE_PRESETS

PROFILE_TYPES = {
    "spectral_exposure_profile": ("film", SpectralExposureProfile),
    "spectral_material_profile": ("film", SpectralMaterialProfile),
    "spectral_observation_profile": ("scanner", SpectralObservationProfile),
}


def apply_recipe_bank(current, edited, bank):
    """Compose the same draft for applying an editor and for previewing it."""
    from film_foundry.model.config import DEVELOP_LOOK_FIELDS, SCAN_LOOK_FIELDS
    config = copy.deepcopy(current)
    if bank == 'film':
        config.film = copy.deepcopy(edited.film)
        config.mode, config.medium = edited.mode, edited.medium
    elif bank == 'develop':
        config.chemistry = copy.deepcopy(edited.chemistry)
        config.mode, config.medium = edited.mode, edited.medium
        config.process_control_mode = edited.process_control_mode
        for key in ('enable_grain','enable_halation','enable_mtf'):
            setattr(config,key,getattr(edited,key))
        for key in DEVELOP_LOOK_FIELDS:setattr(config.look, key, copy.deepcopy(getattr(edited.look,key)))
    elif bank in {'scanner','positive_scanner'}:
        config.scanner = copy.deepcopy(edited.scanner)
        config.enable_subtractive = edited.enable_subtractive
        for key in SCAN_LOOK_FIELDS:setattr(config.look, key, getattr(edited.look,key))
    else:raise ValueError(f'Unknown recipe bank: {bank}')
    return config


def _json(text):
    def reject(value):
        raise ValueError(f"Non-finite JSON value: {value}")
    return json.loads(text, parse_constant=reject)


def load_profile_file(path: Path, key: str):
    section, value_type = PROFILE_TYPES[key]
    value = _json(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(value, dict) and isinstance(value.get(section), dict):
        value = value[section].get(key)
    elif isinstance(value, dict) and key in value:
        value = value[key]
    return value_type.from_value(value)


def load_recipe_file(path: Path):
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    if text.startswith("FFC2:"):
        return config_from_share_code(text)
    value = _json(text)
    if not isinstance(value, dict) or not any(key in value for key in ("film", "develop", "chemistry", "scanner")):
        raise ValueError("Recipe JSON must contain film, develop/chemistry or scanner parameters")
    return DarkroomConfig.from_dict(value)


def load_curve_file(path: Path, key: str):
    if key not in {"tone_curve", "color_curve_rgb", "device_response_rgb"}:
        raise ValueError(f"Unknown scanner curve: {key}")
    value = _json(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(value, dict):
        value = value.get("scanner", value)
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"Curve JSON has no scanner.{key}")
        value = value[key]
    normalize = normalize_scanner_tone_curve if key == "tone_curve" else normalize_scanner_color_curve_rgb
    return normalize(value, field_name=f"scanner.{key}")


def load_reference_tone(key: str):
    """Read only the measured output tone; do not replace a scanner recipe.

    Explicit built-in source, so a same-named user scanner cannot silently
    change the meaning or provenance of the photo-reference tone buttons.
    """
    if key not in REFERENCE_TONE_PRESETS:
        raise ValueError(f"Unknown reference tone: {key}")
    return load_curve_file(preset_root() / "scanner" / f"{key}.json", "tone_curve")
