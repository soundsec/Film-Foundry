"""Local display preferences, separate from recipes, sessions and media."""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import tempfile

from film_foundry.app.paths import app_root


def preferences_path() -> Path:
    override = os.environ.get("FILM_FOUNDRY_UI_SETTINGS")
    return Path(override) if override else app_root() / "user_presets" / "desktop_ui.json"


@lru_cache(maxsize=8)
def _read(path: Path) -> tuple[str, str, bool | None, bool | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            return "zh_CN", "dark", None, None, None
    except (OSError, ValueError):
        return "zh_CN", "dark", None, None, None
    language = value.get("language", "zh_CN")
    theme = value.get("theme", "dark")
    return (language if language in ("zh_CN", "en_US") else "zh_CN",
            theme if theme in ("dark", "ivory") else "dark",
            value.get("auto_preview") if isinstance(value.get("auto_preview"), bool) else None,
            value.get("show_preset_variants") if isinstance(value.get("show_preset_variants"), bool) else None,
            value.get("workspace_layout") if value.get("workspace_layout") in ("compact", "studio") else None)


def load_preferences() -> dict[str, str | bool]:
    language, theme, auto_preview, show_preset_variants, workspace_layout = _read(preferences_path())
    value = {"language": language, "theme": theme}
    if auto_preview is not None:
        value["auto_preview"] = auto_preview
    if show_preset_variants is not None:
        value["show_preset_variants"] = show_preset_variants
    if workspace_layout is not None:
        value["workspace_layout"] = workspace_layout
    return value


def save_preferences(*, language: str | None = None, theme: str | None = None, auto_preview: bool | None = None, show_preset_variants: bool | None = None, workspace_layout: str | None = None) -> None:
    """Atomic small preference write; no image/config access or directory scan."""
    if language is not None and language not in ("zh_CN", "en_US"):
        raise ValueError("Unsupported UI language")
    if theme is not None and theme not in ("dark", "ivory"):
        raise ValueError("Unsupported UI theme")
    if auto_preview is not None and not isinstance(auto_preview, bool):
        raise ValueError("auto_preview must be a boolean")
    if show_preset_variants is not None and not isinstance(show_preset_variants, bool):
        raise ValueError("show_preset_variants must be a boolean")
    if workspace_layout is not None and workspace_layout not in ("compact", "studio"):
        raise ValueError("Unsupported workspace layout")
    value = load_preferences()
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
    path = preferences_path()
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
