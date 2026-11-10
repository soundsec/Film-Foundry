"""Portable exposure stencils for frame labels and film-advance symbols."""
import cv2
import numpy as np
from film_foundry.core.imprint_glyphs import render_display_text


def advance_stencil(number,style,direction,height,max_width):
    h=64;symbol=np.zeros((h,h),np.uint8)
    if style in ('arrow','double_arrow'):
        cv2.line(symbol,(7,32),(54,32),255,5,cv2.LINE_AA)
        cv2.polylines(symbol,[np.array([(35,12),(55,32),(35,52)],np.int32)],False,255,5,cv2.LINE_AA)
        if style=='double_arrow':cv2.polylines(symbol,[np.array([(18,12),(38,32),(18,52)],np.int32)],False,255,5,cv2.LINE_AA)
    elif style=='chevron':
        for x in (5,29):cv2.polylines(symbol,[np.array([(x,10),(x+22,32),(x,54)],np.int32)],False,255,5,cv2.LINE_AA)
    elif style in ('triangle','outline_triangle'):
        points=np.array([(10,8),(55,32),(10,56)],np.int32)
        if style=='triangle':cv2.fillConvexPoly(symbol,points,255,cv2.LINE_AA)
        else:cv2.polylines(symbol,[points],True,255,4,cv2.LINE_AA)
    if direction=='left':symbol=symbol[:,::-1].copy()
    parts=[]
    if number is not None:parts.append(render_display_text(str(number),'dot_matrix',h,640))
    if style!='none':
        if parts:parts.append(np.zeros((h,12),np.uint8))
        parts.append(symbol)
        if direction=='left':parts.reverse()
    if not parts:return np.zeros((1,1),np.float32)
    canvas=np.concatenate(parts,axis=1)
    ratio=min(height/canvas.shape[0],max_width/canvas.shape[1])
    size=(max(1,round(canvas.shape[1]*ratio)),max(1,round(canvas.shape[0]*ratio)))
    return cv2.resize(canvas,size,interpolation=cv2.INTER_AREA).astype(np.float32)/255
