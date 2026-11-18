"""Linear relay operations. No scanner inversion, normalisation or display LUT."""
import numpy as np
from film_foundry.contracts.optical_cascade import OpticalRelay


def transmission_radiance(optical_density, illuminant_rgb):
    density = np.asarray(optical_density, dtype=np.float32)
    if density.ndim != 3 or density.shape[-1] != 3 or min(density.shape[:2]) <= 0:
        raise ValueError('Relay requires an authoritative HxWx3 optical master')
    if not np.isfinite(np.min(density)) or not np.isfinite(np.max(density)) or np.min(density) < 0:
        raise ValueError('Relay optical density must be finite and nonnegative')
    result = np.power(np.float32(10.), -density)
    result *= np.asarray(illuminant_rgb, np.float32)
    return result


def apply_local_exposures(dose, regions):
    """Apply elliptical printing exposures on the receiver, in bounded row blocks."""
    from film_foundry.contracts.scan_framing import pixel_bounds
    height,width = dose.shape[:2]
    for region in regions:
        if not region.ev:
            continue
        l,t,r,b = region.rect
        angle=np.deg2rad(region.angle)
        c,s=np.cos(angle),np.sin(angle)
        cx,cy=(l+r)*width/2,(t+b)*height/2
        rx,ry=(r-l)*width/2,(b-t)*height/2
        dx,dy=abs(c)*rx+abs(s)*ry,abs(s)*rx+abs(c)*ry
        x0,x1=max(0,int(np.floor(cx-dx))),min(width,int(np.ceil(cx+dx)))
        y0,y1=max(0,int(np.floor(cy-dy))),min(height,int(np.ceil(cy+dy)))
        x=np.arange(x0,x1,dtype=np.float64)+.5-cx
        for start in range(y0,y1,128):
            end=min(start+128,y1)
            y=(np.arange(start,end,dtype=np.float64)+.5-cy)[:,None]
            u=(c*x+s*y)/rx;v=(-s*x+c*y)/ry
            distance=np.sqrt(u*u+v*v) if region.shape=='ellipse' else np.maximum(abs(u),abs(v))
            if region.feather:
                weight=np.clip((1-distance)/region.feather,0,1)
                weight=weight*weight*(3-2*weight)
            else:
                weight=distance<=1
            if region.shape=='gradient':weight=weight*np.clip((1+u)/2,0,1)
            gain = np.exp2(region.ev*weight).astype(np.float32)
            dose[start:end,x0:x1] *= gain[:,:,None]


def relay_dose(radiance, settings: OpticalRelay):
    """Resize flux, then apply filter/time. Resampling does not change field extent.

    This ideal relay has no calibrated aperture, distance or magnification loss.
    Its exposure time scales relative dose; reciprocity failure is not modeled.
    """
    image = np.asarray(radiance, dtype=np.float32)
    if image.ndim != 3 or image.shape[-1] != 3 or min(image.shape[:2]) <= 0:
        raise ValueError('Relay requires HxWx3 linear radiance')
    if not np.isfinite(np.min(image)) or not np.isfinite(np.max(image)) or np.min(image) < 0:
        raise ValueError('Relay radiance must be finite and nonnegative')
    if settings.output_shape and settings.output_shape != image.shape[:2]:
        import cv2
        h, w = settings.output_shape
        mode = cv2.INTER_AREA if h <= image.shape[0] and w <= image.shape[1] else cv2.INTER_LINEAR
        image = cv2.resize(image, (w, h), interpolation=mode)
    if settings.flip_horizontal:
        image = image[:, ::-1]
    gain = np.power(10., -np.asarray(settings.filter_density_rgb, np.float64)) * settings.exposure_seconds * 2 ** settings.exposure_ev
    result = np.array(image, dtype=np.float32, copy=True)
    result *= gain.astype(np.float32)
    if settings.local_exposures:
        apply_local_exposures(result,settings.local_exposures)
    if not np.isfinite(np.max(result)):
        raise ValueError('Optical relay dose overflow')
    return result
