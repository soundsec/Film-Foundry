"""Compatibility alias for Film Foundry.

The implementation currently lives in ``half_frame_darkroom`` so existing
scripts and sidecars keep working while the public project name changes.
"""

from half_frame_darkroom import *  # noqa: F401,F403
from half_frame_darkroom import __app_name__
