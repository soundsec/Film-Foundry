"""Immutable film-parameter binding, using the existing FFC2 film object.

This is optional latent metadata, not another recipe or material-array schema.
Unspecified fields keep the receiving implementation's documented defaults;
they must never be filled from an unrelated current recipe.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import math
from numbers import Integral, Real
from types import MappingProxyType

from film_foundry.contracts.data_kernel import MATERIAL_SNAPSHOT_MODEL


def _freeze(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("material parameter keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real) and math.isfinite(value):
        return float(value)
    raise ValueError("material parameters must contain finite JSON values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class MaterialSnapshot:
    film: Mapping

    def __post_init__(self):
        if not isinstance(self.film, Mapping):
            raise ValueError("material snapshot film must be an object")
        film = _freeze(self.film)
        if len(json.dumps(_thaw(film), ensure_ascii=False).encode("utf-8")) > 262144:
            raise ValueError("material snapshot is too large")
        object.__setattr__(self, "film", film)

    def __deepcopy__(self, memo):
        return self

    def parameters(self):
        return _thaw(self.film)

    def as_dict(self):
        return {"model": MATERIAL_SNAPSHOT_MODEL, "film": self.parameters()}

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if (not isinstance(value, Mapping) or set(value) != {"model", "film"}
                or value.get("model") != MATERIAL_SNAPSHOT_MODEL):
            raise ValueError("unsupported material snapshot fields or model")
        return cls(value["film"])
