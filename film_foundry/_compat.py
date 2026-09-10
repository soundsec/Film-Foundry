"""Map ``half_frame_darkroom.*`` import names onto ``film_foundry.*`` modules.

The finder aliases names onto already-loaded ``film_foundry`` modules and does
not give PathFinder a search path under the legacy name, so the same files are
never executed twice.
"""

from __future__ import annotations

import sys
from importlib import import_module
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec

_PREFIX = "half_frame_darkroom"
_TARGET = "film_foundry"
_FINDER_MARK = "_film_foundry_legacy_alias_finder"


class _AliasLoader(Loader):
    def __init__(self, module):
        self._module = module
        self._canonical_name = module.__name__
        self._canonical_spec = getattr(module, "__spec__", None)
        self._canonical_loader = getattr(module, "__loader__", None)

    def create_module(self, spec):
        return self._module

    def get_code(self, fullname):
        # runpy executes the canonical source as __main__; ordinary imports
        # still share the canonical module object through create_module.
        return self._canonical_loader.get_code(self._canonical_name)

    def exec_module(self, module):
        # importlib may copy alias spec attributes onto the shared object.
        module.__name__ = self._canonical_name
        if self._canonical_spec is not None:
            module.__spec__ = self._canonical_spec
        if self._canonical_loader is not None:
            module.__loader__ = self._canonical_loader
        sys.modules[self._canonical_name] = module
        sys.modules[_PREFIX + self._canonical_name[len(_TARGET) :]] = module


class _AliasFinder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != _PREFIX and not fullname.startswith(_PREFIX + "."):
            return None
        canonical = _TARGET + fullname[len(_PREFIX) :]
        if canonical not in sys.modules:
            import_module(canonical)
        mirror_loaded_modules()
        module = sys.modules[canonical]
        # Let importlib use the alias loader, including for python -m. A
        # pre-populated alias would instead expose the canonical source loader.
        sys.modules.pop(fullname, None)
        spec = ModuleSpec(
            fullname,
            _AliasLoader(module),
            is_package=False,
        )
        spec.origin = None
        spec.submodule_search_locations = None
        return spec


def mirror_loaded_modules() -> None:
    """Alias every already-imported canonical module onto the legacy name."""
    if _TARGET in sys.modules:
        sys.modules[_PREFIX] = sys.modules[_TARGET]
    for name, module in list(sys.modules.items()):
        if name == _TARGET or name.startswith(_TARGET + "."):
            sys.modules[_PREFIX + name[len(_TARGET) :]] = module


def install_half_frame_darkroom_alias() -> None:
    if any(getattr(item, _FINDER_MARK, False) for item in sys.meta_path):
        mirror_loaded_modules()
        return
    finder = _AliasFinder()
    setattr(finder, _FINDER_MARK, True)
    sys.meta_path.insert(0, finder)
    mirror_loaded_modules()
