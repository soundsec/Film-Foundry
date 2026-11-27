"""Portable date-back display glyphs; no dependency on installed typefaces."""
from functools import lru_cache
from importlib.resources import files

import cv2
import numpy as np
from film_foundry.contracts.exposure_glyphs import ROWS

_SEGMENTS=('abcedf','bc','abged','abgcd','fgbc','afgcd','afgecd','abc','abcdefg','abcdfg')
_LINES={'a':((3,1),(13,1)), 'g':((3,12),(13,12)), 'd':((3,23),(13,23)),
        'f':((1,3),(1,10)), 'b':((15,3),(15,10)), 'e':((1,14),(1,21)), 'c':((15,14),(15,21))}

@lru_cache(maxsize=2)
def _segment_polygons(resource):
    polygons = {}
    for row in files("film_foundry").joinpath("assets", resource).read_text("ascii").splitlines():
        if row.startswith("#"):
            continue
        name, axis, start, end, center, half = row.split()
        a, b, c, t = map(float, (start, end, center, half))
        vertices = np.array([(a,c), (a+t,c-t), (b-t,c-t), (b,c), (b-t,c+t), (a+t,c+t)])
        polygons[name] = vertices if axis == "h" else vertices[:, ::-1]
    return polygons


def _segmented_display(label, height, max_width, resource):
    advance, cell, design_height = 60, 51, 75
    design_width = max(1, (len(label)-1)*advance + cell)
    scale = min(height/design_height, max_width/design_width)
    size = max(4,round(design_width*scale)), max(4,round(design_height*scale))
    # At preview sizes, direct polygon edge rounding can bridge a narrow diagonal joint.
    sampling = 4 if resource == "date_segments_narrow.txt" and scale <= 2 else 1
    canvas = np.zeros((size[1]*sampling, size[0]*sampling), np.uint8)
    scale *= sampling
    polygons = _segment_polygons(resource)
    for index, char in enumerate(label):
        x = index * advance
        if char in "0123456789-":
            segments = "g" if char == "-" else _SEGMENTS[int(char)]
            for segment in segments:
                # Subpixel vertices retain the chamfer at both output and proof sizes.
                vertices = np.rint(((polygons[segment]+[x,0])*scale)*256).astype(np.int32)
                cv2.fillConvexPoly(canvas, vertices, 255, cv2.LINE_AA, shift=8)
        else:
            rows = ROWS.get(ord(char), ROWS[ord("?")])
            for y, row in enumerate(rows):
                for col in range(5):
                    if row & (1 << (4-col)):
                        point = (round((x+6+col*9)*scale*256), round((9+y*9)*scale*256))
                        cv2.circle(canvas, point, max(1, round(2.8*scale*256)), 255, -1, cv2.LINE_AA, shift=8)
    return cv2.resize(canvas, size, interpolation=cv2.INTER_AREA) if sampling > 1 else canvas


def render_display_text(label,font,height,max_width):
    if font in ("seven_segment_narrow", "seven_segment_open"):
        resource = "date_segments_narrow.txt" if font == "seven_segment_narrow" else "date_segments.txt"
        return _segmented_display(label, height, max_width, resource)
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
