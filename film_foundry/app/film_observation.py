"""Headless observation of frozen film, with full-size exports of a fixed view."""
from dataclasses import dataclass,asdict
from pathlib import Path
import json
import math
import numpy as np
from film_foundry.core.material_family import ViewingConditions


@dataclass(frozen=True)
class FilmViewingSettings:
    mode:str='transmission'
    ev:float=0.
    temperature:float=5500.
    light_angle:float=30.
    view_angle:float=0.
    background:float=.02

    def __post_init__(self):
        if self.mode not in ('transmission','reflection'):raise ValueError('Unknown film observation mode')
        for key,low,high in (('ev',-20,20),('temperature',2000,12000),('light_angle',-80,80),('view_angle',-80,80),('background',0,1)):
            value=getattr(self,key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
                raise ValueError(f'Invalid viewing {key}')


def load_observable_medium(path,*,preview_long_edge=None):
    from film_foundry.core.negative_io import load_developed_medium_for_scan
    from film_foundry.app.preview_inputs import preview_medium
    medium=load_developed_medium_for_scan(path,include_process_surface=True)
    return preview_medium(medium,preview_long_edge) if preview_long_edge else medium


def observe(medium,settings=FilmViewingSettings()):
    from film_foundry.model.config import DarkroomConfig
    config=DarkroomConfig()
    if settings.mode=='reflection':
        from film_foundry.core.film_surface_optics import observe_surface
        from film_foundry.core.color import linear_to_srgb
        from film_foundry.core.scanner import color_temperature_rgb
        def direction(angle):
            angle=math.radians(angle)
            return (math.sin(angle),0.,math.cos(angle))
        view=ViewingConditions(illuminant_rgb=tuple(color_temperature_rgb(settings.temperature)),exposure_ev=settings.ev,
            light_direction=direction(settings.light_angle),view_direction=direction(settings.view_angle),background_rgb=(settings.background,)*3)
        return linear_to_srgb(observe_surface(medium,view),consume_input=True)
    from film_foundry.core.scan_runtime import scan_medium_output
    config.scanner.interpretation_mode='direct'
    config.scanner.remove_base_mask=False;config.scanner.invert_transmission=False
    config.scanner.scan_normalize=False;config.scanner.positive_scan_color_control_strength=0.
    config.scanner.print_gamma=1.
    config.scanner.transmission_light_ev=settings.ev
    config.scanner.transmission_light_temperature_k=settings.temperature
    return scan_medium_output(medium,config,'direct').output_srgb


def export_observation(source,target,settings=FilmViewingSettings()):
    """Use the original medium, never the reduced viewing cache."""
    from PIL import Image,PngImagePlugin
    from film_foundry.core.atomic_io import atomic_output_path
    target=Path(target);source=Path(source)
    if target.suffix.lower()!='.png' or source.resolve()==target.resolve():raise ValueError('Choose a separate PNG observation output')
    medium=load_observable_medium(source);image=observe(medium,settings)
    record={'model':'film_observation_export_v1','source_name':source.name,'view':asdict(settings),
        'encoding':'display_srgb_8bit','resolution':'original_medium','width':image.shape[1],'height':image.shape[0],
        'surface_observation':medium.metadata.get('film_process_model',{}).get('process_surface',{}).get('observation')}
    info=PngImagePlugin.PngInfo();info.add_text('FilmFoundryObservation',json.dumps(record,allow_nan=False))
    with atomic_output_path(target) as temporary:
        Image.fromarray(np.uint8(np.rint(np.clip(image,0,1)*255))).save(temporary,format='PNG',pnginfo=info)
    return target
