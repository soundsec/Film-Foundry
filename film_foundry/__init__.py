"""Film Foundry runtime package.

Written sidecar, PNG, provenance and session identity stays on ``PROJECT_NAME``.
"""

from film_foundry.identity import APP_DISPLAY_NAME, PROJECT_NAME

__app_name__ = PROJECT_NAME
__display_name__ = APP_DISPLAY_NAME
__version__ = "0.4.0"
