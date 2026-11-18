"""Final-image framing and read-only samples in the linear capture domain."""
import cv2
import numpy as np
from film_foundry.contracts.scan_framing import pixel_bounds


def region_view(image, rect):
    x,y,r,b = pixel_bounds(image.shape, rect)
    return image[y:b,x:r]


def base_anchor(raw, rect):
    samples = region_view(raw, rect)
    return np.median(samples.reshape(-1,3),axis=0).astype(np.float32)


def rotation_matrix(shape, angle):
    h,w = shape[:2]
    return cv2.getRotationMatrix2D(((w-1)*.5,(h-1)*.5),angle,1.)


def frame_scan(image, scanner):
    angle, rect = scanner.straighten_degrees, scanner.crop_rect
    if not angle and rect is None:
        return image
    from film_foundry.core.formation_observer import observer
    sink=observer.get()
    if sink is not None:
        sink.scan_geometry=(image.shape[:2],angle,rect)
    x,y,r,b = pixel_bounds(image.shape,rect)
    if not angle:
        return image[y:b,x:r].copy()
    matrix=rotation_matrix(image.shape,angle)
    matrix[:,2] -= (x,y)
    return cv2.warpAffine(image,matrix,(r-x,b-y),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)


def capture_position(geometry,x,y):
    shape,angle,rect = geometry
    left,top,_,_=pixel_bounds(shape,rect)
    x,y=x+left,y+top
    if angle:
        matrix=cv2.invertAffineTransform(rotation_matrix(shape,angle))
        x,y=matrix @ (x,y,1.)
    return round(x),round(y),shape[1],shape[0]
