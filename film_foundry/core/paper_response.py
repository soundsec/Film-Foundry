"""One paper response model with CPU row scheduling and optional fused GPU work."""
import math
import numpy as np
from . import sheet_common as s,desktop_compute
from .material_parallel import run_rows,thread_count,PARALLEL_MIN_PIXELS
from .execution_topology import reference_execution_enabled

_qualified=set()
_KINDS={'graded':0,'variable_contrast':1,'direct_positive':2,'color':3}


def cpu_response(image,material):
    m=material;rgb=image*m.sensitivity_remaining
    if m.kind=='color':dose=rgb
    elif m.kind=='variable_contrast':dose=rgb[...,2,None]+rgb[...,1,None]*np.array([.025,.2,1.],np.float32)
    else:dose=np.repeat((rgb[...,2]+.08*rgb[...,1])[...,None],3,axis=2)
    activation=s.response(dose,m.half_exposure,m.response_contrast)
    if m.kind=='direct_positive':activation=1-activation
    return activation+(1-activation)*m.fog_level


def response(image,material):
    if reference_execution_enabled():return cpu_response(image,material)
    if image.shape[0]*image.shape[1]>=262144:
        m=material
        parameters=np.array([_KINDS[m.kind],m.sensitivity_remaining,math.log(m.half_exposure),m.response_contrast,m.fog_level],np.float32)
        accelerated=desktop_compute.try_paper_response(image,parameters)
        if accelerated is not None:
            key=(desktop_compute.selected_backend(),m.kind)
            # Qualify each backend/material mechanism once; do not compare or
            # hash the full frame on every processing step.
            if key not in _qualified:
                ys=np.linspace(0,image.shape[0]-1,4,dtype=int)
                xs=np.linspace(0,image.shape[1]-1,8,dtype=int)
                expected=cpu_response(image[ys[:,None],xs],m)
                if np.allclose(accelerated[ys[:,None],xs],expected,rtol=2e-6,atol=2e-7):_qualified.add(key)
                else:
                    desktop_compute.reject('Paper response exceeded CPU tolerance')
                    accelerated=None
            if accelerated is not None:return accelerated
    if image.shape[0]*image.shape[1]<PARALLEL_MIN_PIXELS or thread_count()==1:return cpu_response(image,material)
    result=np.empty(image.shape,np.float32)
    def calculate(start,stop):result[start:stop]=cpu_response(image[start:stop],material)
    run_rows(image.shape[0],image.shape[1],calculate)
    return result
