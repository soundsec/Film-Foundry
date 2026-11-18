"""Unframed preview and linear scanner samples for a single calibration dialog."""
from copy import deepcopy
from dataclasses import dataclass,replace

import numpy as np

from film_foundry.app.preview_session import PreviewSession
from film_foundry.app.workflow import observe_medium_file
from film_foundry.core.engine import scan_medium_direct
from film_foundry.core.scanner import capture_optical_density,transmission_illuminant_rgb
from film_foundry.core.color import linear_to_srgb


@dataclass
class CalibrationFrame:
    output: np.ndarray
    raw: np.ndarray
    display: np.ndarray
    reference_source: str
    reference_rgb: tuple | None

    def pick(self,x,y,radius=1):
        height,width=self.raw.shape[:2]
        col=min(width-1,max(0,int(x*width)))
        row=min(height-1,max(0,int(y*height)))
        patch=self.raw[max(0,row-radius):row+radius+1,max(0,col-radius):col+radius+1]
        return tuple(float(v) for v in np.median(patch.reshape(-1,3),axis=0))


class CalibrationPreview:
    def __init__(self,path,mode,config,offset,edge):
        self.path,self.mode,self.config,self.offset,self.edge=path,mode,deepcopy(config),offset,edge
        self.medium=None

    def render(self, remove_base, reference, legacy_rect):
        config=deepcopy(self.config)
        scanner=config.scanner
        scanner.crop_rect=None
        scanner.straighten_degrees=0.
        scanner.remove_base_mask=remove_base
        scanner.base_reference_rgb=reference
        scanner.base_sample_rect=legacy_rect
        if self.mode=='full':
            if self.medium is None:
                self.medium=PreviewSession().form_photo(self.path,config,long_edge=self.edge,path_seed_offset=self.offset)
            scanned=scan_medium_direct(self.medium,config,scanner.interpretation_mode)
            formed=True
        else:
            scanned,kind,_,_=observe_medium_file(self.path,config,retain_stages=True,preview_long_edge=self.edge)
            formed=kind=='density_npz'
        raw=scanned.scanner_raw
        if formed:
            # Sample under the same nominal lamp as the material reference.
            # This also works with spectral capture and nonlinear device curves.
            device=scanned.metadata['runtime_config'].scanner
            nominal=transmission_illuminant_rgb(replace(device,transmission_light_ev=0.))
            if not np.array_equal(nominal,transmission_illuminant_rgb(device)):
                raw=capture_optical_density(scanned.negative_total_density,device,illuminant_rgb=nominal)
        rgb=scanned.metadata.get('base_anchor_rgb')
        return CalibrationFrame(scanned.output_srgb,raw,linear_to_srgb(raw),
                                scanned.metadata.get('base_balance_source','preserved_by_user'),
                                tuple(rgb) if rgb is not None else None)
