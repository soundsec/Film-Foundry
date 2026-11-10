"""Experimental family-neutral envelopes. No film pools or optical-density assumptions."""
from dataclasses import dataclass
from copy import copy
from functools import wraps
from types import MappingProxyType
from typing import Mapping, Protocol, Any
import json
import numpy as np
from film_foundry.core.material_arrays import freeze_array


def cached_body_validation(validate):
    """A successful check lives with its immutable body, without a global cache."""
    @wraps(validate)
    def checked(body):
        if not isinstance(body,MediumBody):return validate(body)
        previous=getattr(body,'_validation_results',())
        for function,result in previous:
            if function is validate:return result
        result=validate(body)
        object.__setattr__(body,'_validation_results',(*previous,(validate,result)))
        return result
    return checked


def frozen_fields(fields: Mapping[str, np.ndarray]):
    result = {}
    shape = None
    for key, value in fields.items():
        array = freeze_array(value)
        if array.ndim not in (2, 3) or 0 in array.shape or not np.isfinite(array).all():
            raise ValueError(f"Invalid material field: {key}")
        if shape is not None and array.shape[:2] != shape:
            raise ValueError("Material fields must share spatial geometry")
        shape = array.shape[:2]
        result[key] = array
    if not result:raise ValueError("Material fields are required")
    return MappingProxyType(result)


@dataclass(frozen=True)
class MediumBody:
    family: str
    schema_version: int
    group_id: str
    body_id: str
    role: str
    observation_model: str
    fields: Mapping[str, np.ndarray]
    usable_negative: bool = False
    presentation_json: str = "{}"

    def __post_init__(self):
        if not self.group_id or not self.body_id or self.schema_version < 1:
            raise ValueError("Versioned medium identity required")
        object.__setattr__(self, "fields", frozen_fields(self.fields))
        presentation=json.loads(self.presentation_json)
        if not isinstance(presentation,dict) or presentation.get("frame","none") not in ("none","equal","pod"):
            raise ValueError("Invalid material presentation")

    def observation_rows(self,start,stop):
        """Read-only rows for observers whose contracts are local to each pixel.

        Only the explicitly listed pointwise observers use this path. They may
        reuse successful body checks: a row subset preserves their invariants.
        Dataclass replacement/loading instead constructs and checks a new body.
        """
        height=next(iter(self.fields.values())).shape[0]
        if not 0<=start<stop<=height:raise ValueError('Invalid observation rows')
        result=copy(self)
        object.__setattr__(result,'fields',MappingProxyType({key:value[start:stop] for key,value in self.fields.items()}))
        return result


@dataclass(frozen=True)
class ProductGroup:
    group_id: str
    bodies: tuple[MediumBody, ...]
    provenance_json: str

    def __post_init__(self):
        object.__setattr__(self, "bodies", tuple(self.bodies))
        if not isinstance(json.loads(self.provenance_json),dict):raise ValueError("Provenance must be an object")
        if not self.bodies or any(b.group_id != self.group_id for b in self.bodies):
            raise ValueError("All products must belong to the same process group")
        if len({b.body_id for b in self.bodies}) != len(self.bodies):
            raise ValueError("Duplicate body identity")


@dataclass(frozen=True)
class ViewingConditions:
    illuminant_rgb: tuple[float, float, float] = (1., 1., 1.)
    exposure_ev: float = 0.
    # Reserved for directional surface observers; paper observes diffuse irradiance.
    light_direction: tuple[float, float, float] = (0., 0., 1.)
    view_direction: tuple[float, float, float] = (0., 0., 1.)
    background_rgb: tuple[float, float, float] = (.02, .02, .02)
    include_border: bool = False
    border_style: str = "material"


def observation_border(body,shape,view):
    """Top, bottom, left and right observation margins, shared by creative layers."""
    if view.border_style not in ("material","none","equal","pod_short"):raise ValueError("Unknown viewing border style")
    style=json.loads(body.presentation_json).get("frame","none") if view.border_style=="material" else view.border_style
    if not view.include_border or style=="none":return (0,0,0,0)
    h,w=shape[:2];edge=max(1,round(min(h,w)*.055))
    bottom=max(edge,round(min(h,w)*.20)) if style=="pod" else edge
    right=edge
    if style=="pod_short":
        if w>h:right=max(edge,round(h*.20))
        else:bottom=max(edge,round(w*.20))
    return edge,bottom,edge,right


def frame_observation(body,image,view):
    top,bottom,left,right=observation_border(body,image.shape,view)
    if not any((top,bottom,left,right)):return image
    h,w=image.shape[:2]
    color=np.asarray(view.illuminant_rgb,dtype=np.float32)*.94*(2**view.exposure_ev)
    if not np.isfinite(color).all() or (color<0).any():raise ValueError('Invalid border radiance')
    canvas=np.empty((h+top+bottom,w+left+right,3),np.float32);canvas[:]=color
    canvas[top:top+h,left:left+w]=image
    return canvas


class MaterialFamily(Protocol):
    """Families own state types and operation vocabularies; frontends own scheduling."""
    def expose(self, image: np.ndarray, material: Any, *, group_id: str, seed: int) -> Any: ...
    def process(self, state: Any, operation: str, **parameters) -> Any: ...
    def finish(self, state: Any) -> ProductGroup: ...
    def observe(self, body: MediumBody, view: ViewingConditions) -> np.ndarray: ...
