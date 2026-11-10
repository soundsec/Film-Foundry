"""Experimental headless entry and atomic archives for independently versioned media.

These .ffmaterial.npz archives are NOT film negatives or legacy latent NPZs.
Existing film APIs and dispatch remain untouched until an explicit adapter exists.
"""
from film_foundry.core.material_structure import from_record
from dataclasses import asdict,replace
import json
import numpy as np
from film_foundry.core.material_family import MediumBody, ProductGroup, ViewingConditions,frame_observation
from film_foundry.core.peel_apart import FAMILY, PeelApartFamily, PeelMaterial, PeelState
from film_foundry.core import peel_apart, integral_instant, silver_plate
from film_foundry.core import photographic_paper, wet_plate, cyanotype, platinum_palladium
from film_foundry.core.material_parallel import run_rows,CHUNK_PIXELS,thread_count
from film_foundry.core.atomic_io import atomic_savez

_FAMILIES={
    peel_apart.FAMILY:(peel_apart,PeelApartFamily,PeelMaterial,PeelState),
    integral_instant.FAMILY:(integral_instant,integral_instant.IntegralInstantFamily,integral_instant.InstantMaterial,integral_instant.InstantState),
    silver_plate.FAMILY:(silver_plate,silver_plate.SilverPlateFamily,silver_plate.PlateMaterial,silver_plate.PlateState),
    photographic_paper.FAMILY:(photographic_paper,photographic_paper.PaperFamily,photographic_paper.PaperMaterial,photographic_paper.PaperState),
    wet_plate.FAMILY:(wet_plate,wet_plate.WetPlateFamily,wet_plate.WetPlateMaterial,wet_plate.WetPlateState),
    cyanotype.FAMILY:(cyanotype,cyanotype.CyanotypeFamily,cyanotype.CyanotypeMaterial,cyanotype.CyanotypeState),
    platinum_palladium.FAMILY:(platinum_palladium,platinum_palladium.PlatinumFamily,platinum_palladium.PlatinumMaterial,platinum_palladium.PlatinumState),
}


def family(name):
    if name not in _FAMILIES:raise ValueError(f"Material family not implemented: {name}")
    return _FAMILIES[name][1]()


def registered_families():return tuple(_FAMILIES)


def binding(name):
    """The registered runtime types; descriptors do not maintain a second registry."""
    if name not in _FAMILIES:raise ValueError(f'Material family not implemented: {name}')
    return _FAMILIES[name]


def state_family(value):
    return next((name for name,entry in _FAMILIES.items() if isinstance(value,entry[3])),None)


def material_definition(name, parameters=None):
    """Construct a family's own material schema for data-only frontends."""
    family(name)
    return _FAMILIES[name][2](**(parameters or {}))


# Explicitly opt in only the current pointwise models. Future spatial observers
# need their own halo/boundary contract before using independent row blocks.
_ROW_OBSERVERS={peel_apart.FAMILY,integral_instant.FAMILY,silver_plate.FAMILY,
    photographic_paper.FAMILY,wet_plate.FAMILY,cyanotype.FAMILY,platinum_palladium.FAMILY}
OBSERVATION_CHUNK_PIXELS=CHUNK_PIXELS


def _checked_observation(image):
    if image.ndim!=3 or image.shape[2]!=3 or not np.isfinite(image).all() or (image<0).any():
        raise ValueError("Material observer returned invalid linear RGB")
    return image


def observe(body, conditions=ViewingConditions(),*,chunk_pixels=OBSERVATION_CHUNK_PIXELS,workers=None):
    """Full-resolution linear RGB; bounded pointwise temporaries, no resampling."""
    if type(chunk_pixels) is not int or chunk_pixels<0:raise ValueError('Observation chunk size must be a nonnegative integer')
    observer=family(body.family).observe
    height,width=next(iter(body.fields.values())).shape[:2]
    if not chunk_pixels or body.family not in _ROW_OBSERVERS or not (body.schema_version in (1,2,3) or (body.family==peel_apart.FAMILY and body.schema_version in (4,5)) or (body.family in (integral_instant.FAMILY,wet_plate.FAMILY,silver_plate.FAMILY) and body.schema_version==4)) or height*width<=chunk_pixels:
        return _checked_observation(observer(body,conditions))
    _FAMILIES[body.family][0].validate_body(body)
    view=replace(conditions,include_border=False)
    image=np.empty((height,width,3),np.float32)
    def render(start,stop):
        image[start:stop]=_checked_observation(observer(body.observation_rows(start,stop),view))
    run_rows(height,width,render,chunk_pixels=chunk_pixels,workers=workers)
    # Silver plates have no paper border in their existing observer.
    return image if body.family==silver_plate.FAMILY else frame_observation(body,image,conditions)


