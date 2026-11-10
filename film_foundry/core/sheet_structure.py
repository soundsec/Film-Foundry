"""Material structures, not RGB overlays. Fields are frozen at preparation.

Geometry is synthesized on a bounded grid and resampled to the working size.
The different constructions represent poured film, brushed sensitizer, and
sensitizer held in cotton fibers; they do not share one texture template.
"""
import math
import cv2
import numpy as np
from film_foundry.core import material_defects as d
from film_foundry.core import material_structure as ms, scaled_structure as scaled


def _shape(shape):return tuple(max(1,round(n*min(1,768/max(shape)))) for n in shape)


def _fit(array,shape):
    if array.shape!=shape:array=cv2.resize(array,(shape[1],shape[0]),interpolation=cv2.INTER_LINEAR)
    return np.clip(array,0,1).astype(np.float32)


def _noise(shape,rng,rows,columns):
    # Bilinear interpolation keeps fractional material bounds without clipping peaks.
    return cv2.resize(rng.random((rows,columns),dtype=np.float32),(shape[1],shape[0]),interpolation=cv2.INTER_LINEAR)


def _axes(shape,direction):
    y,x=d.coordinates(shape)
    if direction=='down':return y,x
    if direction=='up':return 1-y,x
    if direction=='right':return x,y
    if direction=='left':return 1-x,y
    raise ValueError('Unknown coating direction')


def wet_structure(shape,seed,material):
    if ms.enabled(material):return scaled.wet_structure(shape,seed,material)
    grid=_shape(shape);rng=np.random.default_rng(np.random.SeedSequence([seed,1601]))
    along,across=_axes(grid,material.pour_direction);y,x=d.coordinates(grid)
    flow=np.zeros(grid,np.float32)
    # Swept liquid fronts curve toward the draining edge. No repeated parallel grid.
    for _ in range(5):
        center=rng.uniform(.1,.9)+rng.uniform(-.3,.3)*(across-.5)**2+rng.uniform(-.13,.13)*(across-.5)
        width=rng.uniform(.025,.09)
        flow+=np.exp(-((along-center)/width)**2)*rng.uniform(.15,.35)
    flow=np.clip(flow,0,1)
    distance=np.minimum(np.minimum(x,1-x),np.minimum(y,1-y))
    thin=(1-material.coating_variation*d.clouds(grid,seed,1602))*(1-material.flow_ridges*.8*flow)
    # Drain pooling changes thickness, which also changes subsequent drying/access.
    pooled=material.edge_pooling*np.exp(-(1-along)/.055)*(1-thin)
    cover=(thin+pooled)*(1-material.edge_loss*np.exp(-distance/.022))
    holes=np.zeros(grid,np.float32)
    if material.pinholes:
        for _ in range(32):
            cx,cy=rng.uniform(.03,.97,2);radius=rng.uniform(.0015,.006)
            holes=np.maximum(holes,np.exp(-((x-cx)**2+(y-cy)**2)/(2*radius*radius)))
    cover*=1-material.pinholes*holes
    rough=material.texture*(.15+.85*d.clouds(grid,seed,1603,cells=41))
    return {key:_fit(value,shape) for key,value in {'coating':cover,'flow':flow,'roughness':rough,'surface':1-.12*rough}.items()}


def cyan_structure(shape,seed,material):
    if ms.enabled(material):return scaled.cyan_structure(shape,seed,material)
    grid=_shape(shape);rng=np.random.default_rng(np.random.SeedSequence([seed,2601]))
    along,across=_axes(grid,material.brush_direction);y,x=d.coordinates(grid)
    brush=np.zeros(grid,np.float32)
    # Broad overlapping brush passes, with unequal endpoints and bristle fringes.
    for center in np.linspace(.02,.98,9):
        center=center+rng.uniform(-.045,.045)+rng.uniform(-.055,.055)*(along-.5)+rng.uniform(-.08,.08)*(along-.5)**2
        width=rng.uniform(.055,.115)*(1+.25*material.brush_overlap)
        lane=np.exp(-((across-center)/width)**4)
        rows,columns=(1,29) if material.brush_direction in ('up','down') else (29,1)
        start=rng.uniform(0,.04)+.015*_noise(grid,rng,rows,columns)
        end=rng.uniform(.96,1.)-.015*_noise(grid,rng,rows,columns)
        reach=np.clip((along-start)/.012,0,1)*np.clip((end-along)/.012,0,1)
        brush+=lane*reach*rng.uniform(.45,.7)
    brush=np.clip(brush,0,1)
    fibers=d.clouds(grid,seed,2602,cells=53)
    distance=np.minimum(np.minimum(x,1-x),np.minimum(y,1-y))
    cover=(1-material.coating_variation*(1-brush))*(1-material.edge_loss*np.exp(-distance/.015)*(1-.6*brush))
    cover*=1-material.texture*.12*fibers
    binding=np.clip(material.pigment_binding*(.7+.3*fibers),0,1)
    paper=1-material.texture*.16*fibers
    return {key:_fit(value,shape) for key,value in {'coating':cover,'paper':paper,'binding':binding,'fibers':fibers}.items()}


def platinum_structure(shape,seed,material):
    if ms.enabled(material):return scaled.platinum_structure(shape,seed,material)
    grid=_shape(shape);rng=np.random.default_rng(np.random.SeedSequence([seed,3601]))
    y,x=d.coordinates(grid)
    # Crossing fiber populations; no macroscopic stripe field in a rod coating.
    horizontal=cv2.GaussianBlur(rng.random(grid,dtype=np.float32),(0,0),3.,sigmaY=.45)
    vertical=cv2.GaussianBlur(rng.random(grid,dtype=np.float32),(0,0),.45,sigmaY=3.)
    fiber=np.clip(.5+2.5*(.5*horizontal+.5*vertical-.5),0,1)
    texture=material.texture
    absorb=(1-material.paper_sizing)*(.45+.55*fiber)+material.paper_sizing*.35
    mottling=d.clouds(grid,seed,3603,cells=9)
    if material.coating_method=='brush':mottling=.45*mottling+.55*_noise(grid,rng,27,5)
    distance=np.minimum(np.minimum(x,1-x),np.minimum(y,1-y))
    cover=(1-material.coating_variation*(.15+.5*mottling+.35*absorb))*(1-material.edge_loss*np.exp(-distance/.01))
    cover*=1-texture*.12*fiber
    access=np.clip(1-.55*absorb,0,1)
    paper=1-texture*.13*fiber
    return {key:_fit(value,shape) for key,value in {'coating':cover,'paper':paper,'absorption':absorb,'clear_access':access,'fibers':fiber}.items()}


def paper_reflectance(paper,absorption,scatter):
    """Bounded two-flux diffuse remission; K/S is a creative relative measure."""
    k=np.maximum(absorption,0)/np.maximum(scatter,1e-4)
    return paper[...,None]/(1+k+np.sqrt(k*(k+2)))


def view_gloss(view,roughness):
    vectors=[]
    for value in (view.light_direction,view.view_direction):
        vector=np.asarray(value,np.float32)
        if vector.shape!=(3,) or not np.isfinite(vector).all() or np.linalg.norm(vector)<1e-6:raise ValueError('Invalid viewing direction')
        vectors.append(vector/np.linalg.norm(vector))
    light,eye=vectors;half=light+eye
    if np.linalg.norm(half)<1e-6:return np.zeros_like(roughness)
    cosine=max(0,float(half[2]/np.linalg.norm(half)))
    return np.power(cosine,8+120*(1-roughness))*max(0,float(light[2]))*max(0,float(eye[2]))
