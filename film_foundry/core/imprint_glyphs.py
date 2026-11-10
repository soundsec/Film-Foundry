"""Portable date-back display glyphs; no dependency on installed typefaces."""
import cv2
import numpy as np
from film_foundry.contracts.exposure_glyphs import ROWS

_SEGMENTS=('abcedf','bc','abged','abgcd','fgbc','afgcd','afgecd','abc','abcdefg','abcdfg')
_LINES={'a':((3,1),(13,1)), 'g':((3,12),(13,12)), 'd':((3,23),(13,23)),
        'f':((1,3),(1,10)), 'b':((15,3),(15,10)), 'e':((1,14),(1,21)), 'c':((15,14),(15,21))}

def render_display_text(label,font,height,max_width):
    canvas=np.zeros((25,max(1,len(label)*20-3)),np.uint8)
    for index,char in enumerate(label):
        tile=canvas[:,index*20:index*20+17]
        if font=='seven_segment' and char.isascii() and char.isdigit():
            for segment in _SEGMENTS[int(char)]:
                a,b=_LINES[segment];cv2.line(tile,a,b,255,2,cv2.LINE_AA)
        else:
            upper=char.upper()
            rows=ROWS.get(ord(char),ROWS.get(ord(upper) if len(upper)==1 else ord('?'),ROWS[ord('?')]))
            for y,row in enumerate(rows):
                for x in range(5):
                    if row & (1<<(4-x)):cv2.circle(tile,(2+x*3,3+y*3),1,255,-1,cv2.LINE_AA)
    ratio=min(height/25,max_width/canvas.shape[1])
    return cv2.resize(canvas,(max(4,round(canvas.shape[1]*ratio)),max(4,round(25*ratio))),interpolation=cv2.INTER_LINEAR)
