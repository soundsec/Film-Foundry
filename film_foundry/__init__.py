"""Film Foundry runtime package.

``half_frame_darkroom`` is a compatibility alias for the same module objects.
Written sidecar/PNG/provenance identity stays on ``PROJECT_NAME``.
"""

from film_foundry._compat import install_half_frame_darkroom_alias

install_half_frame_darkroom_alias()

from film_foundry.identity import APP_DISPLAY_NAME, PROJECT_NAME

__app_name__ = PROJECT_NAME
__display_name__ = APP_DISPLAY_NAME
__version__ = "0.3.1"