def save(path, value, *, precision='auto'):
    """Atomically replace an explicit archive path. Single bodies retain group identity."""
    from film_foundry.app.material_precision import storage_fields,POLICIES
    if precision not in POLICIES:raise ValueError('Unsupported material storage precision')
    arrays={};storage_record={}
    kind=state_family(value)
    if kind is not None:
        meta=dict(kind="state",family=kind,version=value.schema_version,group_id=value.group_id,
            seed=value.seed,material=asdict(value.material),stage=value.stage,
            elapsed_seconds=value.elapsed_seconds,process_json=value.process_json)
        stored,converted=storage_fields(value.fields,kind,precision)
        arrays.update(stored);storage_record.update({key:'float16' for key in converted})
    elif isinstance(value,ProductGroup):
        meta=dict(kind="products",version=1,group_id=value.group_id,provenance_json=value.provenance_json,bodies=[])
        for i,body in enumerate(value.bodies):
            family(body.family)
            _FAMILIES[body.family][0].validate_body(body)
            descriptor={key:getattr(body,key) for key in ("family","schema_version","group_id","body_id","role","observation_model","usable_negative","presentation_json")}
            descriptor["fields"]={key:f"body_{i}_{key}" for key in body.fields}
            stored,converted=storage_fields(body.fields,body.family,precision)
            arrays.update({descriptor["fields"][key]:array for key,array in stored.items()})
            storage_record.update({descriptor['fields'][key]:'float16' for key in converted})
            meta["bodies"].append(descriptor)
    else:raise TypeError("Expected a registered material state or ProductGroup")
    if precision not in ('float32','full'):meta['storage_precision']={'policy':precision,'fields':storage_record,'arithmetic':'float32'}
    arrays["manifest"]=np.array(json.dumps(meta,sort_keys=True,allow_nan=False))
    atomic_savez(path,compression_level=1,parallel_compression=thread_count()>1,durable=True,**arrays)


def load(path):
    with np.load(path,allow_pickle=False) as archive:
        meta=json.loads(str(archive["manifest"].item()))
        if meta.get("version") not in (1,2,3,4,5):raise ValueError("Unsupported material archive version")
        if meta.get("kind")=="state":
            family(meta.get("family"))
            _,_,material_type,state_type=_FAMILIES[meta["family"]]
            process=meta['process_json']
            if meta.get('storage_precision'):
                process=json.dumps({**json.loads(process),'storage_precision':meta['storage_precision']},sort_keys=True)
            return state_type(meta["group_id"],meta["seed"],from_record(material_type,meta["material"]),meta["stage"],
                meta["elapsed_seconds"],{key:archive[key] for key in archive.files if key!="manifest"},process,schema_version=meta["version"])
        if meta["version"]!=1:raise ValueError("Unsupported product envelope version")
        if meta.get("kind")!="products":raise ValueError("Unsupported material archive kind")
        bodies=[]
        for descriptor in meta["bodies"]:
            spec=dict(descriptor);fields=spec.pop("fields")
            family(spec["family"])
            body=MediumBody(**spec,fields={key:archive[name] for key,name in fields.items()})
            # Validate the family's structural contract without evaluating an image.
            _FAMILIES[body.family][0].validate_body(body);bodies.append(body)
        provenance=meta['provenance_json']
        if meta.get('storage_precision'):
            provenance=json.dumps({**json.loads(provenance),'storage_precision':meta['storage_precision']},sort_keys=True)
        return ProductGroup(meta["group_id"],tuple(bodies),provenance)


def save_body(path, group, body_id, *, precision='auto'):
    selected=tuple(body for body in group.bodies if body.body_id==body_id)
    if len(selected)!=1:raise ValueError("Body does not belong to this product group")
    save(path,ProductGroup(group.group_id,selected,group.provenance_json),precision=precision)
