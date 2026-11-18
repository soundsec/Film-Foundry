"""Local display preferences, separate from recipes, sessions and media."""
from __future__ import annotations

from functools import lru_cache
import copy
import json
import os
from pathlib import Path
import tempfile

from film_foundry.app.paths import app_root
from film_foundry.ui.color_schemes import THEME_KEYS, normalize_custom


def preferences_path() -> Path:
    override = os.environ.get("FILM_FOUNDRY_UI_SETTINGS")
    return Path(override) if override else app_root() / "user_presets" / "desktop_ui.json"


@lru_cache(maxsize=8)
def _read(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            return "zh_CN", "dark", None, None, None, None, None, None, None, None, None, False
    except (OSError, ValueError):
        return "zh_CN", "dark", None, None, None, None, None, None, None, None, None, False
    language = value.get("language", "zh_CN")
    theme = value.get("theme", "dark")
    return (language if isinstance(language,str) else "zh_CN",
            theme if theme in THEME_KEYS else "dark",
            value.get("auto_preview") if isinstance(value.get("auto_preview"), bool) else None,
            value.get("show_preset_variants") if isinstance(value.get("show_preset_variants"), bool) else None,
            value.get("workspace_layout") if value.get("workspace_layout") in ("compact", "studio") else None,
            value.get("histogram") if isinstance(value.get("histogram"), dict) else None,
            value.get("scan_advanced") if isinstance(value.get("scan_advanced"), bool) else None,
            value.get("shortcuts") if isinstance(value.get("shortcuts"), dict) else None,
            value.get("preview_options") if isinstance(value.get("preview_options"), dict) else None,
            value.get('parameter_pins') if isinstance(value.get('parameter_pins'), dict) else None,
            normalize_custom(value.get('custom_colors')), value.get('tour_seen') is True)


def load_preferences(path: Path | None = None) -> dict[str, object]:
    language, theme, auto_preview, show_preset_variants, workspace_layout, histogram, scan_advanced, shortcuts, preview_options, parameter_pins, custom_colors, tour_seen = _read(path or preferences_path())
    value = {"language": language, "theme": theme}
    if tour_seen:value["tour_seen"]=True
    if auto_preview is not None:
        value["auto_preview"] = auto_preview
    if show_preset_variants is not None:
        value["show_preset_variants"] = show_preset_variants
    if workspace_layout is not None:
        value["workspace_layout"] = workspace_layout
    if histogram is not None:
        value["histogram"] = copy.deepcopy(histogram)
    if scan_advanced is not None:
        value['scan_advanced'] = scan_advanced
    if shortcuts is not None:
        value['shortcuts'] = copy.deepcopy(shortcuts)
    if custom_colors is not None:
        value["custom_colors"] = dict(custom_colors)
    if parameter_pins is not None:
        value["parameter_pins"] = copy.deepcopy(parameter_pins)
    if preview_options is not None:
        value["preview_options"] = copy.deepcopy(preview_options)
    return value


def save_preferences(*, language: str | None = None, theme: str | None = None, auto_preview: bool | None = None, show_preset_variants: bool | None = None, workspace_layout: str | None = None, histogram: dict | None = None, scan_advanced: bool | None = None, shortcuts: dict | None = None, preview_options: dict | None = None, parameter_pins: dict | None = None, custom_colors: dict | None = None, tour_seen: bool | None = None, path: Path | None = None) -> None:
    """Atomic small preference write; no image/config access or directory scan."""
    if language is not None:
        from film_foundry.ui.i18n import available_languages
        if language not in available_languages():raise ValueError("Unsupported UI language")
    if theme is not None and theme not in THEME_KEYS:
        raise ValueError("Unsupported UI theme")
    if custom_colors is not None and normalize_custom(custom_colors) is None:
        raise ValueError("Expected two RGB colours in #RRGGBB format")
    if auto_preview is not None and not isinstance(auto_preview, bool):
        raise ValueError("auto_preview must be a boolean")
    if show_preset_variants is not None and not isinstance(show_preset_variants, bool):
        raise ValueError("show_preset_variants must be a boolean")
    if workspace_layout is not None and workspace_layout not in ("compact", "studio"):
        raise ValueError("Unsupported workspace layout")
    if scan_advanced is not None and not isinstance(scan_advanced, bool):
        raise ValueError('scan_advanced must be a boolean')
    path = path or preferences_path()
    value = load_preferences(path)
    if tour_seen is not None:value["tour_seen"]=bool(tour_seen)
    if custom_colors is not None:
        value["custom_colors"] = normalize_custom(custom_colors)
    if parameter_pins is not None:
        value["parameter_pins"] = {**value.get("parameter_pins", {}), **parameter_pins}
    if preview_options is not None:
        value["preview_options"] = dict(value.get("preview_options", {}), **preview_options)
    if language is not None:
        value["language"] = language
    if theme is not None:
        value["theme"] = theme
    if auto_preview is not None:
        value["auto_preview"] = auto_preview
    if show_preset_variants is not None:
        value["show_preset_variants"] = show_preset_variants
    if workspace_layout is not None:
        value["workspace_layout"] = workspace_layout
    if scan_advanced is not None:
        value['scan_advanced'] = scan_advanced
    if histogram is not None:
        if not isinstance(histogram, dict):raise ValueError('histogram must be a dictionary')
        value['histogram'] = copy.deepcopy(histogram)
    if shortcuts is not None:
        if not isinstance(shortcuts, dict):raise ValueError('shortcuts must be a dictionary')
        value['shortcuts'] = copy.deepcopy(shortcuts)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".desktop_ui_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
        _read.cache_clear()
