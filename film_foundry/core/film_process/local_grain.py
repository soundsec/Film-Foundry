"""Derived local response at the formation/finalization boundary.

The consumer reads retained moments before compaction, never modifies pools.
It is also the extension seam for a future diagnostic frontend. These fields
are not extra chemical inventories or writable observation parameters.
"""
from dataclasses import dataclass
import cv2
import numpy as np
from film_foundry.core.film_process.formation_history import NAMES,POOLS


@dataclass(frozen=True)
class LocalGrainResponse:
    product: str
    grain_factor: np.ndarray
    grain_radius_factor: np.ndarray
    grain_clump_factor: np.ndarray
    retained_mass: np.ndarray | None = None

    def at_shape(self,shape):
        if self.grain_factor.shape[:2]==tuple(shape):return self
        def resize(field):
            channels=field.shape[-1] if field.ndim==3 else None
            result=cv2.resize(field,(shape[1],shape[0]),interpolation=cv2.INTER_AREA)
            return result.reshape(*shape,channels) if channels is not None else result
        mass=None if self.retained_mass is None else resize(self.retained_mass)
        values=[]
        for key in NAMES[:3]:
            field=getattr(self,key)
            if mass is None:field=resize(field)
            else:
                moment=resize(field*self.retained_mass)
                field=np.full_like(mass,0. if key=='grain_factor' else 1.)
                np.divide(moment,mass,out=field,where=mass>1e-12)
            values.append(field)
        return LocalGrainResponse(self.product,*values,mass)

    def descriptor(self):
        return {'model':'retained_local_grain_response_v1','product':self.product,
                'shape':list(self.grain_factor.shape),'fields':list(NAMES[:3]),
                'editable':False,'chemical_inventory':False,'resampling':'retained_mass_weighted_area'}


class LocalGrainCollector:
    """One full-frame result assembled from disjoint reaction row blocks."""
    def __init__(self,shape,products):
        self.shape=tuple(shape[:2]);self.products=tuple(dict.fromkeys(products));self.fields={};self.masses={};self.next_row=0

    def capture(self,state,*,row_offset=0):
        stop=row_offset+state.halide.shape[0]
        if state.halide.shape[1]!=self.shape[1] or row_offset!=self.next_row or not 0<=row_offset<stop<=self.shape[0]:
            raise ValueError('Local formation response lies outside its frame')
        for product in self.products:
            pool=getattr(state,POOLS[product]);scalar=product=='silver'
            mass=None if pool is None else (np.sum(pool,axis=-1,dtype=np.float32) if scalar else pool)
            channels=() if scalar else (state.halide.shape[-1],)
            if product not in self.fields:
                self.fields[product]=[np.empty((*self.shape,*channels),np.float32) for _ in NAMES[:3]]
                self.masses[product]=np.empty((*self.shape,*channels),np.float32)
            self.masses[product][row_offset:stop]=0. if mass is None else mass
            for key,target in zip(NAMES[:3],self.fields[product]):
                output=target[row_offset:stop];output.fill(0. if key=='grain_factor' else 1.)
                if mass is None:continue
                # An untouched incoming product has the same neutral response
                # assigned by ensure_history; an empty product has zero gain.
                moment=(state.formation_history[f'{product}_{key}'] if state.formation_history is not None else pool)
                if scalar:moment=np.sum(moment,axis=-1,dtype=np.float32)
                np.divide(moment,mass,out=output,where=mass>1e-12)
        self.next_row=stop

    def append_rows(self,other,*,row_offset):
        """Join a completed private tile in frame order, without recalculation."""
        stop=row_offset+other.shape[0]
        if (other.products!=self.products or other.shape[1]!=self.shape[1]
            or other.next_row!=other.shape[0] or row_offset!=self.next_row
            or not 0<=row_offset<stop<=self.shape[0]):
            raise ValueError('Local formation tile is incomplete or out of order')
        for product in self.products:
            if product not in self.fields:
                self.fields[product]=[np.empty((*self.shape,*value.shape[2:]),np.float32) for value in other.fields[product]]
                self.masses[product]=np.empty((*self.shape,*other.masses[product].shape[2:]),np.float32)
            for target,source in zip(self.fields[product],other.fields[product]):target[row_offset:stop]=source
            self.masses[product][row_offset:stop]=other.masses[product]
        self.next_row=stop

    def response(self,product):
        if self.next_row!=self.shape[0]:raise ValueError('Local grain response is not complete')
        arrays=self.fields[product]
        for value in arrays:value.setflags(write=False)
        self.masses[product].setflags(write=False)
        return LocalGrainResponse(product,*arrays,self.masses[product])


def blend_radius_fields(radius,sample,*,bounds=None):
    """Interpolate three log-spaced filters of the same random realization.

    Bounds belong to the full frame, including when sampling a silver tile.
    No new random draws and no bath-specific texture layers are introduced.
    """
    low,high=bounds if bounds is not None else (float(np.min(radius)),float(np.max(radius)))
    if high-low<=1e-6:return sample((low+high)*.5)
    anchors=np.geomspace(max(low,1e-8),max(high,low+1e-8),3)
    position=np.clip(np.log(np.maximum(radius,1e-8)/anchors[0])/np.log(anchors[1]/anchors[0]),0.,2.)
    result=None
    for index,anchor in enumerate(anchors):
        weight=np.maximum(1.-np.abs(position-index),0.).astype(np.float32)
        field=sample(float(anchor));field*=weight
        if result is None:result=field
        else:result+=field
    return result


@dataclass(frozen=True)
class RadiusBlendPlan:
    """Reusable interpolation geometry; contains no noise or filtered image."""
    anchors: tuple[float, ...]
    weights: tuple[np.ndarray, ...] = ()

    def sample(self, sampler):
        if not self.weights:return sampler(self.anchors[0])
        result=None
        for anchor,weight in zip(self.anchors,self.weights):
            field=sampler(anchor);field*=weight
            if result is None:result=field
            else:result+=field
        return result


def prepare_radius_blend(radius, *, bounds=None):
    """Prepare the exact existing three-anchor arithmetic once per response."""
    low,high=bounds if bounds is not None else (float(np.min(radius)),float(np.max(radius)))
    if high-low<=1e-6:return RadiusBlendPlan(((low+high)*.5,))
    anchors=np.geomspace(max(low,1e-8),max(high,low+1e-8),3)
    position=np.clip(np.log(np.maximum(radius,1e-8)/anchors[0])/np.log(anchors[1]/anchors[0]),0.,2.)
    weights=tuple(np.maximum(1.-np.abs(position-index),0.).astype(np.float32) for index in range(3))
    for weight in weights:weight.setflags(write=False)
    return RadiusBlendPlan(tuple(float(a) for a in anchors),weights)
