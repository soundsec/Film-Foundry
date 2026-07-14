"""Small shared UI helpers for the standalone preset editors."""

from __future__ import annotations

from pathlib import Path
import tkinter as tk

from half_frame_darkroom.ui.i18n import current_language, tr


def ui(zh: str, en: str) -> str:
    return en if current_language() == "en_US" else zh


def localize_widget_tree(widget: tk.Misc, english: dict[str, str]) -> None:
    """Translate static widget text after a Tk editor has built its controls."""
    if current_language() != "en_US":
        return
    try:
        value = str(widget.cget("text"))
    except (AttributeError, tk.TclError):
        value = ""
    if value in english:
        try:
            widget.configure(text=english[value])
        except tk.TclError:
            pass
    for child in widget.winfo_children():
        localize_widget_tree(child, english)


def localized_preset_name(
    kind: str,
    key: str,
    fallback: str,
    *,
    selected_path: Path,
    builtin_dir: Path,
) -> str:
    """Use translated built-in labels while preserving user supplied names."""
    try:
        is_builtin = selected_path.resolve().parent == builtin_dir.resolve()
    except OSError:
        is_builtin = selected_path.parent == builtin_dir
    translation_key = f"preset.{kind}.{Path(key).stem}"
    translated = tr(translation_key)
    if is_builtin and translated != translation_key:
        return translated
    return fallback


def unique_choice_map(items: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    """Build stable display labels without losing the underlying preset key."""
    labels: list[str] = []
    mapping: dict[str, str] = {}
    for key, requested_label in items:
        label = requested_label
        if label in mapping and mapping[label] != key:
            label = f"{requested_label} [{key}]"
        labels.append(label)
        mapping[label] = key
    return labels, mapping
