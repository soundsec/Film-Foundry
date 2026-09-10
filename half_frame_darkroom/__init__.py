"""Legacy import path. Implementation lives in ``film_foundry``.

Importing ``half_frame_darkroom`` or any of its submodules returns the same
module objects as ``film_foundry``.
"""

from film_foundry._compat import install_half_frame_darkroom_alias, mirror_loaded_modules
import film_foundry
import sys

install_half_frame_darkroom_alias()
sys.modules[__name__] = film_foundry
mirror_loaded_modules()
