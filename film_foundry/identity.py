"""Product display name versus written disk/metadata identity.

The canonical import package is ``film_foundry``. ``half_frame_darkroom``
remains a compatibility alias for the same modules. Sidecar, PNG Software,
provenance, and session ``project`` fields keep writing ``PROJECT_NAME`` so
existing files and tests stay compatible. User-facing CLI, GUI, and README
copy should prefer ``APP_DISPLAY_NAME``.
"""

from __future__ import annotations

APP_DISPLAY_NAME = "Film Foundry"
LEGACY_APP_NAME = "Electronic Negative Factory"
PROJECT_NAME = "Film Foundry / Electronic Negative Factory"
KNOWN_PROJECT_NAMES = (
    PROJECT_NAME,
    APP_DISPLAY_NAME,
    LEGACY_APP_NAME,
    "Half-Frame Darkroom",
)
