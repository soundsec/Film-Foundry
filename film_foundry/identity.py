"""Product display name versus written disk/metadata identity.

The import package is ``film_foundry``. Sidecar, PNG Software, provenance,
and session ``project`` fields keep writing ``PROJECT_NAME`` so existing files
stay readable. ``KNOWN_PROJECT_NAMES`` still recognizes older written names.
User-facing CLI, GUI, and README copy should prefer ``APP_DISPLAY_NAME``.
"""

from __future__ import annotations

APP_DISPLAY_NAME = "Film Foundry"
AUTHOR_NAME = "soundsec"
REPOSITORY_URL = "https://github.com/soundsec/Film-Foundry"
LEGACY_APP_NAME = "Electronic Negative Factory"
PROJECT_NAME = "Film Foundry / Electronic Negative Factory"
KNOWN_PROJECT_NAMES = (
    PROJECT_NAME,
    APP_DISPLAY_NAME,
    LEGACY_APP_NAME,
    "Half-Frame Darkroom",
)
