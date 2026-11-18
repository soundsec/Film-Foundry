"""Scan regions use normalized pixel-edge coordinates on the capture canvas."""
import math


def rectangle(value):
    if value is None:
        return None
    if not isinstance(value,(list,tuple)) or len(value)!=4 or not all(
            type(v) in (int,float) and math.isfinite(v) for v in value):
        raise ValueError('A region needs four finite coordinates (left, top, right, bottom)')
    left,top,right,bottom=map(float,value)
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError('Region must lie inside the frame and have positive area')
    return left,top,right,bottom


def base_reference_rgb(value):
    """A saved white point in the linear scanner signal, before inversion."""
    if value is None:
        return None
    if (not isinstance(value,(list,tuple)) or len(value)!=3 or
            not all(type(v) in (int,float) and math.isfinite(v) and 0 < v <= 1 for v in value)):
        raise ValueError('Base reference needs three positive linear RGB values up to 1')
    return tuple(float(v) for v in value)


def straighten_angle(value):
    if type(value) not in (int,float) or not math.isfinite(value) or abs(value)>45:
        raise ValueError('Straightening angle must be within ±45 degrees')
    return float(value)


def pixel_bounds(shape, rect):
    h,w = shape[:2]
    if rect is None:
        return 0,0,w,h
    left,top,right,bottom = rectangle(rect)
    return math.floor(left*w), math.floor(top*h), math.ceil(right*w), math.ceil(bottom*h)
