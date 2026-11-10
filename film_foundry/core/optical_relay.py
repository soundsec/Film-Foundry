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
    if not np.isfinite(np.max(result)):
        raise ValueError('Optical relay dose overflow')
    return result
