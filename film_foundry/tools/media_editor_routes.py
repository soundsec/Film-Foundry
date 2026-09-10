"""Frontend editor module routes keyed by media pipeline.

These strings are GUI/launcher routing only. Numerical core registries
must not import this module or store editor paths.
"""

from __future__ import annotations

NEGATIVE_EDITOR_ROUTES: dict[str, str] = {
    "material": "film_foundry.tools.run_film_material_editor",
    "develop": "film_foundry.tools.run_develop_process_editor",
    "scanner": "film_foundry.tools.run_scanner_render_editor",
}

POSITIVE_EDITOR_ROUTES: dict[str, str] = {
    "material": "film_foundry.tools.run_film_material_editor",
    "develop": "film_foundry.tools.run_develop_process_editor",
    "scanner": "film_foundry.tools.run_positive_scanner_editor",
}

_NEGATIVE_KEYS = ("negative", "color_negative", "bw_negative", "film_negative")
_POSITIVE_KEYS = ("slide", "reversal")

EDITOR_ROUTES: dict[str, dict[str, str]] = {
    **{key: dict(NEGATIVE_EDITOR_ROUTES) for key in _NEGATIVE_KEYS},
    **{key: dict(POSITIVE_EDITOR_ROUTES) for key in _POSITIVE_KEYS},
}


def editor_routes_for(process_key: str) -> dict[str, str]:
    key = str(process_key).strip().lower()
    routes = EDITOR_ROUTES.get(key)
    if routes is not None:
        return dict(routes)
    if key.endswith("_negative"):
        return dict(NEGATIVE_EDITOR_ROUTES)
    raise KeyError(f"No frontend editor routes for media process {process_key!r}.")
