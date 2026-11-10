"""Creative plate screens and transparent-ink overprinting, not press CMYK separation."""
from dataclasses import dataclass,asdict
from functools import lru_cache
import numpy as np
from film_foundry.core.color import srgb_to_linear,linear_to_srgb


@dataclass
class PlateStyle:
    enabled: bool=True
    pattern: str='dots'
    ink: tuple=(.05,.05,.05)
    spacing_mm: float=1.2
    angle: float=15.
    opacity: float=1.
    gain: float=1.
    offset_x_mm: float=0.
    offset_y_mm: float=0.
    invert: bool=False


def separate(image, mode='cmy'):
    image=np.asarray(image,dtype=np.float32)
    if image.ndim!=3 or image.shape[-1]!=3 or not np.isfinite(image).all():raise ValueError('Expected finite sRGB image')
    if mode=='cmy':return [1-image[...,i] for i in range(3)],[(0.,.65,.78),(.85,.06,.34),(.98,.78,.04)],['C','M','Y']
    if mode=='mono':return [1-np.sum(image*np.array([.2126,.7152,.0722]),axis=-1)],[(.06,.045,.035)],['K']
    raise ValueError('Unknown creative separation')


@lru_cache(maxsize=1)
def _dot_distribution():
    y,x=np.mgrid[-128:128,-128:128]/256
    return np.sort((x*x+y*y).ravel()),np.linspace(0,1,256*256)


def _translate(coverage, dx, dy):
    """Bilinear translation with zero coverage outside the original plate."""
    height,width=coverage.shape
    result=np.zeros_like(coverage)
    ix,iy=int(np.floor(dx)),int(np.floor(dy));fx,fy=dx-ix,dy-iy
    for x,y,weight in ((ix,iy,(1-fx)*(1-fy)),(ix+1,iy,fx*(1-fy)),
                       (ix,iy+1,(1-fx)*fy),(ix+1,iy+1,fx*fy)):
        if weight==0 or abs(x)>=width or abs(y)>=height:continue
        left,right=max(0,x),min(width,width+x)
        top,bottom=max(0,y),min(height,height+y)
        result[top:bottom,left:right]+=coverage[top-y:bottom-y,left-x:right-x]*weight
    return result


def screen(coverage, style, dpi=300):
    s=asdict(style);numbers=[s[k] for k in ('spacing_mm','angle','opacity','gain','offset_x_mm','offset_y_mm')]
    if not np.isfinite(numbers).all() or not np.isfinite(dpi) or dpi<=0:raise ValueError('Invalid plate parameters')
    if style.spacing_mm<=0 or not 0<=style.opacity<=1 or not 0<=style.gain<=4:raise ValueError('Invalid plate strength or spacing')
    c=np.asarray(coverage,dtype=np.float32)
    if c.ndim!=2 or not c.size or not np.isfinite(c).all():raise ValueError('Expected finite coverage map')
    c=np.clip((1-c if style.invert else c)*style.gain,0,1)
    period=style.spacing_mm*dpi/25.4
    if style.pattern!='continuous':
        if period<2:raise ValueError('Screen spacing must be at least two pixels at this DPI')
        y,x=np.indices(c.shape,dtype=np.float32);a=np.deg2rad(style.angle)
        u=(x*np.cos(a)+y*np.sin(a))/period;v=(-x*np.sin(a)+y*np.cos(a))/period
        u=np.abs((u+.5)%1-.5);v=np.abs((v+.5)%1-.5)
        if style.pattern=='dots':threshold=np.interp((u*u+v*v).ravel(),*_dot_distribution()).reshape(c.shape)
        elif style.pattern=='lines':threshold=2*u
        elif style.pattern=='grid':threshold=1-(1-2*np.minimum(u,v))**2
        else:raise ValueError('Unknown screen pattern')
        edge=min(.25,.6/period)
        screened=np.clip((c-threshold)/edge+.5,0,1)
        c=np.where(c<=0,0,np.where(c>=1,1,screened)).astype(np.float32)
    if style.offset_x_mm or style.offset_y_mm:
        c=_translate(c,style.offset_x_mm*dpi/25.4,style.offset_y_mm*dpi/25.4)
    return c*style.opacity if style.enabled else np.zeros_like(c)


def compose(plates,styles,*,dpi=300,paper=(1.,.98,.94)):
    if not plates or len(plates)!=len(styles) or len(plates)>32:raise ValueError('Use 1–32 plates with matching styles')
    shape=plates[0].shape
    if len(shape)!=2:raise ValueError('Plates must be two dimensional')
    if any(p.shape!=shape for p in plates):raise ValueError('Plate sizes must match; resample explicitly before import')
    paper=np.asarray(paper,dtype=np.float32)
    if paper.shape!=(3,) or not np.isfinite(paper).all() or np.any((paper<0)|(paper>1)):raise ValueError('Invalid paper colour')
    result=np.broadcast_to(srgb_to_linear(paper),(*shape,3)).copy();masks=[]
    for plate,style in zip(plates,styles):
        ink=np.asarray(style.ink,dtype=np.float32)
        if ink.shape!=(3,) or not np.isfinite(ink).all() or np.any((ink<0)|(ink>1)):raise ValueError('Invalid ink colour')
        mask=screen(plate,style,dpi);masks.append(mask)
        result*=1-mask[...,None]*(1-srgb_to_linear(ink))
    return np.clip(linear_to_srgb(result),0,1),masks
