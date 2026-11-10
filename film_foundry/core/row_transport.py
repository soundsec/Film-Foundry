"""Reusable conservative row-translation geometry for related material fields."""
import numpy as np


class RowShiftPlan:
    """Prepare cell-overlap indices once; leave stationary rows unchanged.

    The caller owns this short-lived plan. It contains geometry, never material
    values, so the same plan can transport pools and their formation moments.
    Arithmetic and zero-filled outside boundaries match emulsion_shear.shift_rows.
    """
    def __init__(self,shape,pixels):
        self.shape=tuple(shape[:2]);h,w=self.shape
        pixels=np.asarray(pixels,np.float64)
        if pixels.shape!=(h,) or not np.isfinite(pixels).all():
            raise ValueError('One finite shift is required per row')
        self.blocks=[];cols=np.arange(w,dtype=np.int64)[None,:]
        block=max(1,262144//w)
        for start in range(0,h,block):
            rows=np.flatnonzero(pixels[start:start+block])+start
            if not len(rows):continue
            offset=-pixels[rows];lower=np.floor(offset).astype(np.int64)
            fraction=(offset-lower).astype(np.float32)
            left=cols+lower[:,None];right=left+1
            wa=(1-fraction[:,None])*((left>=0)&(left<w))
            wb=fraction[:,None]*((right>=0)&(right<w))
            self.blocks.append((rows,np.clip(left,0,w-1),np.clip(right,0,w-1),wa,wb))

    def __call__(self,array):
        source=np.asarray(array,np.float32)
        if source.shape[:2]!=self.shape:raise ValueError('Row transport geometry does not match its field')
        out=source.copy();tail=(None,)*(source.ndim-2)
        for rows,left,right,wa,wb in self.blocks:
            a=source[rows[:,None],left];b=source[rows[:,None],right]
            out[rows]=a*wa[(...,)+tail]+b*wb[(...,)+tail]
        return out
