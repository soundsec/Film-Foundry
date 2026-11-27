"""Versioned material-space sampling, independent of working pixel dimensions."""
from dataclasses import dataclass
import math
import cv2
import numpy as np
from film_foundry.core.material_geometry import MaterialGeometry
from film_foundry.core.execution_topology import reference_execution_enabled

LEGACY='relative_v1'
CURRENT='millimetres_v1'
GRID_EDGE=768
PARAMETERS=('structure_model','material_long_edge_mm','texture_scale_mm','process_scale_mm')


@dataclass(frozen=True,kw_only=True)
class StructureMaterial:
    structure_model: str=CURRENT
    material_long_edge_mm: float=100.
    texture_scale_mm: float=.7
    process_scale_mm: float=8.

    def __post_init__(self):
        if self.structure_model not in (LEGACY,CURRENT):raise ValueError('Unknown material structure model')
        for key,low,high in (('material_long_edge_mm',10,1000),('texture_scale_mm',.05,100),('process_scale_mm',.1,200)):
            value=getattr(self,key)
            if isinstance(value,bool) or not math.isfinite(value) or not low<=value<=high:raise ValueError(f'{key} must be in [{low},{high}] mm')


def from_record(definition,record):
    """Missing generator identity in an archived material means the old model."""
    values=dict(record)
    if issubclass(definition,StructureMaterial):values.setdefault('structure_model',LEGACY)
    for key, value in getattr(definition, 'ARCHIVED_DEFAULTS', {}).items():
        values.setdefault(key, value)
    return definition(**values)


def enabled(material):return material.structure_model==CURRENT


class Grid:
    def __init__(self,shape,seed,material):
        self.output_shape=shape;self.seed=seed;self.material=material
        self.shape=tuple(max(1,round(n/max(shape)*GRID_EDGE)) for n in shape)
        extent=MaterialGeometry(material.material_long_edge_mm,100.,shape)
        self.height_mm=extent.frame_long_edge_mm*shape[0]/max(shape)
        self.width_mm=extent.frame_long_edge_mm*shape[1]/max(shape)
        h,w=self.shape
        self.y=((np.arange(h,dtype=np.float32)+.5)/h*self.height_mm)[:,None]
        self.x=((np.arange(w,dtype=np.float32)+.5)/w*self.width_mm)[None,:]
        self.pitch=max(self.height_mm/h,self.width_mm/w)

    def rng(self,stream):return np.random.default_rng(np.random.SeedSequence([self.seed,stream]))

    def noise(self,stream,scale_mm,aspect=1.,angle_degrees=0.):
        result=np.zeros(self.shape,np.float32)
        x,y=self.x,self.y;width,height=self.width_mm,self.height_mm
        if angle_degrees:
            angle=math.radians(angle_degrees);c=math.cos(angle);s=math.sin(angle)
            x=self.x*c+self.y*s-min(0,width*c)-min(0,height*s)
            y=-self.x*s+self.y*c-min(0,-width*s)-min(0,height*c)
            width,height=abs(width*c)+abs(height*s),abs(width*s)+abs(height*c)
        def weights(coord,spacing):
            grid=coord/spacing
            index=grid.astype(np.int32)
            fraction=grid-index
            return index,fraction*fraction*(3-2*fraction)

        reference=reference_execution_enabled()
        for octave,weight in enumerate((.6,.28,.12)):
            requested=scale_mm/(2**octave)
            sy=max(requested,2*self.pitch);sx=max(requested*aspect,2*self.pitch)
            attenuation=math.exp(-(.5*self.pitch/min(requested,requested*aspect))**2)
            values=self.rng(stream+octave).random((math.ceil(height/sy)+2,math.ceil(width/sx)+2),dtype=np.float32)
            if reference:
                ix,fx=weights(x,sx);iy,fy=weights(y,sy)
                value=(values[iy,ix]*(1-fx)+values[iy,ix+1]*fx)*(1-fy)+(values[iy+1,ix]*(1-fx)+values[iy+1,ix+1]*fx)*fy
                result+=weight*(.5+attenuation*(value-.5))
                continue
            if not angle_degrees:
                # The horizontal interpolation is identical for every output
                # row in the same lattice cell. Evaluate that axis only once.
                ix,fx=weights(x.ravel(),sx)
                horizontal=values[:,ix]*(1-fx)+values[:,ix+1]*fx
                iy,fy=weights(y,sy)
            for start in range(0,self.shape[0],32):
                rows=slice(start,start+32)
                if angle_degrees:
                    ix,fx=weights(x[rows],sx);iy,fy=weights(y[rows],sy)
                    # Flatten lattice indices so each corner is a single gather.
                    top=iy*values.shape[1]+ix
                    bottom=top+values.shape[1]
                    lattice=values.ravel()
                    upper=lattice[top]*(1-fx)+lattice[top+1]*fx
                    lower=lattice[bottom]*(1-fx)+lattice[bottom+1]*fx
                    vertical=fy
                else:
                    upper=horizontal[iy[rows,0]]
                    lower=horizontal[iy[rows,0]+1]
                    vertical=fy[rows]
                value=upper*(1-vertical)+lower*vertical
                result[rows]+=weight*(.5+attenuation*(value-.5))
        return result

    def axes(self,direction):
        if direction=='down':return self.y,self.x,self.height_mm,self.width_mm
        if direction=='up':return self.height_mm-self.y,self.x,self.height_mm,self.width_mm
        if direction=='right':return self.x,self.y,self.width_mm,self.height_mm
        if direction=='left':return self.width_mm-self.x,self.y,self.width_mm,self.height_mm
        raise ValueError('Unknown material direction')

    def edge_distance(self):return np.minimum(np.minimum(self.x,self.width_mm-self.x),np.minimum(self.y,self.height_mm-self.y))

    def fit(self,value):
        value=np.broadcast_to(np.asarray(value,np.float32),self.shape)
        if self.shape==self.output_shape:return value
        h,w=self.output_shape
        interpolation=cv2.INTER_AREA if h<=self.shape[0] and w<=self.shape[1] else cv2.INTER_LINEAR
        return cv2.resize(value,(w,h),interpolation=interpolation)

    def fields(self,**values):return {key:self.fit(value) for key,value in values.items()}


def noise(shape,seed,material,stream,scale_mm,aspect=1.):
    grid=Grid(shape,seed,material)
    return grid.fit(grid.noise(stream,scale_mm,aspect))
