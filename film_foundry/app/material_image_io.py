"""Shared display-image boundaries for independent material workspaces.

Source dimensions describe the input, working dimensions the actual formation.
Export never enlarges an existing medium or treats a diagnostic map as an image.
"""
from pathlib import Path
import json
from PIL import Image, PngImagePlugin
import numpy as np
from film_foundry.core.io_utils import load_image_with_color,save_image_bundle
from film_foundry.core.color import srgb_to_linear
from film_foundry.core.source_image import load_formation_image
from film_foundry.core.preview import resize_to_long_edge
from film_foundry.core.atomic_io import atomic_output_path,atomic_path_set,atomic_write_json
from film_foundry.model.config import OutputConfig


def load_exposure_image(path,long_edge=1600,*,input_icc_policy='apply'):
    if type(long_edge) is not int or long_edge<0:raise ValueError('Working edge must be a nonnegative integer')
    display,color=load_formation_image(path,input_icc_policy=input_icc_policy)
    source_size=list(display.shape[1::-1])
    image=resize_to_long_edge(display if color["encoding"] == "linear_rgb" else srgb_to_linear(display),long_edge or None)
    record={'model':'material_source_input_v1','kind':'image','name':Path(path).name,
            'source_size':source_size,'working_size':list(image.shape[1::-1]),'color':color,'formation_long_edge':long_edge,
            'resampling':'none' if source_size==list(image.shape[1::-1]) else 'linear_rgb_area'}
    return image,record


def save_observation(image,path,metadata,*,bit_depth=8,quality=100,protected_paths=()):
    target=Path(path)
    if type(bit_depth) is not int or bit_depth not in (8,16):raise ValueError('Observation depth must be 8 or 16')
    if target.suffix.lower() not in ('.jpg','.jpeg','.png','.tif','.tiff'):raise ValueError('Material observation requires JPEG, PNG or TIFF')
    jpeg=target.suffix.lower() in ('.jpg','.jpeg')
    if jpeg and bit_depth!=8:raise ValueError('JPEG observation requires 8-bit output')
    if type(quality) is not int or not 1<=quality<=100:raise ValueError('JPEG quality must be 1–100')
    for source in protected_paths:
        if source and Path(source).resolve() in (target.resolve(),target.with_suffix(target.suffix+'.json').resolve()):
            raise ValueError('Observation output must not overwrite its source')
    if image.ndim!=3 or image.shape[-1]!=3 or not image.size or not np.isfinite(image).all():raise ValueError('Observation requires finite HxWx3 RGB')
    record={**metadata,'encoding':f'display_srgb_{bit_depth}bit','output_size':list(image.shape[1::-1])}
    if jpeg:record.update(compression='jpeg_lossy',quality=quality)
    if bit_depth==8 and target.suffix.lower()=='.png':
        # Preserve the existing PNG quantization and embedded observation record.
        info=PngImagePlugin.PngInfo();info.add_text('FilmFoundryObservation',json.dumps(record,allow_nan=False))
        sidecar=target.with_suffix(target.suffix+'.json')
        with atomic_path_set((target,sidecar)):
            with atomic_output_path(target) as temporary:
                Image.fromarray((np.clip(image,0,1)*255+.5).astype(np.uint8)).save(temporary,format='PNG',pnginfo=info)
            atomic_write_json(sidecar,{'FilmFoundryObservation':record})
    else:
        # The existing writer preserves RGB16. Its sidecar is committed/rolled
        # back together with the image; no 8-bit PIL roundtrip or new encoder.
        output=OutputConfig(bit_depth=bit_depth,quality=quality,anti_banding_strength=0.,watermark_metadata=False)
        save_image_bundle(image,target,output,{'FilmFoundryObservation':record},protected_paths=protected_paths)
    return target
